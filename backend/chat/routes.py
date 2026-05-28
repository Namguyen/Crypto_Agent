from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from backend.auth.dependencies import require_user
from backend.chat.service import (
    ChatError,
    conversation_list,
    mark_read,
    message_list,
    open_direct_conversation,
    send_conversation_message,
)

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


class DirectConversationCreate(BaseModel):
    friendId: str = Field(..., min_length=1)


class MessageCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=5000)


def chat_error_response(error: ChatError) -> JSONResponse:
    return JSONResponse({"error": error.message}, status_code=error.status_code)


@router.get("")
def get_conversations(user=Depends(require_user)):
    return {"conversations": conversation_list(user["id"])}


@router.post("/direct")
def create_direct_conversation(payload: DirectConversationCreate, user=Depends(require_user)):
    try:
        conversation = open_direct_conversation(user["id"], payload.friendId)
    except ChatError as error:
        return chat_error_response(error)
    return {"conversation": conversation}


@router.get("/{conversation_id}/messages")
def get_messages(
    conversation_id: str,
    limit: int = Query(50, ge=1, le=100),
    before: int | None = Query(default=None),
    user=Depends(require_user),
):
    try:
        messages = message_list(user["id"], conversation_id, limit=limit, before=before)
    except ChatError as error:
        return chat_error_response(error)
    return {"messages": messages}


@router.post("/{conversation_id}/messages")
def create_message(conversation_id: str, payload: MessageCreate, user=Depends(require_user)):
    try:
        message = send_conversation_message(user["id"], conversation_id, payload.content)
    except ChatError as error:
        return chat_error_response(error)
    return JSONResponse({"message": message}, status_code=201)


@router.post("/{conversation_id}/read")
def mark_conversation_seen(conversation_id: str, user=Depends(require_user)):
    try:
        mark_read(user["id"], conversation_id)
    except ChatError as error:
        return chat_error_response(error)
    return {"ok": True}
