import hashlib
import json
import os
import re
import secrets
import time
from pathlib import Path
from typing import Optional

import dotenv
import requests
from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

try:
    import jwt
except Exception:  # pragma: no cover - helpful error when dependency missing
    jwt = None

from backend.ai.agent import CHAT_MODES, chat_mode_options, normalize_chat_mode, run_agent, run_agent_stream
from backend.ai.retrieval import index_note_for_user, retrieve_user_notes
from backend.auth.store import (
    active_refresh_sessions,
    cleanup_expired_refresh_tokens,
    create_general_notification_event,
    create_note,
    create_user,
    delete_user_by_id,
    delete_all_notes,
    delete_note,
    get_refresh_token,
    get_admin_user,
    get_user_by_id,
    init_auth_db,
    active_notification_settings,
    list_admin_actions,
    create_notification_event,
    list_notification_events,
    list_notification_settings,
    list_recent_user_request_messages,
    list_admin_request_logs,
    list_admin_users,
    list_notes,
    log_admin_action,
    log_user_request,
    mark_notifications_read,
    reset_user_password,
    revoke_refresh_token,
    revoke_refresh_tokens_for_user,
    store_refresh_token,
    suspend_user,
    touch_refresh_token,
    unread_notification_count,
    unsuspend_user,
    update_notification_setting,
    upsert_env_user,
    verify_user_password,
)
from backend.chat.routes import router as chat_router
from backend.chat.store import init_chat_db
from backend.forum.routes import router as forum_router
from backend.forum.store import init_forum_db
from backend.portfolio.store import (
    clear_portfolio_snapshots,
    create_portfolio_snapshot,
    delete_portfolio_holding,
    init_portfolio_db,
    latest_portfolio_snapshot,
    list_portfolio_holdings,
    portfolio_payload,
    upsert_portfolio_holdings,
    update_holding_valuations,
)
from backend.recommendations import personalized_recommendations
from backend.social.routes import router as social_router
from backend.social.store import init_social_db
from backend.users.routes import router as users_router

dotenv.load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
UPLOAD_ROOT = Path(os.getenv("UPLOAD_ROOT") or (PROJECT_ROOT / "uploads")).resolve()
UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
FRONTEND_STATIC_ROOT = (PROJECT_ROOT / "frontend" / "static").resolve()
FRONTEND_STATIC_ROOT.mkdir(parents=True, exist_ok=True)
CHAT_UPLOAD_MAX_FILES = int(os.getenv("CHAT_UPLOAD_MAX_FILES", "5"))
CHAT_UPLOAD_MAX_BYTES = int(os.getenv("CHAT_UPLOAD_MAX_BYTES", str(300 * 1024)))
CHAT_UPLOAD_TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".csv",
    ".json",
    ".yaml",
    ".yml",
    ".log",
    ".py",
    ".js",
    ".ts",
    ".html",
    ".css",
}

app = FastAPI(title="Crypto Agent")
templates = Jinja2Templates(directory=str(PROJECT_ROOT / "frontend" / "templates"))
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_ROOT)), name="uploads")
app.mount("/static", StaticFiles(directory=str(FRONTEND_STATIC_ROOT)), name="static")
app.include_router(users_router)
app.include_router(social_router)
app.include_router(chat_router)
app.include_router(forum_router)

APP_SECRET_KEY = os.getenv("FLASK_SECRET_KEY") or os.getenv("SECRET_KEY") or secrets.token_hex(32)
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY") or APP_SECRET_KEY
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_EXP_SECONDS = int(os.getenv("JWT_EXP_SECONDS", "900"))
ACCESS_TOKEN_SECRET = os.getenv("ACCESS_TOKEN_SECRET") or JWT_SECRET_KEY
REFRESH_TOKEN_SECRET = (
    os.getenv("REFRESH_TOKEN_SECRET")
    or os.getenv("JWT_REFRESH_SECRET")
    or f"{JWT_SECRET_KEY}:refresh"
)
ACCESS_TOKEN_EXP_SECONDS = int(os.getenv("ACCESS_TOKEN_EXP_SECONDS", str(JWT_EXP_SECONDS)))
REFRESH_TOKEN_EXP_SECONDS = int(os.getenv("REFRESH_TOKEN_EXP_SECONDS", str(60 * 60 * 24 * 7)))
AUTH_ALLOW_REGISTRATION = os.getenv("AUTH_ALLOW_REGISTRATION", "true").lower() in {"1", "true", "yes", "on"}
AUTH_COOKIE_SECURE = os.getenv("AUTH_COOKIE_SECURE", "false").lower() in {"1", "true", "yes", "on"}
REFRESH_COOKIE_NAME = "refresh_token"
REFRESH_COOKIE_PATH = "/api/auth"
SELF_AUTH_USERNAME = os.getenv("SELF_AUTH_USERNAME")
SELF_AUTH_PASSWORD = os.getenv("SELF_AUTH_PASSWORD")
SELF_AUTH_EMAIL = os.getenv("SELF_AUTH_EMAIL")
SELF_AUTH_IS_ADMIN = os.getenv("SELF_AUTH_IS_ADMIN", "true").lower() in {"1", "true", "yes", "on"}
DEV_LIVE_RELOAD = os.getenv("DEV_LIVE_RELOAD", "true").lower() in {"1", "true", "yes", "on"}
DEV_RELOAD_EXTENSIONS = {".py", ".html", ".css", ".js"}
DEV_RELOAD_PATHS = [
    PROJECT_ROOT / "app.py",
    PROJECT_ROOT / "backend",
    PROJECT_ROOT / "frontend" / "templates",
]
AUTH_ACTIVE_SESSION_SECONDS = int(os.getenv("AUTH_ACTIVE_SESSION_SECONDS", str(30 * 60)))
dev_reload_cache = {"checked_at": 0.0, "version": "0"}
market_price_cache = {"checked_at": 0.0, "prices": []}
MARKET_PRICE_CACHE_TTL_SECONDS = 25
PORTFOLIO_AUTO_SNAPSHOT_SECONDS = int(os.getenv("PORTFOLIO_AUTO_SNAPSHOT_SECONDS", str(60 * 60)))
AGENT_GREETING_SYMBOL_RE = re.compile(
    r"\b(BTC|ETH|SOL|BNB|XRP|ADA|AVAX|DOGE|LINK|DOT|LTC)\b",
    re.IGNORECASE,
)

init_auth_db()
init_social_db()
init_chat_db()
init_forum_db()
init_portfolio_db()
cleanup_expired_refresh_tokens()
if SELF_AUTH_USERNAME and SELF_AUTH_PASSWORD:
    upsert_env_user(SELF_AUTH_USERNAME, SELF_AUTH_PASSWORD, SELF_AUTH_EMAIL, is_admin=SELF_AUTH_IS_ADMIN)

conversation_histories = {}


def source_reload_version() -> str:
    now = time.monotonic()
    if now - dev_reload_cache["checked_at"] < 0.75:
        return dev_reload_cache["version"]

    newest = 0
    for source_path in DEV_RELOAD_PATHS:
        if source_path.is_file():
            newest = max(newest, source_path.stat().st_mtime_ns)
            continue
        if not source_path.exists():
            continue
        for path in source_path.rglob("*"):
            if "__pycache__" in path.parts or not path.is_file():
                continue
            if path.suffix.lower() in DEV_RELOAD_EXTENSIONS:
                newest = max(newest, path.stat().st_mtime_ns)

    dev_reload_cache["checked_at"] = now
    dev_reload_cache["version"] = str(newest)
    return dev_reload_cache["version"]


def registration_is_enabled() -> bool:
    return AUTH_ALLOW_REGISTRATION


def json_error(message: str, status: int) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def is_text_upload(filename: str, content_type: str) -> bool:
    suffix = Path(filename or "").suffix.lower()
    normalized_type = (content_type or "").split(";", 1)[0].strip().lower()
    return normalized_type.startswith("text/") or normalized_type in {
        "application/json",
        "application/x-ndjson",
        "application/yaml",
        "application/xml",
    } or suffix in CHAT_UPLOAD_TEXT_EXTENSIONS


