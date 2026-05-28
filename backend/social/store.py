import sqlite3
import time
from typing import Optional

from backend.auth.store import auth_connection
from backend.users.store import get_public_user_profile, public_user_profile


def friendship_pair(user_a: int | str, user_b: int | str) -> tuple[str, str]:
    first, second = sorted((int(user_a), int(user_b)))
    return str(first), str(second)


def init_social_db() -> None:
    with auth_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS friend_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_user_id INTEGER NOT NULL,
                to_user_id INTEGER NOT NULL,
                message TEXT,
                created_at INTEGER NOT NULL,
                UNIQUE(from_user_id, to_user_id),
                CHECK(from_user_id != to_user_id),
                FOREIGN KEY(from_user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY(to_user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_friend_requests_from_user_id
                ON friend_requests(from_user_id);
            CREATE INDEX IF NOT EXISTS idx_friend_requests_to_user_id
                ON friend_requests(to_user_id);

            CREATE TABLE IF NOT EXISTS friendships (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_a_id INTEGER NOT NULL,
                user_b_id INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                UNIQUE(user_a_id, user_b_id),
                CHECK(user_a_id < user_b_id),
                FOREIGN KEY(user_a_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY(user_b_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_friendships_user_a_id
                ON friendships(user_a_id);
            CREATE INDEX IF NOT EXISTS idx_friendships_user_b_id
                ON friendships(user_b_id);
            """
        )


def users_are_friends(user_a: int | str, user_b: int | str) -> bool:
    first, second = friendship_pair(user_a, user_b)
    with auth_connection() as conn:
        row = conn.execute(
            """
            SELECT 1
            FROM friendships
            WHERE user_a_id = ? AND user_b_id = ?
            """,
            (first, second),
        ).fetchone()
    return bool(row)


def pending_friend_request_between(user_a: int | str, user_b: int | str) -> Optional[sqlite3.Row]:
    with auth_connection() as conn:
        return conn.execute(
            """
            SELECT *
            FROM friend_requests
            WHERE (from_user_id = ? AND to_user_id = ?)
               OR (from_user_id = ? AND to_user_id = ?)
            """,
            (str(user_a), str(user_b), str(user_b), str(user_a)),
        ).fetchone()


def create_friend_request(from_user_id: int | str, to_user_id: int | str, message: str | None) -> dict:
    now = int(time.time())
    clean_message = (message or "").strip() or None
    if clean_message and len(clean_message) > 300:
        clean_message = clean_message[:300]

    with auth_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO friend_requests (from_user_id, to_user_id, message, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (str(from_user_id), str(to_user_id), clean_message, now),
        )
        row = conn.execute(
            """
            SELECT
                fr.*,
                from_user.username AS from_username,
                to_user.username AS to_username
            FROM friend_requests fr
            JOIN users from_user ON from_user.id = fr.from_user_id
            JOIN users to_user ON to_user.id = fr.to_user_id
            WHERE fr.id = ?
            """,
            (cursor.lastrowid,),
        ).fetchone()
    return public_friend_request(row)


def get_friend_request(request_id: int | str) -> Optional[sqlite3.Row]:
    with auth_connection() as conn:
        return conn.execute(
            "SELECT * FROM friend_requests WHERE id = ?",
            (str(request_id),),
        ).fetchone()


def delete_friend_request(request_id: int | str) -> None:
    with auth_connection() as conn:
        conn.execute("DELETE FROM friend_requests WHERE id = ?", (str(request_id),))


def create_friendship(user_a: int | str, user_b: int | str) -> None:
    first, second = friendship_pair(user_a, user_b)
    now = int(time.time())
    with auth_connection() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO friendships (user_a_id, user_b_id, created_at)
            VALUES (?, ?, ?)
            """,
            (first, second, now),
        )


def list_friend_requests(user_id: int | str) -> dict:
    with auth_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                fr.*,
                from_user.username AS from_username,
                to_user.username AS to_username
            FROM friend_requests fr
            JOIN users from_user ON from_user.id = fr.from_user_id
            JOIN users to_user ON to_user.id = fr.to_user_id
            WHERE fr.from_user_id = ? OR fr.to_user_id = ?
            ORDER BY fr.created_at DESC, fr.id DESC
            """,
            (str(user_id), str(user_id)),
        ).fetchall()

    sent = [public_friend_request(row) for row in rows if str(row["from_user_id"]) == str(user_id)]
    received = [public_friend_request(row) for row in rows if str(row["to_user_id"]) == str(user_id)]
    return {"sent": sent, "received": received}


def list_friends(user_id: int | str) -> list[dict]:
    with auth_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                f.id AS friendship_id,
                f.created_at AS friends_since,
                u.id,
                u.username
            FROM friendships f
            JOIN users u ON u.id = CASE
                WHEN f.user_a_id = ? THEN f.user_b_id
                ELSE f.user_a_id
            END
            WHERE f.user_a_id = ? OR f.user_b_id = ?
            ORDER BY u.username COLLATE NOCASE
            """,
            (str(user_id), str(user_id), str(user_id)),
        ).fetchall()

    friends = []
    for row in rows:
        profile = public_user_profile(row)
        profile["friendshipId"] = str(row["friendship_id"])
        profile["friendsSince"] = int(row["friends_since"])
        friends.append(profile)
    return friends


def public_friend_request(row: sqlite3.Row | dict) -> dict:
    return {
        "id": str(row["id"]),
        "from": {
            "id": str(row["from_user_id"]),
            "username": row["from_username"],
            "name": row["from_username"],
            "picture": "",
            "provider": "local",
        },
        "to": {
            "id": str(row["to_user_id"]),
            "username": row["to_username"],
            "name": row["to_username"],
            "picture": "",
            "provider": "local",
        },
        "message": row["message"] or "",
        "createdAt": int(row["created_at"]),
    }


def user_exists(user_id: int | str) -> bool:
    return get_public_user_profile(user_id) is not None
