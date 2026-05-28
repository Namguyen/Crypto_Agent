import sqlite3
import time
from typing import Optional

from backend.auth.store import auth_connection
from backend.social.store import friendship_pair, users_are_friends
from backend.users.store import public_user_profile


def init_chat_db() -> None:
    with auth_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL CHECK(type IN ('direct')),
                direct_user_a_id INTEGER,
                direct_user_b_id INTEGER,
                created_by INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                last_message_at INTEGER,
                UNIQUE(direct_user_a_id, direct_user_b_id),
                FOREIGN KEY(direct_user_a_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY(direct_user_b_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY(created_by) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_conversations_last_message_at
                ON conversations(last_message_at DESC);

            CREATE TABLE IF NOT EXISTS conversation_participants (
                conversation_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                joined_at INTEGER NOT NULL,
                last_read_at INTEGER,
                PRIMARY KEY(conversation_id, user_id),
                FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_conversation_participants_user_id
                ON conversation_participants(user_id);

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL,
                sender_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
                FOREIGN KEY(sender_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_messages_conversation_created_at
                ON messages(conversation_id, created_at DESC, id DESC);
            """
        )


def user_is_conversation_participant(user_id: int | str, conversation_id: int | str) -> bool:
    with auth_connection() as conn:
        row = conn.execute(
            """
            SELECT 1
            FROM conversation_participants
            WHERE user_id = ? AND conversation_id = ?
            """,
            (str(user_id), str(conversation_id)),
        ).fetchone()
    return bool(row)


def get_or_create_direct_conversation(user_id: int | str, friend_id: int | str) -> dict:
    user_a, user_b = friendship_pair(user_id, friend_id)
    now = int(time.time())

    with auth_connection() as conn:
        existing = conn.execute(
            """
            SELECT id
            FROM conversations
            WHERE type = 'direct'
              AND direct_user_a_id = ?
              AND direct_user_b_id = ?
            """,
            (user_a, user_b),
        ).fetchone()

        if existing:
            conversation_id = existing["id"]
        else:
            cursor = conn.execute(
                """
                INSERT INTO conversations
                    (type, direct_user_a_id, direct_user_b_id, created_by, created_at, updated_at)
                VALUES ('direct', ?, ?, ?, ?, ?)
                """,
                (user_a, user_b, str(user_id), now, now),
            )
            conversation_id = cursor.lastrowid
            conn.executemany(
                """
                INSERT INTO conversation_participants (conversation_id, user_id, joined_at)
                VALUES (?, ?, ?)
                """,
                [(conversation_id, user_a, now), (conversation_id, user_b, now)],
            )

    return get_conversation_for_user(user_id, conversation_id)


def get_conversation_for_user(user_id: int | str, conversation_id: int | str) -> Optional[dict]:
    rows = list_conversations(user_id, conversation_id=conversation_id)
    return rows[0] if rows else None


def list_conversations(user_id: int | str, conversation_id: int | str | None = None) -> list[dict]:
    params = [str(user_id), str(user_id)]
    conversation_filter = ""
    if conversation_id is not None:
        conversation_filter = "AND c.id = ?"
        params.append(str(conversation_id))

    with auth_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT
                c.*,
                other_user.id AS other_user_id,
                other_user.username AS other_username,
                last_msg.id AS last_message_id,
                last_msg.content AS last_message_content,
                last_msg.sender_id AS last_message_sender_id,
                last_msg.created_at AS last_message_created_at,
                (
                    SELECT COUNT(*)
                    FROM messages unread_msg
                    WHERE unread_msg.conversation_id = c.id
                      AND unread_msg.sender_id != ?
                      AND unread_msg.created_at > COALESCE(cp.last_read_at, 0)
                ) AS unread_count
            FROM conversation_participants cp
            JOIN conversations c ON c.id = cp.conversation_id
            JOIN conversation_participants other_cp
                ON other_cp.conversation_id = c.id
               AND other_cp.user_id != cp.user_id
            JOIN users other_user ON other_user.id = other_cp.user_id
            LEFT JOIN messages last_msg
                ON last_msg.id = (
                    SELECT id
                    FROM messages
                    WHERE conversation_id = c.id
                    ORDER BY created_at DESC, id DESC
                    LIMIT 1
                )
            WHERE cp.user_id = ?
              AND c.type = 'direct'
              {conversation_filter}
            ORDER BY COALESCE(c.last_message_at, c.updated_at) DESC, c.id DESC
            """,
            tuple(params),
        ).fetchall()

    return [public_conversation(row, current_user_id=user_id) for row in rows]


def create_message(user_id: int | str, conversation_id: int | str, content: str) -> dict:
    now = int(time.time())
    with auth_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO messages (conversation_id, sender_id, content, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (str(conversation_id), str(user_id), content.strip(), now),
        )
        conn.execute(
            """
            UPDATE conversations
            SET last_message_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (now, now, str(conversation_id)),
        )
        row = conn.execute(
            """
            SELECT m.*, u.username AS sender_username
            FROM messages m
            JOIN users u ON u.id = m.sender_id
            WHERE m.id = ?
            """,
            (cursor.lastrowid,),
        ).fetchone()
    return public_message(row, current_user_id=user_id)


def list_messages(
    user_id: int | str,
    conversation_id: int | str,
    limit: int = 50,
    before: Optional[int] = None,
) -> list[dict]:
    safe_limit = max(1, min(int(limit or 50), 100))
    params = [str(conversation_id)]
    before_filter = ""
    if before is not None:
        before_filter = "AND m.created_at < ?"
        params.append(int(before))
    params.append(safe_limit)

    with auth_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT m.*, u.username AS sender_username
            FROM messages m
            JOIN users u ON u.id = m.sender_id
            WHERE m.conversation_id = ?
              {before_filter}
            ORDER BY m.created_at DESC, m.id DESC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()

    return [public_message(row, current_user_id=user_id) for row in reversed(rows)]


def mark_conversation_read(user_id: int | str, conversation_id: int | str) -> None:
    now = int(time.time())
    with auth_connection() as conn:
        conn.execute(
            """
            UPDATE conversation_participants
            SET last_read_at = ?
            WHERE user_id = ? AND conversation_id = ?
            """,
            (now, str(user_id), str(conversation_id)),
        )


def can_start_direct_conversation(user_id: int | str, friend_id: int | str) -> bool:
    return str(user_id) != str(friend_id) and users_are_friends(user_id, friend_id)


def public_conversation(row: sqlite3.Row | dict, current_user_id: int | str) -> dict:
    last_message = None
    if row["last_message_id"]:
        last_message = {
            "id": str(row["last_message_id"]),
            "content": row["last_message_content"],
            "senderId": str(row["last_message_sender_id"]),
            "createdAt": int(row["last_message_created_at"]),
            "isOwn": str(row["last_message_sender_id"]) == str(current_user_id),
        }

    return {
        "id": str(row["id"]),
        "type": row["type"],
        "otherUser": public_user_profile(
            {
                "id": row["other_user_id"],
                "username": row["other_username"],
            }
        ),
        "lastMessage": last_message,
        "lastMessageAt": row["last_message_at"],
        "unreadCount": int(row["unread_count"] or 0),
        "createdAt": int(row["created_at"]),
        "updatedAt": int(row["updated_at"]),
    }


def public_message(row: sqlite3.Row | dict, current_user_id: int | str) -> dict:
    return {
        "id": str(row["id"]),
        "conversationId": str(row["conversation_id"]),
        "sender": {
            "id": str(row["sender_id"]),
            "username": row["sender_username"],
            "name": row["sender_username"],
            "picture": "",
            "provider": "local",
        },
        "senderId": str(row["sender_id"]),
        "content": row["content"],
        "createdAt": int(row["created_at"]),
        "isOwn": str(row["sender_id"]) == str(current_user_id),
    }