async def extract_uploaded_file_context(file_obj) -> dict:
    filename = Path(getattr(file_obj, "filename", "") or "uploaded-file").name
    content_type = getattr(file_obj, "content_type", "") or ""
    try:
        raw = await file_obj.read(CHAT_UPLOAD_MAX_BYTES + 1)
    finally:
        close = getattr(file_obj, "close", None)
        if close:
            await close()

    size = len(raw)
    item = {
        "name": filename,
        "contentType": content_type,
        "sizeBytes": size,
        "text": "",
        "error": "",
    }
    if size > CHAT_UPLOAD_MAX_BYTES:
        item["error"] = f"File is larger than {CHAT_UPLOAD_MAX_BYTES} bytes"
        return item
    if not is_text_upload(filename, content_type):
        item["error"] = "Only text-like files are readable by the agent right now"
        return item
    try:
        item["text"] = raw.decode("utf-8")
    except UnicodeDecodeError:
        item["text"] = raw.decode("utf-8", errors="replace")
    return item


async def chat_request_payload(request: Request) -> tuple[str, str, list[dict]]:
    content_type = request.headers.get("content-type", "").lower()
    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        user_input = (form.get("message") or "").strip()
        raw_mode = (form.get("mode") or "instant").strip().lower()
        files = [
            item
            for item in form.getlist("attachments")
            if getattr(item, "filename", "")
        ][:CHAT_UPLOAD_MAX_FILES]
        uploaded_files = [await extract_uploaded_file_context(file_obj) for file_obj in files]
        if not user_input and uploaded_files:
            user_input = "Analyze the uploaded file(s)."
        return user_input, raw_mode, uploaded_files

    data = await request.json()
    return (data.get("message", "").strip(), (data.get("mode") or "instant").strip().lower(), [])


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def encode_jwt(payload: dict, secret: str) -> str:
    token = jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)
    if isinstance(token, bytes):
        token = token.decode("utf-8")
    return token


def sign_access_token(user: dict) -> str:
    if jwt is None:
        raise RuntimeError("pyjwt is not installed. Install with `pip install pyjwt`.")
    now = int(time.time())
    return encode_jwt(
        {
            "type": "access",
            "sub": str(user["id"]),
            "username": user.get("username") or user.get("name"),
            "email": user.get("email", ""),
            "iat": now,
            "exp": now + ACCESS_TOKEN_EXP_SECONDS,
        },
        ACCESS_TOKEN_SECRET,
    )


def sign_refresh_token(user: dict, jti: str) -> str:
    if jwt is None:
        raise RuntimeError("pyjwt is not installed. Install with `pip install pyjwt`.")
    now = int(time.time())
    return encode_jwt(
        {
            "type": "refresh",
            "sub": str(user["id"]),
            "jti": jti,
            "iat": now,
            "exp": now + REFRESH_TOKEN_EXP_SECONDS,
        },
        REFRESH_TOKEN_SECRET,
    )


def decode_access_token(token: str) -> dict:
    if jwt is None:
        raise RuntimeError("pyjwt is not installed. Install with `pip install pyjwt`.")
    payload = jwt.decode(token, ACCESS_TOKEN_SECRET, algorithms=[JWT_ALGORITHM])
    if payload.get("type") != "access":
        raise jwt.InvalidTokenError("Invalid token type")
    user = get_user_by_id(payload.get("sub"))
    if not user:
        raise jwt.InvalidTokenError("User no longer exists")
    if user.get("disabledAt"):
        raise jwt.InvalidTokenError("Account disabled")
    return user


def decode_refresh_token(token: str) -> dict:
    if jwt is None:
        raise RuntimeError("pyjwt is not installed. Install with `pip install pyjwt`.")
    payload = jwt.decode(token, REFRESH_TOKEN_SECRET, algorithms=[JWT_ALGORITHM])
    if payload.get("type") != "refresh" or not payload.get("jti"):
        raise jwt.InvalidTokenError("Invalid refresh token")
    return payload


def set_refresh_cookie(response: JSONResponse, refresh_token: str) -> None:
    response.set_cookie(
        REFRESH_COOKIE_NAME,
        refresh_token,
        max_age=REFRESH_TOKEN_EXP_SECONDS,
        httponly=True,
        secure=AUTH_COOKIE_SECURE,
        samesite="strict",
        path=REFRESH_COOKIE_PATH,
    )


def clear_refresh_cookie(response: JSONResponse) -> None:
    response.delete_cookie(
        REFRESH_COOKIE_NAME,
        path=REFRESH_COOKIE_PATH,
        samesite="strict",
        secure=AUTH_COOKIE_SECURE,
    )


def request_ip(request: Request) -> str:
    forwarded_for = request.headers.get("X-Forwarded-For", "")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip()
    return request.client.host if request.client else ""


def create_refresh_token_record(user: dict, request: Request) -> tuple[str, str]:
    jti = secrets.token_hex(16)
    refresh_token = sign_refresh_token(user, jti)
    store_refresh_token(
        user_id=user["id"],
        token_hash=hash_token(refresh_token),
        jti=jti,
        expires_at=int(time.time()) + REFRESH_TOKEN_EXP_SECONDS,
        ip=request_ip(request),
        user_agent=request.headers.get("User-Agent", ""),
    )
    return refresh_token, jti


def current_refresh_jti(request: Request, expected_user_id: str | int | None = None) -> str:
    refresh_token = request.cookies.get(REFRESH_COOKIE_NAME, "")
    if not refresh_token:
        return ""
    try:
        payload = decode_refresh_token(refresh_token)
    except jwt.InvalidTokenError:
        return ""
    if expected_user_id is not None and str(payload.get("sub")) != str(expected_user_id):
        return ""

    token_hash = hash_token(refresh_token)
    record = get_refresh_token(token_hash, payload["jti"])
    if (
        not record
        or record["revoked_at"]
        or int(record["expires_at"]) < int(time.time())
    ):
        return ""
    touch_refresh_token(token_hash, payload["jti"])
    return str(payload["jti"])


def auth_security_payload(user_id: str | int, current_jti: str = "") -> dict:
    other_sessions = active_refresh_sessions(
        user_id,
        exclude_jti=current_jti,
        active_window_seconds=AUTH_ACTIVE_SESSION_SECONDS,
        limit=5,
    )
    return {
        "hasOtherActiveSessions": bool(other_sessions),
        "otherActiveSessionCount": len(other_sessions),
        "latestOtherSession": other_sessions[0] if other_sessions else None,
        "activeWindowSeconds": AUTH_ACTIVE_SESSION_SECONDS,
    }


def login_client_label(request: Request) -> str:
    user_agent = (request.headers.get("User-Agent") or "unknown browser").split(" ", 1)[0]
    ip = request_ip(request)
    return f"{user_agent} at {ip}" if ip else user_agent


def maybe_create_concurrent_login_notice(user: dict, request: Request, current_jti: str) -> None:
    security = auth_security_payload(user["id"], current_jti)
    if not security["hasOtherActiveSessions"]:
        return
    count = security["otherActiveSessionCount"]
    suffix = "" if count == 1 else "s"
    create_general_notification_event(
        user_id=user["id"],
        event_type="account_login",
        title="New login detected",
        message=(
            f"Your account signed in from {login_client_label(request)} while "
            f"{count} other session{suffix} were active."
        ),
        link_url="/?tab=notifications",
        symbol="SEC",
        coin_id="security",
    )


def issue_auth_response(
    user: dict,
    request: Request,
    status_code: int = 200,
    notify_concurrent_login: bool = False,
) -> JSONResponse:
    access_token = sign_access_token(user)
    refresh_token, current_jti = create_refresh_token_record(user, request)
    if notify_concurrent_login:
        maybe_create_concurrent_login_notice(user, request, current_jti)
    response = JSONResponse(
        {
            "ok": True,
            "accessToken": access_token,
            "expiresIn": ACCESS_TOKEN_EXP_SECONDS,
            "user": user,
            "authSecurity": auth_security_payload(user["id"], current_jti),
        },
        status_code=status_code,
    )
    set_refresh_cookie(response, refresh_token)
    return response


