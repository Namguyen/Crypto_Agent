import os
import sqlite3
import time
from contextlib import contextmanager
from typing import Optional

from werkzeug.security import check_password_hash, generate_password_hash


def auth_db_path() -> str:
    return os.getenv("AUTH_DB_PATH", "auth.db")


def connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(auth_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def auth_connection():
    conn = connect_db()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_auth_db() -> None:
    with auth_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                email TEXT UNIQUE COLLATE NOCASE,
                display_name TEXT,
                bio TEXT,
                picture TEXT,
                password_hash TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS refresh_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                jti TEXT NOT NULL UNIQUE,
                expires_at INTEGER NOT NULL,
                revoked_at INTEGER,
                replaced_by TEXT,
                created_at INTEGER NOT NULL,
                ip TEXT,
                user_agent TEXT,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_refresh_tokens_user_id
                ON refresh_tokens(user_id);
            CREATE INDEX IF NOT EXISTS idx_refresh_tokens_expires_at
                ON refresh_tokens(expires_at);

            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_notes_user_id_created_at
                ON notes(user_id, created_at DESC);

            CREATE TABLE IF NOT EXISTS user_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                message TEXT NOT NULL,
                reply TEXT,
                status TEXT NOT NULL,
                error TEXT,
                mode TEXT NOT NULL DEFAULT 'instant',
                model TEXT,
                duration_ms INTEGER,
                created_at INTEGER NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_user_requests_user_id_created_at
                ON user_requests(user_id, created_at DESC);

            CREATE TABLE IF NOT EXISTS notification_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                coin_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                threshold_percent REAL NOT NULL DEFAULT 3.0,
                cooldown_minutes INTEGER NOT NULL DEFAULT 60,
                enabled INTEGER NOT NULL DEFAULT 1,
                last_notified_at INTEGER,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                UNIQUE(user_id, coin_id),
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS notification_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                setting_id INTEGER,
                coin_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                title TEXT NOT NULL,
                message TEXT NOT NULL,
                price_usd REAL,
                change_percent REAL,
                created_at INTEGER NOT NULL,
                read_at INTEGER,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY(setting_id) REFERENCES notification_settings(id) ON DELETE SET NULL
            );

            CREATE INDEX IF NOT EXISTS idx_notification_events_user_created_at
                ON notification_events(user_id, created_at DESC);
            """
        )
        ensure_column(conn, "users", "display_name", "TEXT")
        ensure_column(conn, "users", "bio", "TEXT")
        ensure_column(conn, "users", "picture", "TEXT")
        ensure_column(conn, "user_requests", "mode", "TEXT NOT NULL DEFAULT 'instant'")
        ensure_column(conn, "user_requests", "model", "TEXT")


def ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def normalize_email(email: Optional[str]) -> Optional[str]:
    value = (email or "").strip().lower()
    return value or None


def public_user(row: sqlite3.Row | dict) -> dict:
    display_name = (row["display_name"] if "display_name" in row.keys() else "") or row["username"]
    picture = (row["picture"] if "picture" in row.keys() else "") or ""
    bio = (row["bio"] if "bio" in row.keys() else "") or ""
    return {
        "id": str(row["id"]),
        "username": row["username"],
        "email": row["email"] or "",
        "name": display_name,
        "displayName": display_name,
        "bio": bio,
        "picture": picture,
        "email_verified": bool(row["email"]),
        "provider": "local",
    }


def create_user(username: str, email: Optional[str], password: str) -> dict:
    now = int(time.time())
    try:
        with auth_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO users (username, email, password_hash, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    username.strip(),
                    normalize_email(email),
                    generate_password_hash(password),
                    now,
                    now,
                ),
            )
            user_id = cursor.lastrowid
    except sqlite3.IntegrityError as exc:
        raise ValueError("Username or email already exists") from exc
    return get_user_by_id(user_id)


def get_user_by_id(user_id: int | str) -> Optional[dict]:
    with auth_connection() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (str(user_id),)).fetchone()
    return public_user(row) if row else None


def find_user_by_login(login: str) -> Optional[sqlite3.Row]:
    value = (login or "").strip()
    if not value:
        return None
    with auth_connection() as conn:
        return conn.execute(
            """
            SELECT * FROM users
            WHERE username = ? COLLATE NOCASE OR email = ? COLLATE NOCASE
            """,
            (value, value.lower()),
        ).fetchone()


def verify_user_password(login: str, password: str) -> Optional[dict]:
    row = find_user_by_login(login)
    if not row or not check_password_hash(row["password_hash"], password or ""):
        return None
    return public_user(row)


def upsert_env_user(username: str, password: str, email: Optional[str] = None) -> dict:
    now = int(time.time())
    existing = find_user_by_login(username)
    normalized_email = normalize_email(email)
    if existing:
        with auth_connection() as conn:
            conn.execute(
                """
                UPDATE users
                SET email = COALESCE(?, email),
                    password_hash = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    normalized_email,
                    generate_password_hash(password),
                    now,
                    existing["id"],
                ),
            )
        return get_user_by_id(existing["id"])
    return create_user(username, normalized_email, password)


