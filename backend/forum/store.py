import sqlite3
import time
from typing import Optional

from backend.auth.store import auth_connection


def init_forum_db() -> None:
    with auth_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS forum_topics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                summary TEXT,
                summary_model TEXT,
                summary_updated_at INTEGER,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                last_post_at INTEGER NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_forum_topics_last_post_at
                ON forum_topics(last_post_at DESC, id DESC);
            CREATE INDEX IF NOT EXISTS idx_forum_topics_user_id
                ON forum_topics(user_id);

            CREATE TABLE IF NOT EXISTS forum_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                FOREIGN KEY(topic_id) REFERENCES forum_topics(id) ON DELETE CASCADE,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_forum_posts_topic_created_at
                ON forum_posts(topic_id, created_at ASC, id ASC);
            CREATE INDEX IF NOT EXISTS idx_forum_posts_user_id
                ON forum_posts(user_id);
            """
        )


def public_forum_author(row: sqlite3.Row | dict, prefix: str = "author") -> dict:
    display_name = (row[f"{prefix}_display_name"] if f"{prefix}_display_name" in row.keys() else "") or row[f"{prefix}_username"]
    return {
        "id": str(row[f"{prefix}_id"]),
        "username": row[f"{prefix}_username"],
        "displayName": display_name,
        "picture": (row[f"{prefix}_picture"] if f"{prefix}_picture" in row.keys() else "") or "",
    }


def public_topic(row: sqlite3.Row | dict) -> dict:
    summary_updated_at = row["summary_updated_at"] if "summary_updated_at" in row.keys() else None
    return {
        "id": str(row["id"]),
        "title": row["title"],
        "body": row["body"],
        "status": row["status"],
        "author": public_forum_author(row),
        "replyCount": int(row["reply_count"] if "reply_count" in row.keys() else 0),
        "createdAt": int(row["created_at"]),
        "updatedAt": int(row["updated_at"]),
        "lastPostAt": int(row["last_post_at"]),
        "summary": (row["summary"] if "summary" in row.keys() else "") or "",
        "summaryModel": (row["summary_model"] if "summary_model" in row.keys() else "") or "",
        "summaryUpdatedAt": int(summary_updated_at) if summary_updated_at else None,
    }


def public_post(row: sqlite3.Row | dict) -> dict:
    return {
        "id": str(row["id"]),
        "topicId": str(row["topic_id"]),
        "content": row["content"],
        "author": public_forum_author(row),
        "createdAt": int(row["created_at"]),
        "updatedAt": int(row["updated_at"]),
    }


def list_topics(limit: int = 50) -> list[dict]:
    safe_limit = max(1, min(int(limit or 50), 100))
    with auth_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                t.*,
                u.id AS author_id,
                u.username AS author_username,
                u.display_name AS author_display_name,
                u.picture AS author_picture,
                COUNT(p.id) AS reply_count
            FROM forum_topics t
            JOIN users u ON u.id = t.user_id
            LEFT JOIN forum_posts p ON p.topic_id = t.id
            WHERE u.disabled_at IS NULL
            GROUP BY t.id
            ORDER BY t.last_post_at DESC, t.id DESC
            LIMIT ?
            """,
            (safe_limit,),
        ).fetchall()
    return [public_topic(row) for row in rows]


def create_topic(user_id: int | str, title: str, body: str) -> dict:
    now = int(time.time())
    with auth_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO forum_topics (user_id, title, body, created_at, updated_at, last_post_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (str(user_id), title, body, now, now, now),
        )
        topic_id = cursor.lastrowid
    topic = get_topic(topic_id)
    if not topic:
        raise RuntimeError("Topic was not created")
    return topic


def get_topic(topic_id: int | str) -> Optional[dict]:
    with auth_connection() as conn:
        row = conn.execute(
            """
            SELECT
                t.*,
                u.id AS author_id,
                u.username AS author_username,
                u.display_name AS author_display_name,
                u.picture AS author_picture,
                COUNT(p.id) AS reply_count
            FROM forum_topics t
            JOIN users u ON u.id = t.user_id
            LEFT JOIN forum_posts p ON p.topic_id = t.id
            WHERE t.id = ? AND u.disabled_at IS NULL
            GROUP BY t.id
            """,
            (str(topic_id),),
        ).fetchone()
    return public_topic(row) if row else None


def list_posts(topic_id: int | str, limit: int = 100) -> list[dict]:
    safe_limit = max(1, min(int(limit or 100), 250))
    with auth_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                p.*,
                u.id AS author_id,
                u.username AS author_username,
                u.display_name AS author_display_name,
                u.picture AS author_picture
            FROM forum_posts p
            JOIN users u ON u.id = p.user_id
            WHERE p.topic_id = ? AND u.disabled_at IS NULL
            ORDER BY p.created_at ASC, p.id ASC
            LIMIT ?
            """,
            (str(topic_id), safe_limit),
        ).fetchall()
    return [public_post(row) for row in rows]


def create_post(user_id: int | str, topic_id: int | str, content: str) -> Optional[dict]:
    now = int(time.time())
    with auth_connection() as conn:
        topic = conn.execute("SELECT id FROM forum_topics WHERE id = ?", (str(topic_id),)).fetchone()
        if not topic:
            return None
        cursor = conn.execute(
            """
            INSERT INTO forum_posts (topic_id, user_id, content, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (str(topic_id), str(user_id), content, now, now),
        )
        conn.execute(
            """
            UPDATE forum_topics
            SET updated_at = ?,
                last_post_at = ?,
                summary = NULL,
                summary_model = NULL,
                summary_updated_at = NULL
            WHERE id = ?
            """,
            (now, now, str(topic_id)),
        )
        row = conn.execute(
            """
            SELECT
                p.*,
                u.id AS author_id,
                u.username AS author_username,
                u.display_name AS author_display_name,
                u.picture AS author_picture
            FROM forum_posts p
            JOIN users u ON u.id = p.user_id
            WHERE p.id = ?
            """,
            (cursor.lastrowid,),
        ).fetchone()
    return public_post(row) if row else None


def save_topic_summary(topic_id: int | str, summary: str, model: str) -> Optional[dict]:
    now = int(time.time())
    with auth_connection() as conn:
        cursor = conn.execute(
            """
            UPDATE forum_topics
            SET summary = ?,
                summary_model = ?,
                summary_updated_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (summary, model, now, now, str(topic_id)),
        )
        changed = cursor.rowcount
    return get_topic(topic_id) if changed else None