def bearer_token(request: Request) -> str:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return ""
    return auth_header.split(" ", 1)[1].strip()


def authenticate_request(request: Request) -> tuple[Optional[dict], Optional[str]]:
    token = bearer_token(request)
    if token:
        try:
            user = decode_access_token(token)
            request.state.current_user = user
            return user, None
        except jwt.ExpiredSignatureError:
            return None, "Access token expired"
        except jwt.InvalidTokenError as exc:
            message = str(exc)
            return None, "Account disabled" if message == "Account disabled" else "Invalid token"
    return None, None


def current_user(request: Request) -> Optional[dict]:
    if hasattr(request.state, "current_user"):
        return request.state.current_user
    user, _ = authenticate_request(request)
    return user


def require_user(request: Request) -> dict | JSONResponse:
    user, auth_error = authenticate_request(request)
    if not user:
        return json_error(auth_error or "Login required", 403 if auth_error == "Account disabled" else 401)
    return user


def require_admin(user=Depends(require_user)) -> dict | JSONResponse:
    if isinstance(user, JSONResponse):
        return user
    if not user.get("isAdmin"):
        return json_error("Admin access required", 403)
    return user


def conversation_key_for_user(user: dict) -> str:
    return f"{user.get('provider', 'local')}:{user['id']}"


def get_conversation_history(user: dict) -> list:
    key = conversation_key_for_user(user)
    return conversation_histories.setdefault(key, [])


def load_history() -> list:
    history_path = PROJECT_ROOT / "crypto_history.json"
    if not history_path.exists():
        return []
    try:
        with history_path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def price_sidebar_data() -> list[dict]:
    history = load_history()
    latest = {}
    for item in history:
        coin = item.get("coin", "").upper()
        if not coin:
            continue
        current = latest.get(coin)
        if current is None or item.get("time", "") > current["time"]:
            latest[coin] = {
                "symbol": coin,
                "price": item.get("usd", 0) or item.get("USD", 0),
                "time": item.get("time", ""),
            }
    return list(latest.values())


def live_price_sidebar_data() -> list[dict]:
    prices = fetch_market_prices(list(MARKET_SYMBOLS.keys()))
    rows = []
    for coin_id, symbol in MARKET_SYMBOLS.items():
        coin = prices.get(coin_id)
        if not coin:
            continue
        rows.append(
            {
                "coinId": coin_id,
                "symbol": symbol,
                "price": coin.get("usd"),
                "usd": coin.get("usd"),
                "changePercent": coin.get("usd_24h_change"),
                "usd_24h_change": coin.get("usd_24h_change"),
                "source": coin.get("source") or "market data",
            }
        )
    return rows


def compact_agent_context_text(value: str, max_length: int = 120) -> str:
    text = " ".join((value or "").split())
    if len(text) > max_length:
        return text[: max_length - 3].rstrip() + "..."
    return text


def extract_symbols_from_texts(texts: list[str], limit: int = 4) -> list[str]:
    symbols = []
    for text in texts:
        for match in AGENT_GREETING_SYMBOL_RE.findall(text or ""):
            symbol = match.upper()
            if symbol not in symbols:
                symbols.append(symbol)
            if len(symbols) >= limit:
                return symbols
    return symbols


def stable_choice(options: list[str], *parts: object) -> str:
    if not options:
        return ""
    seed = "|".join(str(part or "") for part in parts)
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return options[int(digest[:8], 16) % len(options)]


def greeting_style_for_user(user: dict, ai_profile: dict) -> str:
    explicit_style = (ai_profile.get("communicationStyle") or "").strip().lower()
    if explicit_style in {"casual", "direct", "executive", "technical", "teacher"}:
        return explicit_style
    return stable_choice(
        ["direct", "technical", "coach", "brief"],
        user.get("id"),
        user.get("username"),
        user.get("displayName") or user.get("name"),
    )


def greeting_name_for_user(user: dict) -> str:
    display_name = user.get("displayName") or user.get("name") or user.get("username") or ""
    first_name = (display_name or "").strip().split(" ", 1)[0]
    return f"{first_name}, " if first_name else ""


def symbol_list_text(symbols: list[str]) -> str:
    if not symbols:
        return ""
    if len(symbols) == 1:
        return symbols[0]
    if len(symbols) == 2:
        return f"{symbols[0]} and {symbols[1]}"
    return f"{', '.join(symbols[:-1])}, and {symbols[-1]}"


def greeting_action_phrase(ai_profile: dict, style: str) -> str:
    risk_profile = (ai_profile.get("riskProfile") or "").strip().lower()
    depth = (ai_profile.get("preferredDepth") or "").strip().lower()

    if risk_profile == "conservative":
        return "downside risk, invalidation, and the safest next step"
    if risk_profile == "aggressive":
        return "momentum, upside setup, and the level that kills the trade"
    if style == "executive":
        return "what changed, the risk, and the next decision"
    if style == "teacher":
        return "the setup, the risk, and why each level matters"
    if style == "technical" or depth == "detailed":
        return "structure, key levels, invalidation, and scenarios"
    if depth == "short":
        return "the plan, risk, and next level"
    return "the plan, risk, and key levels"


def recent_symbol_greeting(name: str, symbols: list[str], ai_profile: dict, style: str, user: dict) -> str:
    symbol_text = symbol_list_text(symbols)
    action = greeting_action_phrase(ai_profile, style)
    variants = {
        "executive": [
            f"{name}quick brief: your recent flow is concentrated in {symbol_text}. I can turn it into {action}.",
            f"{name}{symbol_text} is the current thread. I can compress it into a decision brief: {action}.",
        ],
        "technical": [
            f"{name}your recent context clusters around {symbol_text}. I can map {action}.",
            f"{name}I see repeated {symbol_text} signals in your history. Want the technical read across {action}?",
        ],
        "casual": [
            f"{name}I keep seeing {symbol_text} in your recent chats. Want a clean game plan with {action}?",
            f"{name}{symbol_text} is back on your radar. I can give you the no-fluff version: {action}.",
        ],
        "teacher": [
            f"{name}your latest questions point at {symbol_text}. I can walk through {action}.",
            f"{name}we can use {symbol_text} as today's case study and break down {action}.",
        ],
        "direct": [
            f"{name}{symbol_text} is the active thread. I can update {action}.",
            f"{name}your recent market focus is {symbol_text}. Pick one and I'll tighten {action}.",
        ],
        "coach": [
            f"{name}{symbol_text} keeps showing up in your work. I can help turn it into {action}.",
            f"{name}I see a pattern around {symbol_text}. Let's turn that into {action}.",
        ],
        "brief": [
            f"{name}current context: {symbol_text}. I can give you {action}.",
            f"{name}your latest market thread is {symbol_text}. I can summarize {action}.",
        ],
    }
    return stable_choice(
        variants.get(style, variants["direct"]),
        user.get("id"),
        user.get("username"),
        symbol_text,
        style,
        ai_profile.get("riskProfile"),
    )


def recent_topic_greeting(name: str, topic: str, ai_profile: dict, style: str) -> str:
    action = greeting_action_phrase(ai_profile, style)
    variants = {
        "executive": f"{name}I can continue from your last thread: \"{topic}\". Want the decision brief on {action}?",
        "technical": f"{name}your last thread was \"{topic}\". I can extend it into {action}.",
        "casual": f"{name}we can pick up where you left off: \"{topic}\". Want the clean version with {action}?",
        "teacher": f"{name}let's continue from \"{topic}\" and break down {action}.",
        "direct": f"{name}last topic: \"{topic}\". I can update {action}.",
        "coach": f"{name}your last thread was \"{topic}\". I can help shape it into {action}.",
        "brief": f"{name}continuing from \"{topic}\". I can cover {action}.",
    }
    return variants.get(style, variants["direct"])


