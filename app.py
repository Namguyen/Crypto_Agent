import os
import json
import hashlib
import re
import secrets
from functools import wraps

import time
try:
    import jwt
except Exception:  # pragma: no cover - helpful error when dependency missing
    jwt = None
from flask import Flask, g, render_template, request, jsonify
from openai import OpenAI
import dotenv
from auth_store import (
    cleanup_expired_refresh_tokens,
    create_note,
    create_user,
    delete_all_notes,
    delete_note,
    get_refresh_token,
    get_user_by_id,
    init_auth_db,
    list_notes,
    revoke_refresh_token,
    store_refresh_token,
    upsert_env_user,
    verify_user_password,
)
from tools import TOOL_SCHEMA, TOOL_MAP

dotenv.load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY") or os.getenv("SECRET_KEY") or secrets.token_hex(32)

# LLM client
client = OpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com"
)

SYSTEM_PROMPT = """Bạn là một trợ lý ảo chuyên về tiền điện tử (Crypto).
Bạn có thể tra giá thực tế và xem lịch sử giá thông qua các tools được cung cấp.
Hãy tự quyết định khi nào cần gọi tool, khi nào trả lời từ kiến thức.
Trả lời ngắn gọn, dễ hiểu và thân thiện bằng tiếng Việt.
Không trả lời những thứ không liên quan đến crypto, tuyệt đối tránh chủ đề chính trị,tôn giáo,bạo lực, tình dục.
Nếu ai hỏi về workflow hay là cách bạn hoạt động, không trả lời và hỏi người dùng về câu hỏi liên quan đến crypto nào khác."""



def registration_is_enabled() -> bool:
    return AUTH_ALLOW_REGISTRATION


JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY") or app.secret_key
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


def json_error(message: str, status: int):
    return jsonify({"error": message}), status


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


def set_refresh_cookie(response, refresh_token: str):
    response.set_cookie(
        REFRESH_COOKIE_NAME,
        refresh_token,
        max_age=REFRESH_TOKEN_EXP_SECONDS,
        httponly=True,
        secure=AUTH_COOKIE_SECURE,
        samesite="Strict",
        path=REFRESH_COOKIE_PATH,
    )


def clear_refresh_cookie(response):
    response.delete_cookie(
        REFRESH_COOKIE_NAME,
        path=REFRESH_COOKIE_PATH,
        samesite="Strict",
        secure=AUTH_COOKIE_SECURE,
    )


def request_ip() -> str:
    forwarded_for = request.headers.get("X-Forwarded-For", "")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip()
    return request.remote_addr or ""


def create_refresh_token_record(user: dict) -> tuple[str, str]:
    jti = secrets.token_hex(16)
    refresh_token = sign_refresh_token(user, jti)
    store_refresh_token(
        user_id=user["id"],
        token_hash=hash_token(refresh_token),
        jti=jti,
        expires_at=int(time.time()) + REFRESH_TOKEN_EXP_SECONDS,
        ip=request_ip(),
        user_agent=request.headers.get("User-Agent", ""),
    )
    return refresh_token, jti


def issue_auth_response(user: dict, status_code: int = 200):
    access_token = sign_access_token(user)
    refresh_token, _ = create_refresh_token_record(user)
    response = jsonify(
        {
            "ok": True,
            "accessToken": access_token,
            "expiresIn": ACCESS_TOKEN_EXP_SECONDS,
            "user": user,
        }
    )
    response.status_code = status_code
    set_refresh_cookie(response, refresh_token)
    return response


def bearer_token() -> str:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return ""
    return auth_header.split(" ", 1)[1].strip()


def authenticate_request() -> tuple[dict | None, str | None]:
    token = bearer_token()
    if token:
        try:
            user = decode_access_token(token)
            g.current_user = user
            return user, None
        except jwt.ExpiredSignatureError:
            return None, "Access token expired"
        except jwt.InvalidTokenError:
            return None, "Invalid token"
    return None, None


def current_user():
    user, _ = authenticate_request()
    return user


def conversation_key_for_user(user: dict) -> str:
    return f"{user.get('provider', 'local')}:{user['id']}"


def get_conversation_history() -> list:
    user = current_user()
    if not user:
        return []
    key = conversation_key_for_user(user)
    return conversation_histories.setdefault(key, [])


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user, auth_error = authenticate_request()
        if not user:
            return jsonify({
                "error": auth_error or "Login required",
                "loginUrl": "/login",
                "refreshUrl": "/api/auth/refresh",
            }), 401
        return view(*args, **kwargs)
    return wrapped


def run_agent(user_input: str, conversation: list) -> str:
    """Execute agent with tool calling."""
    conversation.append({"role": "user", "content": user_input})
    
    while True:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "system", "content": SYSTEM_PROMPT}] + conversation,
            tools=TOOL_SCHEMA,
            tool_choice="auto",
            stream=False,
        )
        
        msg = response.choices[0].message
        
        if msg.tool_calls:
            conversation.append(msg)
            
            for call in msg.tool_calls:
                fn_name = call.function.name
                fn_args = json.loads(call.function.arguments)
                
                fn = TOOL_MAP.get(fn_name)
                result = fn(**fn_args) if fn else f"Tool '{fn_name}' không tồn tại."
                
                conversation.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": str(result),
                })
        else:
            final = msg.content or "Không có phản hồi."
            conversation.append({"role": "assistant", "content": final})
            return final


