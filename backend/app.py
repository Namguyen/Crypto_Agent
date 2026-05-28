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
from fastapi.templating import Jinja2Templates

try:
    import jwt
except Exception:  # pragma: no cover - helpful error when dependency missing
    jwt = None

from backend.ai.agent import CHAT_MODES, chat_mode_options, normalize_chat_mode, run_agent
from backend.auth.store import (
    cleanup_expired_refresh_tokens,
    create_note,
    create_user,
    delete_all_notes,
    delete_note,
    get_refresh_token,
    get_user_by_id,
    init_auth_db,
    active_notification_settings,
    create_notification_event,
    list_notification_events,
    list_notification_settings,
    list_admin_request_logs,
    list_admin_users,
    list_notes,
    log_user_request,
    mark_notifications_read,
    revoke_refresh_token,
    store_refresh_token,
    unread_notification_count,
    update_notification_setting,
    upsert_env_user,
    verify_user_password,
)

dotenv.load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent

app = FastAPI(title="Crypto Agent")
templates = Jinja2Templates(directory=str(PROJECT_ROOT / "frontend" / "templates"))

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

init_auth_db()
cleanup_expired_refresh_tokens()
if SELF_AUTH_USERNAME and SELF_AUTH_PASSWORD:
    upsert_env_user(SELF_AUTH_USERNAME, SELF_AUTH_PASSWORD, SELF_AUTH_EMAIL)

conversation_histories = {}


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
        except jwt.InvalidTokenError:
            return None, "Invalid token"
    return None, None


def current_user(request: Request) -> Optional[dict]:
    if hasattr(request.state, "current_user"):
        return request.state.current_user
    user, _ = authenticate_request(request)
    return user


def require_user(request: Request) -> dict | JSONResponse:
    user, auth_error = authenticate_request(request)
    if not user:
        return json_error(auth_error or "Login required", 401)
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


def fetch_market_prices(coin_ids: list[str]) -> dict:
    if not coin_ids:
        return {}
    response = requests.get(
        "https://api.coingecko.com/api/v3/simple/price",
        params={
            "ids": ",".join(coin_ids),
            "vs_currencies": "usd",
            "include_24hr_change": "true",
        },
        timeout=8,
    )
    response.raise_for_status()
    return response.json()


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
        message = (
            f"{setting['symbol']} {direction} {abs(float(change)):.2f}% in 24h. "
            f"Current price: ${float(price):,.2f}."
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

    user = {
        "id": str(record["user_id"]),
        "username": record["username"],
        "email": record["email"] or "",
        "name": record["username"],
        "picture": "",
        "email_verified": bool(record["email"]),
        "provider": "local",
    }
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
    return JSONResponse({"note": create_note(user["id"], content)}, status_code=201)


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
def admin_users(user=Depends(require_user)):
    if isinstance(user, JSONResponse):
        return user
    return {"users": list_admin_users()}


@app.get("/api/admin/request-logs")
def admin_request_logs(limit: int = 100, user=Depends(require_user)):
    if isinstance(user, JSONResponse):
        return user
    return {"logs": list_admin_request_logs(limit)}


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
        reply = run_agent(user_input, get_conversation_history(user), mode=mode)
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        log_user_request(user["id"], user_input, reply, "ok", None, duration_ms, mode, mode_config.model)
        return {"reply": reply, "mode": mode, "model": mode_config.model}
    except Exception as exc:
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        log_user_request(user["id"], user_input, None, "error", str(exc), duration_ms, mode, mode_config.model)
        return json_error(str(exc), 500)