def store_refresh_token(
    user_id: str,
    token_hash: str,
    jti: str,
    expires_at: int,
    ip: str,
    user_agent: str,
) -> None:
    now = int(time.time())
    with auth_connection() as conn:
        conn.execute(
            """
            INSERT INTO refresh_tokens
                (user_id, token_hash, jti, expires_at, created_at, ip, user_agent)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, token_hash, jti, expires_at, now, ip, user_agent),
        )


def get_refresh_token(token_hash: str, jti: str) -> Optional[sqlite3.Row]:
    with auth_connection() as conn:
        return conn.execute(
            """
            SELECT
                rt.*,
                u.username AS username,
                u.email AS email
            FROM refresh_tokens rt
            JOIN users u ON u.id = rt.user_id
            WHERE rt.token_hash = ? AND rt.jti = ?
            """,
            (token_hash, jti),
        ).fetchone()


def revoke_refresh_token(token_hash: str, replaced_by: Optional[str] = None) -> None:
    now = int(time.time())
    with auth_connection() as conn:
        conn.execute(
            """
            UPDATE refresh_tokens
            SET revoked_at = COALESCE(revoked_at, ?),
                replaced_by = COALESCE(replaced_by, ?)
            WHERE token_hash = ?
            """,
            (now, replaced_by, token_hash),
        )


def cleanup_expired_refresh_tokens() -> None:
    now = int(time.time())
    with auth_connection() as conn:
        conn.execute(
            """
            DELETE FROM refresh_tokens
            WHERE expires_at < ? AND revoked_at IS NOT NULL
            """,
            (now,),
        )


def public_note(row: sqlite3.Row | dict) -> dict:
    return {
        "id": str(row["id"]),
        "content": row["content"],
        "time": time.strftime("%H:%M:%S", time.localtime(int(row["created_at"]))),
        "createdAt": int(row["created_at"]),
    }


def list_notes(user_id: int | str) -> list[dict]:
    with auth_connection() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM notes
            WHERE user_id = ?
            ORDER BY created_at DESC, id DESC
            """,
            (str(user_id),),
        ).fetchall()
    return [public_note(row) for row in rows]


def create_note(user_id: int | str, content: str) -> dict:
    now = int(time.time())
    with auth_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO notes (user_id, content, created_at)
            VALUES (?, ?, ?)
            """,
            (str(user_id), content, now),
        )
        note_id = cursor.lastrowid
        row = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
    return public_note(row)


def delete_note(user_id: int | str, note_id: int | str) -> None:
    with auth_connection() as conn:
        conn.execute(
            """
            DELETE FROM notes
            WHERE id = ? AND user_id = ?
            """,
            (str(note_id), str(user_id)),
        )


def delete_all_notes(user_id: int | str) -> None:
    with auth_connection() as conn:
        conn.execute("DELETE FROM notes WHERE user_id = ?", (str(user_id),))


def log_user_request(
    user_id: int | str,
    message: str,
    reply: Optional[str],
    status: str,
    error: Optional[str],
    duration_ms: Optional[int],
    mode: str = "instant",
    model: Optional[str] = None,
) -> None:
    now = int(time.time())
    with auth_connection() as conn:
        conn.execute(
            """
            INSERT INTO user_requests
                (user_id, message, reply, status, error, duration_ms, mode, model, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (str(user_id), message, reply, status, error, duration_ms, mode, model, now),
        )


