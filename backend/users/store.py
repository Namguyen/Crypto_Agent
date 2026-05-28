from backend.auth.store import auth_connection


def public_user_profile(row) -> dict:
    return {
        "id": str(row["id"]),
        "username": row["username"],
        "name": row["username"],
        "picture": "",
        "provider": "local",
    }


def search_public_users(query: str, current_user_id: int | str, limit: int = 10) -> list[dict]:
    value = (query or "").strip()
    if not value:
        return []

    safe_limit = max(1, min(int(limit or 10), 25))
    pattern = f"%{value}%"
    with auth_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, username
            FROM users
            WHERE id != ?
              AND username LIKE ? COLLATE NOCASE
            ORDER BY
              CASE WHEN username = ? COLLATE NOCASE THEN 0 ELSE 1 END,
              username COLLATE NOCASE
            LIMIT ?
            """,
            (str(current_user_id), pattern, value, safe_limit),
        ).fetchall()
    return [public_user_profile(row) for row in rows]


def get_public_user_profile(user_id: int | str) -> dict | None:
    with auth_connection() as conn:
        row = conn.execute(
            "SELECT id, username FROM users WHERE id = ?",
            (str(user_id),),
        ).fetchone()
    return public_user_profile(row) if row else None
