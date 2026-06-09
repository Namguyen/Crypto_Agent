import os
import re
import secrets
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from backend.auth.dependencies import require_user
from backend.auth.store import get_user_by_id
from backend.users.store import (
    get_public_user_profile_by_ref,
    search_public_users,
    update_user_ai_profile,
    update_user_profile,
)

router = APIRouter(prefix="/api/users", tags=["users"])

PROJECT_ROOT = Path(__file__).resolve().parents[2]
UPLOAD_ROOT = Path(os.getenv("UPLOAD_ROOT") or (PROJECT_ROOT / "uploads")).resolve()
PROFILE_PICTURE_DIR = UPLOAD_ROOT / "profile_pictures"
PROFILE_PICTURE_DIR.mkdir(parents=True, exist_ok=True)
MAX_PROFILE_PICTURE_BYTES = 4 * 1024 * 1024
LOCAL_PROFILE_PICTURE_RE = r"/uploads/profile_pictures/[A-Za-z0-9_.-]+"
AI_PROFILE_CHOICES = {
    "experienceLevel": {"", "beginner", "intermediate", "advanced"},
    "communicationStyle": {"", "casual", "direct", "executive", "technical", "teacher"},
    "riskProfile": {"", "conservative", "balanced", "aggressive"},
    "preferredDepth": {"", "short", "normal", "detailed"},
}
AI_PROFILE_TEXT_LIMITS = {
    "goals": 500,
    "favoriteAssets": 240,
}


def local_picture_path(public_url: str) -> Path | None:
    if not public_url.startswith("/uploads/profile_pictures/"):
        return None
    candidate = (UPLOAD_ROOT / public_url.removeprefix("/uploads/")).resolve()
    try:
        candidate.relative_to(UPLOAD_ROOT)
    except ValueError:
        return None
    return candidate


def detect_image_extension(content: bytes, content_type: str) -> str | None:
    value = (content_type or "").split(";", 1)[0].strip().lower()
    signatures = {
        "image/png": (".png", lambda data: data.startswith(b"\x89PNG\r\n\x1a\n")),
        "image/jpeg": (".jpg", lambda data: data.startswith(b"\xff\xd8\xff")),
        "image/webp": (".webp", lambda data: data.startswith(b"RIFF") and data[8:12] == b"WEBP"),
        "image/gif": (".gif", lambda data: data.startswith((b"GIF87a", b"GIF89a"))),
    }
    if value in signatures and signatures[value][1](content):
        return signatures[value][0]
    for extension, matches in signatures.values():
        if matches(content):
            return extension
    return None


def clean_ai_profile(data: dict) -> tuple[dict | None, str | None]:
    profile = {}
    for key, allowed in AI_PROFILE_CHOICES.items():
        value = (data.get(key) or "").strip().lower()
        if value not in allowed:
            return None, f"Invalid AI profile value for {key}"
        profile[key] = value

    for key, limit in AI_PROFILE_TEXT_LIMITS.items():
        value = (data.get(key) or "").strip()
        if len(value) > limit:
            return None, f"AI profile {key} must be {limit} characters or less"
        profile[key] = value

    return profile, None


@router.get("/search")
def search_users(
    username: str = Query(..., min_length=2, max_length=64),
    limit: int = Query(10, ge=1, le=25),
    user=Depends(require_user),
):
    return {"users": search_public_users(username, user["id"], limit)}


@router.patch("/me")
async def update_me(request: Request, user=Depends(require_user)):
    data = await request.json()
    display_name = (
        data.get("displayName")
        if "displayName" in data
        else data.get("name") if "name" in data else user.get("displayName", "")
    )
    bio = data.get("bio") if "bio" in data else user.get("bio", "")
    picture = data.get("picture") if "picture" in data else user.get("picture", "")
    display_name = (display_name or "").strip()
    bio = (bio or "").strip()
    picture = (picture or "").strip()
    ai_profile_data = data.get("aiProfile")
    ai_profile = None

    if len(display_name) > 80:
        return JSONResponse({"error": "Display name must be 80 characters or less"}, status_code=400)
    if len(bio) > 240:
        return JSONResponse({"error": "Bio must be 240 characters or less"}, status_code=400)
    if picture and not (
        re.fullmatch(r"https?://\S{3,500}", picture)
        or re.fullmatch(LOCAL_PROFILE_PICTURE_RE, picture)
    ):
        return JSONResponse({"error": "Picture must be an http(s) URL or uploaded profile picture"}, status_code=400)
    if ai_profile_data is not None:
        if not isinstance(ai_profile_data, dict):
            return JSONResponse({"error": "AI profile must be an object"}, status_code=400)
        ai_profile, error = clean_ai_profile(ai_profile_data)
        if error:
            return JSONResponse({"error": error}, status_code=400)

    update_user_profile(
        user["id"],
        display_name=display_name,
        bio=bio,
        picture=picture,
    )
    if ai_profile is not None:
        update_user_ai_profile(
            user["id"],
            experience_level=ai_profile["experienceLevel"],
            communication_style=ai_profile["communicationStyle"],
            risk_profile=ai_profile["riskProfile"],
            preferred_depth=ai_profile["preferredDepth"],
            goals=ai_profile["goals"],
            favorite_assets=ai_profile["favoriteAssets"],
        )
    return {"user": get_user_by_id(user["id"])}


@router.post("/me/picture")
async def upload_profile_picture(request: Request, user=Depends(require_user)):
    content = await request.body()
    if not content:
        return JSONResponse({"error": "Image file is required"}, status_code=400)
    if len(content) > MAX_PROFILE_PICTURE_BYTES:
        return JSONResponse({"error": "Profile picture must be 4 MB or smaller"}, status_code=413)

    extension = detect_image_extension(content, request.headers.get("content-type", ""))
    if not extension:
        return JSONResponse({"error": "Profile picture must be PNG, JPEG, WebP, or GIF"}, status_code=400)

    current_user = get_user_by_id(user["id"])
    filename = f"user_{user['id']}_{secrets.token_urlsafe(12)}{extension}"
    path = PROFILE_PICTURE_DIR / filename
    path.write_bytes(content)

    old_path = local_picture_path((current_user or {}).get("picture", ""))
    if old_path and old_path.exists() and old_path != path:
        old_path.unlink(missing_ok=True)

    public_url = f"/uploads/profile_pictures/{filename}"
    update_user_profile(
        user["id"],
        display_name=(current_user or {}).get("displayName", ""),
        bio=(current_user or {}).get("bio", ""),
        picture=public_url,
    )
    return {"user": get_user_by_id(user["id"]), "picture": public_url}


@router.get("/{user_ref}")
def public_profile(user_ref: str, user=Depends(require_user)):
    profile = get_public_user_profile_by_ref(user_ref)
    if not profile:
        return JSONResponse({"error": "User not found"}, status_code=404)
    return {"user": profile}