def profile_greeting(name: str, favorite_assets: str, goals: str, ai_profile: dict, style: str) -> str:
    action = greeting_action_phrase(ai_profile, style)
    if favorite_assets:
        variants = {
            "executive": f"{name}watchlist loaded: {favorite_assets}. I can brief you on {action}.",
            "technical": f"{name}I have {favorite_assets} on deck. Want structure, levels, and invalidation first?",
            "casual": f"{name}your watchlist is {favorite_assets}. Want a quick read without the noise?",
            "teacher": f"{name}we can use {favorite_assets} to walk through {action}.",
            "direct": f"{name}{favorite_assets} is in focus. I can give you {action}.",
            "coach": f"{name}your watchlist is set to {favorite_assets}. I can help turn it into {action}.",
            "brief": f"{name}watchlist: {favorite_assets}. I can cover {action}.",
        }
        return variants.get(style, variants["direct"])

    variants = {
        "executive": f"{name}your stated goal is \"{goals}\". I can keep answers framed around decisions and risk.",
        "technical": f"{name}I'll frame the work around your goal: \"{goals}\". Send a ticker and I'll build the structure.",
        "casual": f"{name}I've got your goal: \"{goals}\". Drop a ticker and I'll keep it practical.",
        "teacher": f"{name}your goal is \"{goals}\". I can explain each setup step by step from there.",
        "direct": f"{name}goal loaded: \"{goals}\". Send a ticker and I'll frame the plan around it.",
        "coach": f"{name}I'll keep your goal in view: \"{goals}\". What market do you want to work on first?",
        "brief": f"{name}goal: \"{goals}\". Send a ticker for a focused brief.",
    }
    return variants.get(style, variants["direct"])


def default_greeting(name: str, ai_profile: dict, style: str) -> str:
    action = greeting_action_phrase(ai_profile, style)
    variants = {
        "executive": f"{name}I can run this like a market desk: what changed, what matters, and what to do next.",
        "technical": f"{name}send a ticker and I'll map {action}.",
        "casual": f"{name}send me a coin and I'll give you the clean read: no noise, just what matters.",
        "teacher": f"{name}give me a ticker and I'll break down the setup, risk, and next levels.",
        "direct": f"{name}send a ticker and I'll give you {action}.",
        "coach": f"{name}bring me a coin or market idea and I'll help shape it into {action}.",
        "brief": f"{name}ready for a focused market brief. Send a ticker or ask for today's setup.",
    }
    return variants.get(style, variants["direct"])


def agent_greeting_for_user(user: dict) -> dict:
    ai_profile = user.get("aiProfile") or {}
    recent_messages = list_recent_user_request_messages(user["id"], limit=4)
    notes = list_notes(user["id"])[:4]
    note_texts = [note.get("content", "") for note in notes]
    favorite_assets = compact_agent_context_text(ai_profile.get("favoriteAssets", ""), 80)
    goals = compact_agent_context_text(ai_profile.get("goals", ""), 120)
    symbols = extract_symbols_from_texts([favorite_assets, *recent_messages, *note_texts])
    style = greeting_style_for_user(user, ai_profile)
    greeting_name = greeting_name_for_user(user)

    if recent_messages and symbols:
        message = recent_symbol_greeting(greeting_name, symbols, ai_profile, style, user)
        source = "recent-chat"
    elif recent_messages:
        topic = compact_agent_context_text(recent_messages[0], 90)
        message = recent_topic_greeting(greeting_name, topic, ai_profile, style)
        source = "recent-chat"
    elif favorite_assets:
        message = profile_greeting(greeting_name, favorite_assets, goals, ai_profile, style)
        source = "ai-profile"
    elif goals:
        message = profile_greeting(greeting_name, favorite_assets, goals, ai_profile, style)
        source = "ai-profile"
    elif note_texts and symbols:
        message = recent_symbol_greeting(greeting_name, symbols, ai_profile, style, user)
        source = "notes"
    else:
        message = default_greeting(greeting_name, ai_profile, style)
        source = "default"

    return {"message": message, "source": source, "symbols": symbols, "style": style}

    display_name = user.get("displayName") or user.get("name") or user.get("username") or ""
    first_name = (display_name or "").strip().split(" ", 1)[0]
    greeting_name = f"{first_name}, " if first_name else ""

    if recent_messages and symbols:
        message = (
            f"{greeting_name}I picked up your recent {', '.join(symbols)} context. "
            "Want me to update the plan, risk, or key levels from here?"
        )
        source = "recent-chat"
    elif recent_messages:
        topic = compact_agent_context_text(recent_messages[0], 90)
        message = (
            f"{greeting_name}I can continue from your last market thread: \"{topic}\". "
            "Do you want an update, a risk check, or a cleaner trade plan?"
        )
        source = "recent-chat"
    elif favorite_assets:
        message = (
            f"{greeting_name}I have your watchlist in focus: {favorite_assets}. "
            "Want a quick market check or a risk-first plan?"
        )
        source = "ai-profile"
    elif goals:
        message = (
            f"{greeting_name}I’m tuned to your goal: {goals}. "
            "Give me a ticker and I’ll frame the answer around that."
        )
        source = "ai-profile"
    elif note_texts and symbols:
        message = (
            f"{greeting_name}your notes mention {', '.join(symbols)}. "
            "I can turn that into a current watchlist, risk map, or trade plan."
        )
        source = "notes"
    else:
        message = (
            f"{greeting_name}I’m ready to personalize the market work from your notes, "
            "watchlist, and chat history. Send a ticker or ask for today’s brief."
        )
        source = "default"

    return {"message": message, "source": source, "symbols": symbols}


MARKET_SYMBOLS = {
    "bitcoin": "BTC",
    "ethereum": "ETH",
    "solana": "SOL",
    "ripple": "XRP",
    "binancecoin": "BNB",
    "cardano": "ADA",
    "avalanche-2": "AVAX",
    "dogecoin": "DOGE",
    "chainlink": "LINK",
    "polkadot": "DOT",
    "litecoin": "LTC",
}

PORTFOLIO_SYMBOL_TO_COIN_ID = {symbol: coin_id for coin_id, symbol in MARKET_SYMBOLS.items()}
PORTFOLIO_ASSET_ALIASES = {
    **{symbol: symbol for symbol in PORTFOLIO_SYMBOL_TO_COIN_ID},
    "BITCOIN": "BTC",
    "ETHEREUM": "ETH",
    "SOLANA": "SOL",
    "BINANCE": "BNB",
    "BINANCECOIN": "BNB",
    "CARDANO": "ADA",
    "AVALANCHE": "AVAX",
    "DOGECOIN": "DOGE",
    "CHAINLINK": "LINK",
    "POLKADOT": "DOT",
    "LITECOIN": "LTC",
    "RIPPLE": "XRP",
}
PORTFOLIO_STOP_WORDS = {
    "AND",
    "AT",
    "AVG",
    "AVERAGE",
    "BOUGHT",
    "BUY",
    "COST",
    "ENTRY",
    "FOR",
    "HOLD",
    "HOLDING",
    "I",
    "OWN",
    "PRICE",
    "WITH",
}
PORTFOLIO_NUMBER_PATTERN = r"\d[\d,]*(?:\.\d+)?"
PORTFOLIO_ASSET_PATTERN = r"[A-Za-z][A-Za-z0-9.-]{1,20}"
PORTFOLIO_QTY_ASSET_PRICE_RE = re.compile(
    rf"(?P<quantity>{PORTFOLIO_NUMBER_PATTERN})\s+"
    rf"(?P<asset>{PORTFOLIO_ASSET_PATTERN})\b"
    rf"(?:\s+(?:at|@|for|cost(?:\s+basis)?|avg(?:erage)?(?:\s+cost)?|entry(?:\s+price)?|price))?"
    rf"\s+\$?(?P<price>{PORTFOLIO_NUMBER_PATTERN})",
    re.IGNORECASE,
)
PORTFOLIO_ASSET_QTY_PRICE_RE = re.compile(
    rf"\b(?P<asset>{PORTFOLIO_ASSET_PATTERN})\s+"
    rf"(?P<quantity>{PORTFOLIO_NUMBER_PATTERN})"
    rf"\s+(?:at|@|for|cost(?:\s+basis)?|avg(?:erage)?(?:\s+cost)?|entry(?:\s+price)?|price)"
    rf"\s+\$?(?P<price>{PORTFOLIO_NUMBER_PATTERN})",
    re.IGNORECASE,
)
PORTFOLIO_QTY_ASSET_RE = re.compile(
    rf"(?P<quantity>{PORTFOLIO_NUMBER_PATTERN})\s+(?P<asset>{PORTFOLIO_ASSET_PATTERN})\b",
    re.IGNORECASE,
)

