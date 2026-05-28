import os
from typing import Optional

import dotenv
from fastapi import HTTPException, Request

try:
    import jwt
except Exception:  # pragma: no cover - dependency error is clearer at runtime
    jwt = None

from backend.auth.store import get_user_by_id

dotenv.load_dotenv()

JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
APP_SECRET_KEY = os.getenv("FLASK_SECRET_KEY") or os.getenv("SECRET_KEY") or ""
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY") or APP_SECRET_KEY
ACCESS_TOKEN_SECRET = os.getenv("ACCESS_TOKEN_SECRET") or JWT_SECRET_KEY


def bearer_token(request: Request) -> str:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return ""
    return auth_header.split(" ", 1)[1].strip()


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


def authenticate_request(request: Request) -> tuple[Optional[dict], Optional[str]]:
    token = bearer_token(request)
    if not token:
        return None, None
    try:
        user = decode_access_token(token)
        request.state.current_user = user
        return user, None
    except jwt.ExpiredSignatureError:
        return None, "Access token expired"
    except jwt.InvalidTokenError:
        return None, "Invalid token"


def require_user(request: Request) -> dict:
    if hasattr(request.state, "current_user"):
        return request.state.current_user

    user, auth_error = authenticate_request(request)
    if not user:
        raise HTTPException(
            status_code=401,
            detail={
                "error": auth_error or "Login required",
                "loginUrl": "/login",
                "refreshUrl": "/api/auth/refresh",
            },
        )
    return user
