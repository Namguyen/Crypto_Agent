from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from backend.auth.dependencies import require_user
from backend.social.service import (
    SocialError,
    accept_friend_request,
    decline_friend_request,
    friend_list_payload,
    friend_request_payload,
    send_friend_request,
)

router = APIRouter(prefix="/api/friends", tags=["friends"])


class FriendRequestCreate(BaseModel):
    to: str = Field(..., min_length=1)
    message: str | None = Field(default=None, max_length=300)


def social_error_response(error: SocialError) -> JSONResponse:
    return JSONResponse({"error": error.message}, status_code=error.status_code)


@router.post("/requests")
def create_request(payload: FriendRequestCreate, user=Depends(require_user)):
    try:
        request = send_friend_request(user["id"], payload.to, payload.message)
    except SocialError as error:
        return social_error_response(error)
    return JSONResponse({"request": request}, status_code=201)


@router.get("/requests")
def get_requests(user=Depends(require_user)):
    return friend_request_payload(user["id"])


@router.post("/requests/{request_id}/accept")
def accept_request(request_id: str, user=Depends(require_user)):
    try:
        friend = accept_friend_request(user["id"], request_id)
    except SocialError as error:
        return social_error_response(error)
    return {"friend": friend}


@router.post("/requests/{request_id}/decline")
def decline_request(request_id: str, user=Depends(require_user)):
    try:
        decline_friend_request(user["id"], request_id)
    except SocialError as error:
        return social_error_response(error)
    return {"ok": True}


@router.get("")
def get_friends(user=Depends(require_user)):
    return {"friends": friend_list_payload(user["id"])}