BINANCE_USDT_PAIRS = {coin_id: f"{symbol}USDT" for coin_id, symbol in MARKET_SYMBOLS.items()}
COINBASE_USD_PRODUCTS = {
    coin_id: f"{symbol}-USD"
    for coin_id, symbol in MARKET_SYMBOLS.items()
    if symbol not in {"BNB"}
}
MARKET_REQUEST_HEADERS = {"User-Agent": "Crypto-Agent/1.0"}


def compact_coin_ids(coin_ids: list[str]) -> list[str]:
    return list(dict.fromkeys(coin_id for coin_id in coin_ids if coin_id))


def parse_portfolio_number(value: str) -> float:
    return float((value or "").replace(",", ""))


def normalize_portfolio_asset(asset: str) -> tuple[str, str] | tuple[None, None]:
    key = re.sub(r"[^A-Za-z0-9.-]", "", asset or "").upper()
    symbol = PORTFOLIO_ASSET_ALIASES.get(key)
    if not symbol:
        return None, None
    return symbol, PORTFOLIO_SYMBOL_TO_COIN_ID[symbol]


def portfolio_draft_row(asset: str, quantity: float | None, average_cost: float | None) -> dict:
    raw_symbol = re.sub(r"[^A-Za-z0-9.-]", "", asset or "").upper()
    symbol, coin_id = normalize_portfolio_asset(asset)
    errors = []
    if not symbol:
        errors.append("Unsupported crypto symbol")
    if quantity is None or quantity <= 0:
        errors.append("Quantity must be positive")
    if average_cost is None or average_cost <= 0:
        errors.append("Average cost is required")

    return {
        "symbol": symbol or raw_symbol,
        "coinId": coin_id or "",
        "quantity": quantity,
        "averageCostUsd": average_cost,
        "valid": not errors,
        "error": "; ".join(errors),
    }


def parse_portfolio_prompt_text(text: str) -> dict:
    body = (text or "").strip()
    draft = []
    errors = []
    consumed_spans: list[tuple[int, int]] = []

    def overlaps(start: int, end: int) -> bool:
        return any(start < span_end and end > span_start for span_start, span_end in consumed_spans)

    def add_match(match, has_price: bool = True) -> None:
        start, end = match.span()
        if overlaps(start, end):
            return
        asset = match.group("asset")
        if asset.upper() in PORTFOLIO_STOP_WORDS:
            return
        try:
            quantity = parse_portfolio_number(match.group("quantity"))
            average_cost = parse_portfolio_number(match.group("price")) if has_price else None
        except (TypeError, ValueError):
            return
        draft.append(portfolio_draft_row(asset, quantity, average_cost))
        consumed_spans.append((start, end))

    for pattern in (PORTFOLIO_ASSET_QTY_PRICE_RE, PORTFOLIO_QTY_ASSET_PRICE_RE):
        for match in pattern.finditer(body):
            add_match(match, has_price=True)

    for match in PORTFOLIO_QTY_ASSET_RE.finditer(body):
        add_match(match, has_price=False)

    if not draft:
        errors.append("No crypto holdings found. Try: I bought 0.5 BTC at 65000 and 3 ETH at 3200.")

    return {"draft": draft, "errors": errors}


def validate_portfolio_holding_payload(rows: list[dict]) -> list[dict]:
    holdings = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Portfolio holdings must be objects")
        symbol = (row.get("symbol") or "").strip().upper()
        coin_id = PORTFOLIO_SYMBOL_TO_COIN_ID.get(symbol)
        if not coin_id:
            raise ValueError(f"Unsupported crypto symbol: {symbol or 'unknown'}")
        try:
            quantity = float(row.get("quantity"))
            average_cost = float(row.get("averageCostUsd"))
        except (TypeError, ValueError):
            raise ValueError(f"{symbol} quantity and average cost must be numbers")
        if quantity <= 0:
            raise ValueError(f"{symbol} quantity must be positive")
        if average_cost <= 0:
            raise ValueError(f"{symbol} average cost must be positive")
        holdings.append(
            {
                "symbol": symbol,
                "coin_id": coin_id,
                "quantity": quantity,
                "average_cost_usd": average_cost,
            }
        )
    if not holdings:
        raise ValueError("At least one valid holding is required")
    return holdings


def normalized_market_price(price: str | float | int, change: str | float | int, source: str) -> dict:
    return {
        "usd": float(price),
        "usd_24h_change": float(change),
        "source": source,
    }


def fetch_binance_market_prices(coin_ids: list[str], *, base_url: str, source: str) -> dict:
    pairs = {
        coin_id: BINANCE_USDT_PAIRS[coin_id]
        for coin_id in coin_ids
        if coin_id in BINANCE_USDT_PAIRS
    }
    if not pairs:
        return {}

    response = requests.get(
        f"{base_url.rstrip('/')}/api/v3/ticker/24hr",
        params={"symbols": json.dumps(list(pairs.values()), separators=(",", ":"))},
        headers=MARKET_REQUEST_HEADERS,
        timeout=6,
    )
    response.raise_for_status()
    payload = response.json()
    rows = payload if isinstance(payload, list) else [payload]
    by_symbol = {row.get("symbol"): row for row in rows if isinstance(row, dict)}

    prices = {}
    for coin_id, symbol in pairs.items():
        row = by_symbol.get(symbol)
        if not row or row.get("lastPrice") is None or row.get("priceChangePercent") is None:
            continue
        prices[coin_id] = normalized_market_price(row["lastPrice"], row["priceChangePercent"], source)
    return prices


def fetch_coinbase_market_prices(coin_ids: list[str]) -> dict:
    prices = {}
    for coin_id in coin_ids:
        product = COINBASE_USD_PRODUCTS.get(coin_id)
        if not product:
            continue
        response = requests.get(
            f"https://api.exchange.coinbase.com/products/{product}/stats",
            headers=MARKET_REQUEST_HEADERS,
            timeout=6,
        )
        response.raise_for_status()
        row = response.json()
        last = float(row["last"])
        open_price = float(row["open"])
        if open_price == 0:
            continue
        change = ((last - open_price) / open_price) * 100
        prices[coin_id] = normalized_market_price(last, change, "coinbase")
    return prices


def fetch_coingecko_market_prices(coin_ids: list[str]) -> dict:
    if not coin_ids:
        return {}
    response = requests.get(
        "https://api.coingecko.com/api/v3/simple/price",
        params={
            "ids": ",".join(coin_ids),
            "vs_currencies": "usd",
            "include_24hr_change": "true",
        },
        headers=MARKET_REQUEST_HEADERS,
        timeout=8,
    )
    response.raise_for_status()
    prices = {}
    for coin_id, row in response.json().items():
        if row.get("usd") is None or row.get("usd_24h_change") is None:
            continue
        prices[coin_id] = normalized_market_price(row["usd"], row["usd_24h_change"], "coingecko")
    return prices


def fetch_market_prices(coin_ids: list[str]) -> dict:
    wanted = compact_coin_ids(coin_ids)
    if not wanted:
        return {}

    prices = {}
    errors = []
    providers = [
        lambda ids: fetch_binance_market_prices(ids, base_url="https://api.binance.com", source="binance"),
        lambda ids: fetch_binance_market_prices(ids, base_url="https://api.binance.us", source="binance-us"),
        fetch_coinbase_market_prices,
        fetch_coingecko_market_prices,
    ]

    for provider in providers:
        missing = [coin_id for coin_id in wanted if coin_id not in prices]
        if not missing:
            break
        try:
            prices.update(provider(missing))
        except (KeyError, TypeError, ValueError, requests.RequestException) as exc:
            errors.append(str(exc))

    if not prices and errors:
        raise requests.RequestException("Market price providers failed: " + " | ".join(errors))
    return prices