def list_admin_users() -> list[dict]:
    with auth_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                u.id,
                u.username,
                u.email,
                u.created_at,
                u.updated_at,
                COUNT(DISTINCT n.id) AS notes_count,
                COUNT(DISTINCT ur.id) AS requests_count
            FROM users u
            LEFT JOIN notes n ON n.user_id = u.id
            LEFT JOIN user_requests ur ON ur.user_id = u.id
            GROUP BY u.id
            ORDER BY u.created_at DESC
            """
        ).fetchall()
    return [
        {
            "id": str(row["id"]),
            "username": row["username"],
            "email": row["email"] or "",
            "createdAt": int(row["created_at"]),
            "updatedAt": int(row["updated_at"]),
            "notesCount": int(row["notes_count"]),
            "requestsCount": int(row["requests_count"]),
        }
        for row in rows
    ]


def list_admin_request_logs(limit: int = 100) -> list[dict]:
    safe_limit = max(1, min(int(limit or 100), 500))
    with auth_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                ur.id,
                ur.user_id,
                u.username,
                ur.message,
                ur.reply,
                ur.status,
                ur.error,
                ur.mode,
                ur.model,
                ur.duration_ms,
                ur.created_at
            FROM user_requests ur
            JOIN users u ON u.id = ur.user_id
            ORDER BY ur.created_at DESC, ur.id DESC
            LIMIT ?
            """,
            (safe_limit,),
        ).fetchall()
    return [
        {
            "id": str(row["id"]),
            "userId": str(row["user_id"]),
            "username": row["username"],
            "message": row["message"],
            "reply": row["reply"] or "",
            "status": row["status"],
            "error": row["error"] or "",
            "mode": row["mode"] or "instant",
            "model": row["model"] or "",
            "durationMs": row["duration_ms"],
            "createdAt": int(row["created_at"]),
        }
        for row in rows
    ]


DEFAULT_NOTIFICATION_COINS = [
    ("bitcoin", "BTC"),
    ("ethereum", "ETH"),
    ("solana", "SOL"),
    ("ripple", "XRP"),
    ("binancecoin", "BNB"),
]


def ensure_default_notification_settings(user_id: int | str) -> None:
    now = int(time.time())
    with auth_connection() as conn:
        for coin_id, symbol in DEFAULT_NOTIFICATION_COINS:
            conn.execute(
                """
                INSERT OR IGNORE INTO notification_settings
                    (user_id, coin_id, symbol, threshold_percent, cooldown_minutes, enabled, created_at, updated_at)
                VALUES (?, ?, ?, 3.0, 60, 1, ?, ?)
                """,
                (str(user_id), coin_id, symbol, now, now),
            )


