from fastapi import APIRouter, Depends, Query

from backend.auth.dependencies import require_user
from backend.users.store import search_public_users

router = APIRouter(prefix="/api/users", tags=["users"])


@router.get("/search")
def search_users(
    username: str = Query(..., min_length=2, max_length=64),
    limit: int = Query(10, ge=1, le=25),
    user=Depends(require_user),
):
    return {"users": search_public_users(username, user["id"], limit)}