def notification_payload(user_id: int | str, created: Optional[list[dict]] = None) -> dict:
    return {
        "created": created or [],
        "events": list_notification_events(user_id),
        "unreadCount": unread_notification_count(user_id),
        "settings": list_notification_settings(user_id),
    }


def check_price_notifications_for_user(user: dict) -> list[dict]:
    settings = active_notification_settings(user["id"])
    price_data = fetch_market_prices([setting["coin_id"] for setting in settings])
    now = int(time.time())
    created = []

    for setting in settings:
        coin = price_data.get(setting["coin_id"])
        if not coin:
            continue

        price = coin.get("usd")
        change = coin.get("usd_24h_change")
        if price is None or change is None:
            continue

        threshold = float(setting["threshold_percent"])
        if abs(float(change)) < threshold:
            continue

        last_notified_at = setting["last_notified_at"]
        cooldown_seconds = int(setting["cooldown_minutes"]) * 60
        if last_notified_at and now - int(last_notified_at) < cooldown_seconds:
            continue

        direction = "rose" if change > 0 else "dropped"
        title = f"{setting['symbol']} Price Alert"
        source = coin.get("source") or "market data"
        message = (
            f"{setting['symbol']} {direction} {abs(float(change)):.2f}% in 24h. "
            f"Current price: ${float(price):,.2f}. Source: {source}."
        )
        created.append(
            create_notification_event(
                user_id=user["id"],
                setting_id=setting["id"],
                coin_id=setting["coin_id"],
                symbol=setting["symbol"],
                title=title,
                message=message,
                price_usd=float(price),
                change_percent=float(change),
            )
        )

    return created


def maybe_create_auto_portfolio_snapshot(user_id: int | str) -> Optional[str]:
    holdings = list_portfolio_holdings(user_id)
    if not holdings:
        return None

    latest = latest_portfolio_snapshot(user_id)
    now = int(time.time())
    if latest and latest.get("createdAt") and now - int(latest["createdAt"]) < PORTFOLIO_AUTO_SNAPSHOT_SECONDS:
        return None

    coin_ids = [holding["coinId"] for holding in holdings]
    try:
        price_data = fetch_market_prices(coin_ids)
        price_by_symbol = {
            MARKET_SYMBOLS[coin_id]: float(row["usd"])
            for coin_id, row in price_data.items()
            if coin_id in MARKET_SYMBOLS and row.get("usd") is not None
        }
        if not price_by_symbol:
            return "Could not refresh automatic portfolio snapshot prices"
        update_holding_valuations(user_id, price_by_symbol, priced_at=now)
        create_portfolio_snapshot(user_id)
    except (KeyError, TypeError, ValueError, requests.RequestException) as exc:
        return f"Could not create automatic portfolio snapshot: {exc}"
    return None


@app.get("/api/price-data")
def price_data():
    now = time.monotonic()
    cached_prices = market_price_cache["prices"]
    if cached_prices and now - float(market_price_cache["checked_at"] or 0) < MARKET_PRICE_CACHE_TTL_SECONDS:
        return {"prices": cached_prices, "live": True, "cached": True}

    try:
        prices = live_price_sidebar_data()
        if prices:
            market_price_cache["checked_at"] = now
            market_price_cache["prices"] = prices
            return {"prices": prices, "live": True, "cached": False}
    except requests.RequestException:
        pass

    return {"prices": price_sidebar_data(), "live": False, "cached": False}


@app.get("/api/dev/reload-version")
def dev_reload_version():
    return {"enabled": DEV_LIVE_RELOAD, "version": source_reload_version() if DEV_LIVE_RELOAD else "0"}


@app.get("/api/auth/me")
def auth_me(request: Request):
    user, auth_error = authenticate_request(request)
    current_jti = current_refresh_jti(request, user["id"]) if user else ""
    return {
        "authenticated": bool(user),
        "user": user,
        "authError": auth_error,
        "authSecurity": auth_security_payload(user["id"], current_jti) if user else None,
        "registrationEnabled": registration_is_enabled(),
        "accessTokenTtlSeconds": ACCESS_TOKEN_EXP_SECONDS,
        "loginUrl": "/login",
        "registerUrl": "/register",
        "refreshUrl": "/api/auth/refresh",
        "logoutUrl": "/api/auth/logout",
    }


@app.get("/api/users/me")
def user_me(user=Depends(require_user)):
    if isinstance(user, JSONResponse):
        return user
    return {"user": user}


@app.post("/api/auth/register")
async def auth_register(request: Request):
    if not registration_is_enabled():
        return json_error("Registration is disabled", 403)

    data = await request.json()
    username = (data.get("username") or "").strip()
    email = (data.get("email") or "").strip()
    password = data.get("password") or ""

    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,64}", username):
        return json_error("Username must be 3-64 letters, numbers, dots, dashes, or underscores", 400)
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return json_error("Invalid email address", 400)
    if len(password) < 8:
        return json_error("Password must be at least 8 characters", 400)

    try:
        user = create_user(username, email, password)
    except ValueError as exc:
        return json_error(str(exc), 409)
    return issue_auth_response(user, request, status_code=201)


@app.post("/api/auth/login")
async def auth_login_api(request: Request):
    data = await request.json()
    login = (data.get("login") or data.get("username") or data.get("email") or "").strip()
    password = data.get("password") or ""
    if not login or not password:
        return json_error("Missing login or password", 400)

    user = verify_user_password(login, password)
    if not user:
        return json_error("Invalid credentials", 401)
    if user.get("disabledAt"):
        return json_error("Account disabled", 403)
    return issue_auth_response(user, request, notify_concurrent_login=True)


@app.post("/api/auth/refresh")
def auth_refresh(request: Request):
    refresh_token = request.cookies.get(REFRESH_COOKIE_NAME, "")
    if not refresh_token:
        return json_error("Missing refresh token", 401)

    token_hash = hash_token(refresh_token)
    try:
        payload = decode_refresh_token(refresh_token)
    except jwt.ExpiredSignatureError:
        revoke_refresh_token(token_hash)
        response = json_error("Refresh token expired", 401)
        clear_refresh_cookie(response)
        return response
    except jwt.InvalidTokenError:
        response = json_error("Invalid refresh token", 401)
        clear_refresh_cookie(response)
        return response

    record = get_refresh_token(token_hash, payload["jti"])
    if not record:
        response = json_error("Refresh token not recognized", 401)
        clear_refresh_cookie(response)
        return response
    if record["revoked_at"]:
        response = json_error("Refresh token revoked", 401)
        clear_refresh_cookie(response)
        return response
    if int(record["expires_at"]) < int(time.time()):
        revoke_refresh_token(token_hash)
        response = json_error("Refresh token expired", 401)
        clear_refresh_cookie(response)
        return response

    user = get_user_by_id(record["user_id"])
    if not user:
        response = json_error("User no longer exists", 401)
        clear_refresh_cookie(response)
        return response
    if user.get("disabledAt"):
        revoke_refresh_token(token_hash)
        response = json_error("Account disabled", 403)
        clear_refresh_cookie(response)
        return response
    access_token = sign_access_token(user)
    new_refresh_token, new_jti = create_refresh_token_record(user, request)
    revoke_refresh_token(token_hash, replaced_by=new_jti)

    response = JSONResponse(
        {
            "ok": True,
            "accessToken": access_token,
            "expiresIn": ACCESS_TOKEN_EXP_SECONDS,
            "user": user,
            "authSecurity": auth_security_payload(user["id"], new_jti),
        }
    )
    set_refresh_cookie(response, new_refresh_token)
    return response


@app.post("/api/auth/logout")
def auth_logout(request: Request):
    user = current_user(request)
    refresh_token = request.cookies.get(REFRESH_COOKIE_NAME, "")
    if refresh_token:
        revoke_refresh_token(hash_token(refresh_token))
    if user:
        conversation_histories.pop(conversation_key_for_user(user), None)

    response = JSONResponse({"ok": True})
    clear_refresh_cookie(response)
    return response


