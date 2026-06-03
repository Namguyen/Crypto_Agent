import json
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
                is_admin INTEGER NOT NULL DEFAULT 0,
                disabled_at INTEGER,
                disabled_reason TEXT,
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

            CREATE TABLE IF NOT EXISTS note_embeddings (
                note_id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                model TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                dimension INTEGER NOT NULL,
                vector BLOB NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                FOREIGN KEY(note_id) REFERENCES notes(id) ON DELETE CASCADE,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_note_embeddings_user_id
                ON note_embeddings(user_id);

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

            CREATE TABLE IF NOT EXISTS admin_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_user_id INTEGER,
                target_user_id INTEGER,
                action TEXT NOT NULL,
                details TEXT,
                created_at INTEGER NOT NULL,
                FOREIGN KEY(admin_user_id) REFERENCES users(id) ON DELETE SET NULL,
                FOREIGN KEY(target_user_id) REFERENCES users(id) ON DELETE SET NULL
            );

            CREATE INDEX IF NOT EXISTS idx_admin_actions_created_at
                ON admin_actions(created_at DESC);
            """
        )
        ensure_column(conn, "users", "display_name", "TEXT")
        ensure_column(conn, "users", "bio", "TEXT")
        ensure_column(conn, "users", "picture", "TEXT")
        ensure_column(conn, "users", "is_admin", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(conn, "users", "disabled_at", "INTEGER")
        ensure_column(conn, "users", "disabled_reason", "TEXT")
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
    disabled_at = row["disabled_at"] if "disabled_at" in row.keys() else None
    return {
        "id": str(row["id"]),
        "username": row["username"],
        "email": row["email"] or "",
        "name": display_name,
        "displayName": display_name,
        "bio": bio,
        "picture": picture,
        "isAdmin": bool(row["is_admin"] if "is_admin" in row.keys() else 0),
        "disabledAt": int(disabled_at) if disabled_at else None,
        "disabledReason": (row["disabled_reason"] if "disabled_reason" in row.keys() else "") or "",
        "email_verified": bool(row["email"]),
        "provider": "local",
    }


def create_user(username: str, email: Optional[str], password: str, is_admin: bool = False) -> dict:
    now = int(time.time())
    try:
        with auth_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO users (username, email, password_hash, is_admin, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    username.strip(),
                    normalize_email(email),
                    generate_password_hash(password),
                    1 if is_admin else 0,
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


def upsert_env_user(
    username: str,
    password: str,
    email: Optional[str] = None,
    is_admin: bool = False,
) -> dict:
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
                    is_admin = ?,
                    disabled_at = NULL,
                    disabled_reason = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    normalized_email,
                    generate_password_hash(password),
                    1 if is_admin else 0,
                    now,
                    existing["id"],
                ),
            )
        return get_user_by_id(existing["id"])
    return create_user(username, normalized_email, password, is_admin=is_admin)


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


def list_notes_for_retrieval(user_id: int | str) -> list[dict]:
    with auth_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                n.id,
                n.user_id,
                n.content,
                n.created_at,
                ne.model,
                ne.content_hash,
                ne.dimension,
                ne.vector
            FROM notes n
            LEFT JOIN note_embeddings ne ON ne.note_id = n.id AND ne.user_id = n.user_id
            WHERE n.user_id = ?
            ORDER BY n.created_at DESC, n.id DESC
            """,
            (str(user_id),),
        ).fetchall()
    return [dict(row) for row in rows]


def upsert_note_embedding(
    user_id: int | str,
    note_id: int | str,
    model: str,
    content_hash: str,
    dimension: int,
    vector: bytes,
) -> None:
    now = int(time.time())
    with auth_connection() as conn:
        conn.execute(
            """
            INSERT INTO note_embeddings
                (note_id, user_id, model, content_hash, dimension, vector, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(note_id) DO UPDATE SET
                user_id = excluded.user_id,
                model = excluded.model,
                content_hash = excluded.content_hash,
                dimension = excluded.dimension,
                vector = excluded.vector,
                updated_at = excluded.updated_at
            """,
            (str(note_id), str(user_id), model, content_hash, int(dimension), vector, now, now),
        )


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


def admin_user_summary(row: sqlite3.Row | dict) -> dict:
    disabled_at = row["disabled_at"] if "disabled_at" in row.keys() else None
    return {
        "id": str(row["id"]),
        "username": row["username"],
        "email": row["email"] or "",
        "displayName": (row["display_name"] if "display_name" in row.keys() else "") or row["username"],
        "isAdmin": bool(row["is_admin"] if "is_admin" in row.keys() else 0),
        "disabledAt": int(disabled_at) if disabled_at else None,
        "disabledReason": (row["disabled_reason"] if "disabled_reason" in row.keys() else "") or "",
        "createdAt": int(row["created_at"]),
        "updatedAt": int(row["updated_at"]),
        "notesCount": int(row["notes_count"] if "notes_count" in row.keys() else 0),
        "requestsCount": int(row["requests_count"] if "requests_count" in row.keys() else 0),
        "friendsCount": int(row["friends_count"] if "friends_count" in row.keys() else 0),
        "messagesCount": int(row["messages_count"] if "messages_count" in row.keys() else 0),
    }