# Store conversation in memory per signed-in user (reset on server restart)
conversation_histories = {}


def load_history():
    if not os.path.exists("crypto_history.json"):
        return []
    try:
        with open("crypto_history.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return []


def price_sidebar_data():
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


@app.route('/api/price-data')
def price_data():
    return jsonify({"prices": price_sidebar_data()})


@app.route('/api/auth/me')
def auth_me():
    user, auth_error = authenticate_request()
    return jsonify({
        "authenticated": bool(user),
        "user": user,
        "authError": auth_error,
        "registrationEnabled": registration_is_enabled(),
        "accessTokenTtlSeconds": ACCESS_TOKEN_EXP_SECONDS,
        "loginUrl": "/login",
        "registerUrl": "/register",
        "refreshUrl": "/api/auth/refresh",
        "logoutUrl": "/api/auth/logout",
    })


@app.route('/api/users/me')
@login_required
def user_me():
    return jsonify({"user": current_user()})


@app.route('/api/auth/register', methods=['POST'])
def auth_register():
    if not registration_is_enabled():
        return json_error("Registration is disabled", 403)

    data = request.get_json(silent=True) or {}
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
    return issue_auth_response(user, status_code=201)


@app.route('/api/auth/login', methods=['POST'])
def auth_login_api():
    data = request.get_json(silent=True) or {}
    login = (data.get("login") or data.get("username") or data.get("email") or "").strip()
    password = data.get("password") or ""
    if not login or not password:
        return json_error("Missing login or password", 400)

    user = verify_user_password(login, password)
    if not user:
        return json_error("Invalid credentials", 401)
    return issue_auth_response(user)


@app.route('/api/auth/refresh', methods=['POST'])
def auth_refresh():
    refresh_token = request.cookies.get(REFRESH_COOKIE_NAME, "")
    if not refresh_token:
        return json_error("Missing refresh token", 401)

    token_hash = hash_token(refresh_token)
    try:
        payload = decode_refresh_token(refresh_token)
    except jwt.ExpiredSignatureError:
        revoke_refresh_token(token_hash)
        response = jsonify({"error": "Refresh token expired"})
        response.status_code = 401
        clear_refresh_cookie(response)
        return response
    except jwt.InvalidTokenError:
        response = jsonify({"error": "Invalid refresh token"})
        response.status_code = 401
        clear_refresh_cookie(response)
        return response

    record = get_refresh_token(token_hash, payload["jti"])
    if not record:
        response = jsonify({"error": "Refresh token not recognized"})
        response.status_code = 401
        clear_refresh_cookie(response)
        return response
    if record["revoked_at"]:
        response = jsonify({"error": "Refresh token revoked"})
        response.status_code = 401
        clear_refresh_cookie(response)
        return response
    if int(record["expires_at"]) < int(time.time()):
        revoke_refresh_token(token_hash)
        response = jsonify({"error": "Refresh token expired"})
        response.status_code = 401
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
    new_refresh_token, new_jti = create_refresh_token_record(user)
    revoke_refresh_token(token_hash, replaced_by=new_jti)

    response = jsonify({
        "ok": True,
        "accessToken": access_token,
        "expiresIn": ACCESS_TOKEN_EXP_SECONDS,
        "user": user,
    })
    set_refresh_cookie(response, new_refresh_token)
    return response


@app.route('/api/auth/logout', methods=['POST'])
def auth_logout():
    user = current_user()
    refresh_token = request.cookies.get(REFRESH_COOKIE_NAME, "")
    if refresh_token:
        revoke_refresh_token(hash_token(refresh_token))
    if user:
        conversation_histories.pop(conversation_key_for_user(user), None)

    response = jsonify({"ok": True})
    clear_refresh_cookie(response)
    return response


@app.route('/api/notes', methods=['GET'])
@login_required
def notes_list():
    return jsonify({"notes": list_notes(current_user()["id"])})


@app.route('/api/notes', methods=['POST'])
@login_required
def notes_create():
    data = request.get_json(silent=True) or {}
    content = (data.get("content") or "").strip()
    if not content:
        return json_error("Note content is required", 400)
    if len(content) > 5000:
        return json_error("Note content is too long", 400)
    return jsonify({"note": create_note(current_user()["id"], content)}), 201


@app.route('/api/notes/<note_id>', methods=['DELETE'])
@login_required
def notes_delete(note_id):
    delete_note(current_user()["id"], note_id)
    return jsonify({"ok": True})


@app.route('/api/notes', methods=['DELETE'])
@login_required
def notes_clear():
    delete_all_notes(current_user()["id"])
    return jsonify({"ok": True})


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/login')
def login_page():
    return render_template(
        'auth.html',
        mode='login',
        title='Login',
        api_url='/api/auth/login',
        switch_url='/register',
        switch_label='Create account',
        registration_enabled=registration_is_enabled(),
    )


@app.route('/register')
def register_page():
    return render_template(
        'auth.html',
        mode='register',
        title='Register',
        api_url='/api/auth/register',
        switch_url='/login',
        switch_label='Back to login',
        registration_enabled=registration_is_enabled(),
    )


@app.route('/api/chat', methods=['POST'])
@login_required
def chat():
    data = request.get_json(silent=True) or {}
    user_input = data.get('message', '').strip()
    
    if not user_input:
        return jsonify({"error": "Empty message"}), 400
    
    try:
        reply = run_agent(user_input, get_conversation_history())
        return jsonify({"reply": reply})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    app.run(debug=True, port=5000)