@app.get("/api/notes")
def notes_list(user=Depends(require_user)):
    if isinstance(user, JSONResponse):
        return user
    return {"notes": list_notes(user["id"])}


@app.post("/api/notes")
async def notes_create(request: Request, user=Depends(require_user)):
    if isinstance(user, JSONResponse):
        return user
    data = await request.json()
    content = (data.get("content") or "").strip()
    if not content:
        return json_error("Note content is required", 400)
    if len(content) > 5000:
        return json_error("Note content is too long", 400)
    note = create_note(user["id"], content)
    try:
        index_note_for_user(user["id"], note["id"], note["content"])
    except Exception:
        pass
    return JSONResponse({"note": note}, status_code=201)


@app.delete("/api/notes/{note_id}")
def notes_delete(note_id: str, user=Depends(require_user)):
    if isinstance(user, JSONResponse):
        return user
    delete_note(user["id"], note_id)
    return {"ok": True}


@app.delete("/api/notes")
def notes_clear(user=Depends(require_user)):
    if isinstance(user, JSONResponse):
        return user
    delete_all_notes(user["id"])
    return {"ok": True}


@app.get("/api/portfolio")
def portfolio_get(user=Depends(require_user)):
    if isinstance(user, JSONResponse):
        return user
    snapshot_error = maybe_create_auto_portfolio_snapshot(user["id"])
    payload = portfolio_payload(user["id"])
    if snapshot_error:
        payload["error"] = snapshot_error
    return payload


@app.post("/api/portfolio/parse")
async def portfolio_parse(request: Request, user=Depends(require_user)):
    if isinstance(user, JSONResponse):
        return user
    data = await request.json()
    text = (data.get("text") or "").strip()
    if not text:
        return json_error("Prompt text is required", 400)
    return parse_portfolio_prompt_text(text)


@app.post("/api/portfolio/holdings")
async def portfolio_holdings_upsert(request: Request, user=Depends(require_user)):
    if isinstance(user, JSONResponse):
        return user
    data = await request.json()
    try:
        holdings = validate_portfolio_holding_payload(data.get("holdings") or [])
        upsert_portfolio_holdings(user["id"], holdings)
        create_portfolio_snapshot(user["id"])
    except ValueError as exc:
        return json_error(str(exc), 400)
    return portfolio_payload(user["id"])


@app.delete("/api/portfolio/holdings/{symbol}")
def portfolio_holding_delete(symbol: str, user=Depends(require_user)):
    if isinstance(user, JSONResponse):
        return user
    normalized_symbol = (symbol or "").strip().upper()
    if normalized_symbol not in PORTFOLIO_SYMBOL_TO_COIN_ID:
        return json_error("Unsupported crypto symbol", 400)
    delete_portfolio_holding(user["id"], normalized_symbol)
    remaining_holdings = list_portfolio_holdings(user["id"])
    if remaining_holdings:
        create_portfolio_snapshot(user["id"])
    else:
        clear_portfolio_snapshots(user["id"])
    return portfolio_payload(user["id"])


@app.post("/api/portfolio/refresh")
def portfolio_refresh(user=Depends(require_user)):
    if isinstance(user, JSONResponse):
        return user
    holdings = list_portfolio_holdings(user["id"])
    if not holdings:
        return portfolio_payload(user["id"])

    coin_ids = [holding["coinId"] for holding in holdings]
    try:
        price_data = fetch_market_prices(coin_ids)
        price_by_symbol = {
            MARKET_SYMBOLS[coin_id]: float(row["usd"])
            for coin_id, row in price_data.items()
            if coin_id in MARKET_SYMBOLS and row.get("usd") is not None
        }
    except (KeyError, TypeError, ValueError, requests.RequestException) as exc:
        payload = portfolio_payload(user["id"])
        payload["error"] = f"Could not refresh portfolio prices: {exc}"
        return JSONResponse(payload, status_code=502)

    if not price_by_symbol:
        payload = portfolio_payload(user["id"])
        payload["error"] = "Could not refresh portfolio prices"
        return JSONResponse(payload, status_code=502)

    update_holding_valuations(user["id"], price_by_symbol, priced_at=int(time.time()))
    create_portfolio_snapshot(user["id"])
    return portfolio_payload(user["id"])


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.get("/friends", response_class=HTMLResponse)
def friends_page(request: Request):
    return templates.TemplateResponse(request, "friends.html")


@app.get("/forum", response_class=HTMLResponse)
def forum_page(request: Request):
    return templates.TemplateResponse(request, "forum.html")


@app.get("/profiles", response_class=HTMLResponse)
@app.get("/profile", response_class=HTMLResponse)
def profile_page(request: Request):
    return templates.TemplateResponse(request, "profile.html")


@app.get("/profiles/{user_ref}", response_class=HTMLResponse)
def public_profile_page(request: Request, user_ref: str):
    return templates.TemplateResponse(request, "profile.html", {"profile_ref": user_ref})


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(
        request,
        "auth.html",
        {
            "mode": "login",
            "title": "Login",
            "api_url": "/api/auth/login",
            "switch_url": "/register",
            "switch_label": "Create account",
            "registration_enabled": registration_is_enabled(),
        },
    )


@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    return templates.TemplateResponse(
        request,
        "auth.html",
        {
            "mode": "register",
            "title": "Register",
            "api_url": "/api/auth/register",
            "switch_url": "/login",
            "switch_label": "Back to login",
            "registration_enabled": registration_is_enabled(),
        },
    )


@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request):
    return templates.TemplateResponse(request, "admin.html")


@app.get("/api/admin/users")
def admin_users(user=Depends(require_admin)):
    if isinstance(user, JSONResponse):
        return user
    return {"users": list_admin_users()}


@app.get("/api/admin/request-logs")
def admin_request_logs(limit: int = 100, user=Depends(require_admin)):
    if isinstance(user, JSONResponse):
        return user
    return {"logs": list_admin_request_logs(limit)}


@app.get("/api/admin/actions")
def admin_actions(limit: int = 100, user=Depends(require_admin)):
    if isinstance(user, JSONResponse):
        return user
    return {"actions": list_admin_actions(limit)}


def admin_target(target_user_id: str) -> tuple[Optional[dict], Optional[JSONResponse]]:
    target = get_admin_user(target_user_id)
    if not target:
        return None, json_error("User not found", 404)
    return target, None


def assert_not_self_admin_action(admin: dict, target: dict, action: str) -> Optional[JSONResponse]:
    if str(admin["id"]) == str(target["id"]):
        return json_error(f"Cannot {action} your own account", 400)
    return None


def assert_not_admin_target(target: dict, action: str) -> Optional[JSONResponse]:
    if target.get("isAdmin"):
        return json_error(f"Cannot {action} an admin account", 400)
    return None


@app.post("/api/admin/users/{target_user_id}/reset-password")
async def admin_user_reset_password(target_user_id: str, request: Request, admin=Depends(require_admin)):
    if isinstance(admin, JSONResponse):
        return admin
    target, error = admin_target(target_user_id)
    if error:
        return error

    data = await request.json()
    password = data.get("password") or ""
    if len(password) < 8:
        return json_error("Password must be at least 8 characters", 400)

    updated = reset_user_password(target["id"], password)
    if not updated:
        return json_error("User not found", 404)
    log_admin_action(admin["id"], target["id"], "reset_password", {"username": target["username"]})
    return {"ok": True, "user": updated}


@app.post("/api/admin/users/{target_user_id}/suspend")
async def admin_user_suspend(target_user_id: str, request: Request, admin=Depends(require_admin)):
    if isinstance(admin, JSONResponse):
        return admin
    target, error = admin_target(target_user_id)
    if error:
        return error
    guard = assert_not_self_admin_action(admin, target, "suspend") or assert_not_admin_target(target, "suspend")
    if guard:
        return guard

    data = await request.json()
    reason = (data.get("reason") or "").strip()
    updated = suspend_user(target["id"], reason)
    if not updated:
        return json_error("User not found", 404)
    log_admin_action(admin["id"], target["id"], "suspend_user", {"username": target["username"], "reason": reason})
    return {"ok": True, "user": updated}


