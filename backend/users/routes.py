import re

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from backend.auth.dependencies import require_user
from backend.auth.store import get_user_by_id
from backend.users.store import search_public_users, update_user_profile

router = APIRouter(prefix="/api/users", tags=["users"])


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
    display_name = (data.get("displayName") or data.get("name") or "").strip()
    bio = (data.get("bio") or "").strip()
    picture = (data.get("picture") or "").strip()

    if len(display_name) > 80:
        return JSONResponse({"error": "Display name must be 80 characters or less"}, status_code=400)
    if len(bio) > 240:
        return JSONResponse({"error": "Bio must be 240 characters or less"}, status_code=400)
    if picture and not re.fullmatch(r"https?://\S{3,500}", picture):
        return JSONResponse({"error": "Picture must be an http or https URL"}, status_code=400)

    update_user_profile(
        user["id"],
        display_name=display_name,
        bio=bio,
        picture=picture,
    )
    return {"user": get_user_by_id(user["id"])}
