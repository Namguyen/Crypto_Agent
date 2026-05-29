from backend.auth.store import auth_connection


def public_user_profile(row) -> dict:
    display_name = row["display_name"] if "display_name" in row.keys() else ""
    picture = row["picture"] if "picture" in row.keys() else ""
    bio = row["bio"] if "bio" in row.keys() else ""
    return {
        "id": str(row["id"]),
        "username": row["username"],
        "name": display_name or row["username"],
        "displayName": display_name or row["username"],
        "bio": bio or "",
        "picture": picture or "",
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
            SELECT id, username, display_name, bio, picture
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
            "SELECT id, username, display_name, bio, picture FROM users WHERE id = ?",
            (str(user_id),),
        ).fetchone()
    return public_user_profile(row) if row else None


def get_public_user_profile_by_ref(user_ref: str) -> dict | None:
    value = (user_ref or "").strip()
    if not value:
        return None

    with auth_connection() as conn:
        row = conn.execute(
            """
            SELECT id, username, display_name, bio, picture
            FROM users
            WHERE id = ?
               OR username = ? COLLATE NOCASE
            """,
            (value, value),
        ).fetchone()
    return public_user_profile(row) if row else None


def update_user_profile(
    user_id: int | str,
    *,
    display_name: str,
    bio: str,
    picture: str,
) -> dict | None:
    with auth_connection() as conn:
        conn.execute(
            """
            UPDATE users
            SET display_name = ?,
                bio = ?,
                picture = ?,
                updated_at = strftime('%s', 'now')
            WHERE id = ?
            """,
            (display_name or None, bio or None, picture or None, str(user_id)),
        )
        row = conn.execute(
            "SELECT id, username, display_name, bio, picture FROM users WHERE id = ?",
            (str(user_id),),
        ).fetchone()
    return public_user_profile(row) if row else None
