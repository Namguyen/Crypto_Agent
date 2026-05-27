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
                duration_ms INTEGER,
                created_at INTEGER NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_user_requests_user_id_created_at
                ON user_requests(user_id, created_at DESC);
            """
        )


def normalize_email(email: Optional[str]) -> Optional[str]:
    value = (email or "").strip().lower()
    return value or None


def public_user(row: sqlite3.Row | dict) -> dict:
    return {
        "id": str(row["id"]),
        "username": row["username"],
        "email": row["email"] or "",
        "name": row["username"],
        "picture": "",
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
) -> None:
    now = int(time.time())
    with auth_connection() as conn:
        conn.execute(
            """
            INSERT INTO user_requests
                (user_id, message, reply, status, error, duration_ms, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (str(user_id), message, reply, status, error, duration_ms, now),
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
            "durationMs": row["duration_ms"],
            "createdAt": int(row["created_at"]),
        }
        for row in rows
    ]