def list_notification_settings(user_id: int | str) -> list[dict]:
    ensure_default_notification_settings(user_id)
    with auth_connection() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM notification_settings
            WHERE user_id = ?
            ORDER BY symbol
            """,
            (str(user_id),),
        ).fetchall()
    return [
        {
            "id": str(row["id"]),
            "coinId": row["coin_id"],
            "symbol": row["symbol"],
            "thresholdPercent": float(row["threshold_percent"]),
            "cooldownMinutes": int(row["cooldown_minutes"]),
            "enabled": bool(row["enabled"]),
            "lastNotifiedAt": row["last_notified_at"],
        }
        for row in rows
    ]


def update_notification_setting(
    user_id: int | str,
    setting_id: int | str,
    enabled: Optional[bool] = None,
    threshold_percent: Optional[float] = None,
    cooldown_minutes: Optional[int] = None,
) -> Optional[dict]:
    fields = []
    values = []
    if enabled is not None:
        fields.append("enabled = ?")
        values.append(1 if enabled else 0)
    if threshold_percent is not None:
        fields.append("threshold_percent = ?")
        values.append(max(0.5, min(float(threshold_percent), 50.0)))
    if cooldown_minutes is not None:
        fields.append("cooldown_minutes = ?")
        values.append(max(5, min(int(cooldown_minutes), 1440)))
    if not fields:
        return None

    fields.append("updated_at = ?")
    values.append(int(time.time()))
    values.extend([str(setting_id), str(user_id)])
    with auth_connection() as conn:
        conn.execute(
            f"""
            UPDATE notification_settings
            SET {", ".join(fields)}
            WHERE id = ? AND user_id = ?
            """,
            tuple(values),
        )
        row = conn.execute(
            "SELECT * FROM notification_settings WHERE id = ? AND user_id = ?",
            (str(setting_id), str(user_id)),
        ).fetchone()
    if not row:
        return None
    return {
        "id": str(row["id"]),
        "coinId": row["coin_id"],
        "symbol": row["symbol"],
        "thresholdPercent": float(row["threshold_percent"]),
        "cooldownMinutes": int(row["cooldown_minutes"]),
        "enabled": bool(row["enabled"]),
        "lastNotifiedAt": row["last_notified_at"],
    }


def active_notification_settings(user_id: int | str) -> list[dict]:
    ensure_default_notification_settings(user_id)
    with auth_connection() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM notification_settings
            WHERE user_id = ? AND enabled = 1
            ORDER BY symbol
            """,
            (str(user_id),),
        ).fetchall()
    return [dict(row) for row in rows]


def create_notification_event(
    user_id: int | str,
    setting_id: int | str,
    coin_id: str,
    symbol: str,
    title: str,
    message: str,
    price_usd: Optional[float],
    change_percent: Optional[float],
) -> dict:
    now = int(time.time())
    with auth_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO notification_events
                (user_id, setting_id, coin_id, symbol, title, message, price_usd, change_percent, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (str(user_id), str(setting_id), coin_id, symbol, title, message, price_usd, change_percent, now),
        )
        conn.execute(
            """
            UPDATE notification_settings
            SET last_notified_at = ?, updated_at = ?
            WHERE id = ? AND user_id = ?
            """,
            (now, now, str(setting_id), str(user_id)),
        )
        row = conn.execute(
            "SELECT * FROM notification_events WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
    return public_notification_event(row)


def public_notification_event(row: sqlite3.Row | dict) -> dict:
    return {
        "id": str(row["id"]),
        "coinId": row["coin_id"],
        "symbol": row["symbol"],
        "title": row["title"],
        "message": row["message"],
        "priceUsd": row["price_usd"],
        "changePercent": row["change_percent"],
        "createdAt": int(row["created_at"]),
        "readAt": row["read_at"],
    }


def list_notification_events(user_id: int | str, limit: int = 30) -> list[dict]:
    safe_limit = max(1, min(int(limit or 30), 100))
    with auth_connection() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM notification_events
            WHERE user_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (str(user_id), safe_limit),
        ).fetchall()
    return [public_notification_event(row) for row in rows]


def unread_notification_count(user_id: int | str) -> int:
    with auth_connection() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM notification_events
            WHERE user_id = ? AND read_at IS NULL
            """,
            (str(user_id),),
        ).fetchone()
    return int(row["count"])


def mark_notifications_read(user_id: int | str) -> None:
    now = int(time.time())
    with auth_connection() as conn:
        conn.execute(
            """
            UPDATE notification_events
            SET read_at = COALESCE(read_at, ?)
            WHERE user_id = ?
            """,
            (now, str(user_id)),
        )