@app.post("/api/admin/users/{target_user_id}/unsuspend")
def admin_user_unsuspend(target_user_id: str, admin=Depends(require_admin)):
    if isinstance(admin, JSONResponse):
        return admin
    target, error = admin_target(target_user_id)
    if error:
        return error

    updated = unsuspend_user(target["id"])
    if not updated:
        return json_error("User not found", 404)
    log_admin_action(admin["id"], target["id"], "unsuspend_user", {"username": target["username"]})
    return {"ok": True, "user": updated}


@app.post("/api/admin/users/{target_user_id}/revoke-sessions")
def admin_user_revoke_sessions(target_user_id: str, admin=Depends(require_admin)):
    if isinstance(admin, JSONResponse):
        return admin
    target, error = admin_target(target_user_id)
    if error:
        return error

    revoked = revoke_refresh_tokens_for_user(target["id"])
    log_admin_action(
        admin["id"],
        target["id"],
        "revoke_sessions",
        {"username": target["username"], "revokedRefreshTokens": revoked},
    )
    return {"ok": True, "revokedRefreshTokens": revoked, "user": get_admin_user(target["id"])}


@app.delete("/api/admin/users/{target_user_id}")
def admin_user_delete(target_user_id: str, admin=Depends(require_admin)):
    if isinstance(admin, JSONResponse):
        return admin
    target, error = admin_target(target_user_id)
    if error:
        return error
    guard = assert_not_self_admin_action(admin, target, "delete") or assert_not_admin_target(target, "delete")
    if guard:
        return guard

    log_admin_action(
        admin["id"],
        target["id"],
        "delete_user",
        {"id": target["id"], "username": target["username"], "email": target["email"]},
    )
    deleted = delete_user_by_id(target["id"])
    if not deleted:
        return json_error("User not found", 404)
    return {"ok": True, "deletedUser": deleted}


@app.get("/api/notifications")
def notifications_list(user=Depends(require_user)):
    if isinstance(user, JSONResponse):
        return user
    return notification_payload(user["id"])


@app.post("/api/notifications/check")
def notifications_check(user=Depends(require_user)):
    if isinstance(user, JSONResponse):
        return user
    try:
        created = check_price_notifications_for_user(user)
    except requests.RequestException as exc:
        return json_error(f"Could not check market notifications: {exc}", 502)
    return notification_payload(user["id"], created=created)


@app.post("/api/notifications/read")
def notifications_read(user=Depends(require_user)):
    if isinstance(user, JSONResponse):
        return user
    mark_notifications_read(user["id"])
    return notification_payload(user["id"])


@app.patch("/api/notifications/settings/{setting_id}")
async def notifications_setting_update(setting_id: str, request: Request, user=Depends(require_user)):
    if isinstance(user, JSONResponse):
        return user
    data = await request.json()
    setting = update_notification_setting(
        user_id=user["id"],
        setting_id=setting_id,
        enabled=data.get("enabled") if "enabled" in data else None,
        threshold_percent=data.get("thresholdPercent") if "thresholdPercent" in data else None,
        cooldown_minutes=data.get("cooldownMinutes") if "cooldownMinutes" in data else None,
    )
    if not setting:
        return json_error("Notification setting not found or no changes provided", 404)
    return notification_payload(user["id"])


@app.get("/api/chat/modes")
def chat_modes():
    return {"defaultMode": "instant", "modes": chat_mode_options()}


@app.get("/api/recommendations")
def recommendations(limit: int = 6, user=Depends(require_user)):
    if isinstance(user, JSONResponse):
        return user
    return {"recommendations": personalized_recommendations(user["id"], limit=limit)}


@app.get("/api/agent/greeting")
def agent_greeting(user=Depends(require_user)):
    if isinstance(user, JSONResponse):
        return user
    return agent_greeting_for_user(user)


@app.post("/api/chat")
async def chat(request: Request, user=Depends(require_user)):
    if isinstance(user, JSONResponse):
        return user
    user_input, raw_mode, uploaded_files = await chat_request_payload(request)

    if raw_mode not in CHAT_MODES:
        return json_error("Invalid chat mode", 400)
    if not user_input:
        return json_error("Empty message", 400)

    mode = normalize_chat_mode(raw_mode)
    mode_config = CHAT_MODES[mode]
    started_at = time.perf_counter()
    try:
        try:
            retrieved_notes = retrieve_user_notes(user["id"], user_input, limit=4)
        except Exception:
            retrieved_notes = []
        recent_activity = list_recent_user_request_messages(user["id"], limit=5)
        ai_profile = {
            **(user.get("aiProfile") or {}),
            "displayName": user.get("displayName", ""),
            "bio": user.get("bio", ""),
        }
        reply = run_agent(
            user_input,
            get_conversation_history(user),
            mode=mode,
            retrieved_notes=retrieved_notes,
            ai_profile=ai_profile,
            recent_activity=recent_activity,
            uploaded_files=uploaded_files,
        )
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        log_user_request(user["id"], user_input, reply, "ok", None, duration_ms, mode, mode_config.model)
        return {"reply": reply, "mode": mode, "model": mode_config.model}
    except Exception as exc:
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        log_user_request(user["id"], user_input, None, "error", str(exc), duration_ms, mode, mode_config.model)
        return json_error(str(exc), 500)


@app.post("/api/chat/stream")
async def chat_stream(request: Request, user=Depends(require_user)):
    if isinstance(user, JSONResponse):
        return user
    user_input, raw_mode, uploaded_files = await chat_request_payload(request)

    if raw_mode not in CHAT_MODES:
        return json_error("Invalid chat mode", 400)
    if not user_input:
        return json_error("Empty message", 400)

    mode = normalize_chat_mode(raw_mode)
    mode_config = CHAT_MODES[mode]
    started_at = time.perf_counter()

    try:
        try:
            retrieved_notes = retrieve_user_notes(user["id"], user_input, limit=4)
        except Exception:
            retrieved_notes = []
        recent_activity = list_recent_user_request_messages(user["id"], limit=5)
        ai_profile = {
            **(user.get("aiProfile") or {}),
            "displayName": user.get("displayName", ""),
            "bio": user.get("bio", ""),
        }
    except Exception as exc:
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        log_user_request(user["id"], user_input, None, "error", str(exc), duration_ms, mode, mode_config.model)
        return json_error(str(exc), 500)

    def sse(payload: dict) -> str:
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    def event_stream():
        reply_parts = []
        sources_by_url = {}
        try:
            for event in run_agent_stream(
                user_input,
                get_conversation_history(user),
                mode=mode,
                retrieved_notes=retrieved_notes,
                ai_profile=ai_profile,
                recent_activity=recent_activity,
                uploaded_files=uploaded_files,
                event_mode=True,
            ):
                if isinstance(event, dict):
                    event_type = event.get("type")
                    if event_type == "token":
                        content = event.get("content", "")
                        reply_parts.append(content)
                        yield sse({"type": "token", "content": content})
                    elif event_type == "sources":
                        fresh_sources = []
                        for source in event.get("sources", []) or []:
                            url = source.get("url")
                            if not url or url in sources_by_url:
                                continue
                            sources_by_url[url] = source
                            fresh_sources.append(source)
                        if fresh_sources:
                            yield sse({"type": "sources", "sources": fresh_sources})
                    elif event_type == "status":
                        yield sse(event)
                    continue

                token = str(event)
                reply_parts.append(token)
                yield sse({"type": "token", "content": token})

            reply = "".join(reply_parts) or "No response."
            duration_ms = int((time.perf_counter() - started_at) * 1000)
            log_user_request(user["id"], user_input, reply, "ok", None, duration_ms, mode, mode_config.model)
            yield sse({
                "type": "done",
                "mode": mode,
                "model": mode_config.model,
                "sources": list(sources_by_url.values()),
            })
        except Exception as exc:
            duration_ms = int((time.perf_counter() - started_at) * 1000)
            partial = "".join(reply_parts) or None
            log_user_request(user["id"], user_input, partial, "error", str(exc), duration_ms, mode, mode_config.model)
            yield sse({"type": "error", "error": str(exc)})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