def get_admin_user(user_id: int | str) -> Optional[dict]:
    with auth_connection() as conn:
        row = conn.execute(
            """
            SELECT
                u.*,
                (SELECT COUNT(*) FROM notes n WHERE n.user_id = u.id) AS notes_count,
                (SELECT COUNT(*) FROM user_requests ur WHERE ur.user_id = u.id) AS requests_count,
                (SELECT COUNT(*) FROM friendships f WHERE f.user_a_id = u.id OR f.user_b_id = u.id) AS friends_count,
                (SELECT COUNT(*) FROM messages m WHERE m.sender_id = u.id) AS messages_count
            FROM users u
            WHERE u.id = ?
            """,
            (str(user_id),),
        ).fetchone()
    return admin_user_summary(row) if row else None


def list_admin_users() -> list[dict]:
    with auth_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                u.*,
                (SELECT COUNT(*) FROM notes n WHERE n.user_id = u.id) AS notes_count,
                (SELECT COUNT(*) FROM user_requests ur WHERE ur.user_id = u.id) AS requests_count,
                (SELECT COUNT(*) FROM friendships f WHERE f.user_a_id = u.id OR f.user_b_id = u.id) AS friends_count,
                (SELECT COUNT(*) FROM messages m WHERE m.sender_id = u.id) AS messages_count
            FROM users u
            ORDER BY u.created_at DESC
            """
        ).fetchall()
    return [admin_user_summary(row) for row in rows]


def log_admin_action(
    admin_user_id: int | str | None,
    target_user_id: int | str | None,
    action: str,
    details: dict | str | None = None,
) -> dict:
    now = int(time.time())
    if isinstance(details, dict):
        details_value = json.dumps(details, sort_keys=True)
    else:
        details_value = details or ""
    with auth_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO admin_actions (admin_user_id, target_user_id, action, details, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                str(admin_user_id) if admin_user_id is not None else None,
                str(target_user_id) if target_user_id is not None else None,
                action,
                details_value,
                now,
            ),
        )
        row = conn.execute(
            """
            SELECT
                aa.*,
                admin.username AS admin_username,
                target.username AS target_username
            FROM admin_actions aa
            LEFT JOIN users admin ON admin.id = aa.admin_user_id
            LEFT JOIN users target ON target.id = aa.target_user_id
            WHERE aa.id = ?
            """,
            (cursor.lastrowid,),
        ).fetchone()
    return public_admin_action(row)


def public_admin_action(row: sqlite3.Row | dict) -> dict:
    return {
        "id": str(row["id"]),
        "adminUserId": str(row["admin_user_id"]) if row["admin_user_id"] else "",
        "adminUsername": row["admin_username"] or "deleted-admin",
        "targetUserId": str(row["target_user_id"]) if row["target_user_id"] else "",
        "targetUsername": row["target_username"] or "",
        "action": row["action"],
        "details": row["details"] or "",
        "createdAt": int(row["created_at"]),
    }


def list_admin_actions(limit: int = 100) -> list[dict]:
    safe_limit = max(1, min(int(limit or 100), 500))
    with auth_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                aa.*,
                admin.username AS admin_username,
                target.username AS target_username
            FROM admin_actions aa
            LEFT JOIN users admin ON admin.id = aa.admin_user_id
            LEFT JOIN users target ON target.id = aa.target_user_id
            ORDER BY aa.created_at DESC, aa.id DESC
            LIMIT ?
            """,
            (safe_limit,),
        ).fetchall()
    return [public_admin_action(row) for row in rows]


def revoke_refresh_tokens_for_user(user_id: int | str) -> int:
    now = int(time.time())
    with auth_connection() as conn:
        cursor = conn.execute(
            """
            UPDATE refresh_tokens
            SET revoked_at = COALESCE(revoked_at, ?)
            WHERE user_id = ? AND revoked_at IS NULL
            """,
            (now, str(user_id)),
        )
    return cursor.rowcount


def reset_user_password(user_id: int | str, password: str) -> Optional[dict]:
    now = int(time.time())
    with auth_connection() as conn:
        cursor = conn.execute(
            """
            UPDATE users
            SET password_hash = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (generate_password_hash(password), now, str(user_id)),
        )
    if cursor.rowcount < 1:
        return None
    revoke_refresh_tokens_for_user(user_id)
    return get_admin_user(user_id)


def suspend_user(user_id: int | str, reason: str = "") -> Optional[dict]:
    now = int(time.time())
    clean_reason = (reason or "").strip()[:500]
    with auth_connection() as conn:
        cursor = conn.execute(
            """
            UPDATE users
            SET disabled_at = COALESCE(disabled_at, ?),
                disabled_reason = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (now, clean_reason or None, now, str(user_id)),
        )
    if cursor.rowcount < 1:
        return None
    revoke_refresh_tokens_for_user(user_id)
    return get_admin_user(user_id)


def unsuspend_user(user_id: int | str) -> Optional[dict]:
    now = int(time.time())
    with auth_connection() as conn:
        cursor = conn.execute(
            """
            UPDATE users
            SET disabled_at = NULL,
                disabled_reason = NULL,
                updated_at = ?
            WHERE id = ?
            """,
            (now, str(user_id)),
        )
    return get_admin_user(user_id) if cursor.rowcount else None


def delete_user_by_id(user_id: int | str) -> Optional[dict]:
    target = get_admin_user(user_id)
    if not target:
        return None
    with auth_connection() as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (str(user_id),))
    return target


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
