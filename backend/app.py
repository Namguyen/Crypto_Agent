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
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

try:
    import jwt
except Exception:  # pragma: no cover - helpful error when dependency missing
    jwt = None

from backend.ai.agent import CHAT_MODES, chat_mode_options, normalize_chat_mode, run_agent
from backend.ai.retrieval import index_note_for_user, retrieve_user_notes
from backend.auth.store import (
    cleanup_expired_refresh_tokens,
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
from backend.social.routes import router as social_router
from backend.social.store import init_social_db
from backend.users.routes import router as users_router

dotenv.load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
UPLOAD_ROOT = Path(os.getenv("UPLOAD_ROOT") or (PROJECT_ROOT / "uploads")).resolve()
UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Crypto Agent")
templates = Jinja2Templates(directory=str(PROJECT_ROOT / "frontend" / "templates"))
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_ROOT)), name="uploads")
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
dev_reload_cache = {"checked_at": 0.0, "version": "0"}

init_auth_db()
init_social_db()
init_chat_db()
init_forum_db()
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


def issue_auth_response(user: dict, request: Request, status_code: int = 200) -> JSONResponse:
    access_token = sign_access_token(user)
    refresh_token, _ = create_refresh_token_record(user, request)
    response = JSONResponse(
        {
            "ok": True,
            "accessToken": access_token,
            "expiresIn": ACCESS_TOKEN_EXP_SECONDS,
            "user": user,
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

BINANCE_USDT_PAIRS = {coin_id: f"{symbol}USDT" for coin_id, symbol in MARKET_SYMBOLS.items()}
COINBASE_USD_PRODUCTS = {
    coin_id: f"{symbol}-USD"
    for coin_id, symbol in MARKET_SYMBOLS.items()
    if symbol not in {"BNB"}
}
MARKET_REQUEST_HEADERS = {"User-Agent": "Crypto-Agent/1.0"}


def compact_coin_ids(coin_ids: list[str]) -> list[str]:
    return list(dict.fromkeys(coin_id for coin_id in coin_ids if coin_id))


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


@app.get("/api/price-data")
def price_data():
    return {"prices": price_sidebar_data()}


@app.get("/api/dev/reload-version")
def dev_reload_version():
    return {"enabled": DEV_LIVE_RELOAD, "version": source_reload_version() if DEV_LIVE_RELOAD else "0"}


@app.get("/api/auth/me")
def auth_me(request: Request):
    user, auth_error = authenticate_request(request)
    return {
        "authenticated": bool(user),
        "user": user,
        "authError": auth_error,
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
    return issue_auth_response(user, request)


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


@app.post("/api/chat")
async def chat(request: Request, user=Depends(require_user)):
    if isinstance(user, JSONResponse):
        return user
    data = await request.json()
    user_input = data.get("message", "").strip()
    raw_mode = (data.get("mode") or "instant").strip().lower()

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
        reply = run_agent(user_input, get_conversation_history(user), mode=mode, retrieved_notes=retrieved_notes)
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        log_user_request(user["id"], user_input, reply, "ok", None, duration_ms, mode, mode_config.model)
        return {"reply": reply, "mode": mode, "model": mode_config.model}
    except Exception as exc:
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        log_user_request(user["id"], user_input, None, "error", str(exc), duration_ms, mode, mode_config.model)
        return json_error(str(exc), 500)
