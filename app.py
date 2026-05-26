import os
import json
import secrets
from functools import wraps
from urllib.parse import urlencode

import requests
import time
try:
    import jwt
except Exception:  # pragma: no cover - helpful error when dependency missing
    jwt = None
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify, redirect, session, url_for
from openai import OpenAI
import dotenv
from tools import TOOL_SCHEMA, TOOL_MAP

dotenv.load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY") or os.getenv("SECRET_KEY") or secrets.token_hex(32)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
GOOGLE_OAUTH_CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "")
GOOGLE_OAUTH_CLIENT_SECRET = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "")
OAUTH_SCOPES = "openid email profile"

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



def oauth_is_configured() -> bool:
    return bool(GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET)


def self_auth_is_configured() -> bool:
    return bool(SELF_AUTH_USERNAME and SELF_AUTH_PASSWORD)


def verify_id_token(id_token: str) -> dict:
    """Verify a Google ID token (JWT) via Google's tokeninfo endpoint.
    Returns the token payload as a dict on success or raises Exception on failure.
    """
    try:
        resp = requests.get(
            "https://oauth2.googleapis.com/tokeninfo",
            params={"id_token": id_token},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise Exception("Invalid ID token") from exc

    # Validate audience if a client id is configured
    if GOOGLE_OAUTH_CLIENT_ID and data.get("aud") != GOOGLE_OAUTH_CLIENT_ID:
        raise Exception("ID token audience does not match client id")

    # Validate expiry if present
    try:
        exp = int(data.get("exp", 0))
        if exp and exp < int(time.time()):
            raise Exception("ID token has expired")
    except (TypeError, ValueError):
        pass

    return data


JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY") or app.secret_key
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_EXP_SECONDS = int(os.getenv("JWT_EXP_SECONDS", "3600"))
SELF_AUTH_USERNAME = os.getenv("SELF_AUTH_USERNAME")
SELF_AUTH_PASSWORD = os.getenv("SELF_AUTH_PASSWORD")


def create_self_signed_jwt(subject: str, name: str = None) -> str:
    if jwt is None:
        raise RuntimeError("pyjwt is not installed. Install with `pip install pyjwt`.")
    now = int(time.time())
    payload = {
        "sub": subject,
        "name": name or subject,
        "iat": now,
        "exp": now + JWT_EXP_SECONDS,
    }
    token = jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    # pyjwt may return bytes in older versions
    if isinstance(token, bytes):
        token = token.decode('utf-8')
    return token


def verify_self_signed_jwt(token: str) -> dict:
    if jwt is None:
        raise RuntimeError("pyjwt is not installed. Install with `pip install pyjwt`.")
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise Exception("Token has expired")
    except jwt.InvalidTokenError:
        raise Exception("Invalid token")


def oauth_redirect_uri() -> str:
    return os.getenv("GOOGLE_OAUTH_REDIRECT_URI") or url_for("oauth_callback", _external=True)


def current_user():
    return session.get("user")


def conversation_key_for_user(user: dict) -> str:
    return f"google:{user['id']}"


def get_conversation_history() -> list:
    user = current_user()
    if not user:
        return []
    key = conversation_key_for_user(user)
    return conversation_histories.setdefault(key, [])


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user():
            # Allow Authorization: Bearer <self-signed-token> for stateless API access
            auth_header = request.headers.get('Authorization', '')
            if auth_header.startswith('Bearer '):
                token = auth_header.split(' ', 1)[1].strip()
                if token:
                    # Try self-signed JWT first
                    try:
                        payload = None
                        if jwt is not None:
                            try:
                                payload = verify_self_signed_jwt(token)
                            except Exception:
                                payload = None
                        # If self-signed verify didn't work, try Google ID token verification
                        if payload is None:
                            try:
                                payload = verify_id_token(token)
                            except Exception:
                                payload = None
                        if payload and payload.get('sub'):
                            # establish a temporary session user
                            session['user'] = {
                                'id': payload.get('sub'),
                                'name': payload.get('name') or payload.get('email') or payload.get('sub'),
                                'email': payload.get('email', ''),
                                'picture': payload.get('picture', ''),
                                'email_verified': bool(payload.get('email_verified')),
                            }
                            return view(*args, **kwargs)
                    except Exception:
                        pass

            return jsonify({
                "error": "Login required",
                "loginUrl": url_for("oauth_login"),
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
    user = current_user()
    return jsonify({
        "authenticated": bool(user),
        "user": user,
        "oauthConfigured": oauth_is_configured(),
        "selfAuthConfigured": self_auth_is_configured(),
        "loginUrl": url_for("oauth_login"),
        "jwtLoginUrl": url_for("jwt_login"),
        "selfLoginUrl": url_for("self_login"),
        "selfVerifyUrl": url_for("self_verify"),
        "logoutUrl": url_for("logout"),
    })


@app.route('/auth/login')
@app.route('/auth/google/login')
def oauth_login():
    if not oauth_is_configured():
        return (
            "Google OAuth is not configured. Add GOOGLE_OAUTH_CLIENT_ID, "
            "GOOGLE_OAUTH_CLIENT_SECRET, and FLASK_SECRET_KEY to .env, then restart Flask.",
            503,
        )

    state = secrets.token_urlsafe(32)
    redirect_uri = oauth_redirect_uri()
    session["oauth_state"] = state
    session["oauth_redirect_uri"] = redirect_uri

    params = {
        "client_id": GOOGLE_OAUTH_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": OAUTH_SCOPES,
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    }
    return redirect(f"{GOOGLE_AUTH_URL}?{urlencode(params)}")


@app.route('/auth/callback')
@app.route('/auth/google/callback')
def oauth_callback():
    if not oauth_is_configured():
        return "Google OAuth is not configured.", 503

    expected_state = session.pop("oauth_state", None)
    redirect_uri = session.pop("oauth_redirect_uri", oauth_redirect_uri())
    received_state = request.args.get("state")
    if not expected_state or received_state != expected_state:
        return "Invalid OAuth state. Please try logging in again.", 400

    if request.args.get("error"):
        return f"OAuth login failed: {request.args['error']}", 400

    code = request.args.get("code")
    if not code:
        return "OAuth callback did not include an authorization code.", 400

    try:
        token_response = requests.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": GOOGLE_OAUTH_CLIENT_ID,
                "client_secret": GOOGLE_OAUTH_CLIENT_SECRET,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
            timeout=10,
        )
        token_response.raise_for_status()
        token_data = token_response.json()
    except (requests.RequestException, ValueError) as exc:
        return f"Could not exchange OAuth code for tokens: {exc}", 502

    access_token = token_data.get("access_token")
    if not access_token:
        return "OAuth provider did not return an access token.", 502

    try:
        profile_response = requests.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        profile_response.raise_for_status()
        profile = profile_response.json()
    except (requests.RequestException, ValueError) as exc:
        return f"Could not load OAuth profile: {exc}", 502

    if not profile.get("sub"):
        return "OAuth profile did not include a stable user id.", 502

    session["user"] = {
        "id": profile["sub"],
        "name": profile.get("name") or profile.get("email") or "Google User",
        "email": profile.get("email", ""),
        "picture": profile.get("picture", ""),
        "email_verified": bool(profile.get("email_verified")),
    }
    return redirect(url_for("index"))


@app.route('/auth/jwt-login', methods=['POST'])
def jwt_login():
    data = request.get_json(silent=True) or {}
    id_token = data.get('id_token') or request.form.get('id_token')
    if not id_token:
        return jsonify({"error": "Missing id_token"}), 400

    try:
        profile = verify_id_token(id_token)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 401

    if not profile.get('sub'):
        return jsonify({"error": "ID token did not include a user id"}), 401

    session['user'] = {
        'id': profile['sub'],
        'name': profile.get('name') or profile.get('email') or 'Google User',
        'email': profile.get('email', ''),
        'picture': profile.get('picture', ''),
        'email_verified': bool(profile.get('email_verified')),
    }

    return jsonify({"ok": True})


@app.route('/auth/self-login', methods=['POST'])
def self_login():
    """Issue a self-signed JWT for a local user and set session.

    Behavior:
    - If `SELF_AUTH_USERNAME` and `SELF_AUTH_PASSWORD` are set in env, the request
      must match those credentials.
    - Otherwise any `username` is accepted (convenience for local dev).
    Request JSON: {"username": "...", "password": "..."}
    Returns: {"token": "..."}
    """
    if jwt is None:
        return jsonify({"error": "pyjwt library not installed"}), 500

    data = request.get_json(silent=True) or {}
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''
    if not username:
        return jsonify({"error": "Missing username"}), 400

    if SELF_AUTH_USERNAME and SELF_AUTH_PASSWORD:
        if username != SELF_AUTH_USERNAME or password != SELF_AUTH_PASSWORD:
            return jsonify({"error": "Invalid credentials"}), 401
    # else: permissive for local dev

    token = create_self_signed_jwt(username, name=username)
    # Also populate session for existing session-based flows
    session['user'] = {
        'id': username,
        'name': username,
        'email': '',
        'picture': '',
        'email_verified': False,
    }
    return jsonify({"token": token})


@app.route('/auth/self-verify', methods=['POST'])
def self_verify():
    """Verify a self-signed JWT and create a session for the user.

    Request JSON: {"token": "..."}
    Returns: {"ok": True}
    """
    if jwt is None:
        return jsonify({"error": "pyjwt library not installed"}), 500

    data = request.get_json(silent=True) or {}
    token = data.get('token') or request.form.get('token')
    if not token:
        return jsonify({"error": "Missing token"}), 400

    try:
        payload = verify_self_signed_jwt(token)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 401

    sub = payload.get('sub')
    if not sub:
        return jsonify({"error": "Token payload missing subject"}), 401

    session['user'] = {
        'id': sub,
        'name': payload.get('name') or sub,
        'email': payload.get('email', ''),
        'picture': payload.get('picture', ''),
        'email_verified': bool(payload.get('email_verified')),
    }
    return jsonify({"ok": True})


@app.route('/auth/logout', methods=['POST'])
def logout():
    user = current_user()
    if user:
        conversation_histories.pop(conversation_key_for_user(user), None)
    session.clear()
    return jsonify({"ok": True})


@app.route('/')
def index():
    return render_template('index.html', google_client_id=GOOGLE_OAUTH_CLIENT_ID)


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
