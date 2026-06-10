from collections import defaultdict

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from backend.auth.dependencies import decode_access_token
from backend.auth.dependencies import require_user
from backend.auth.store import create_general_notification_event
from backend.chat.store import conversation_recipient_ids
from backend.chat.service import (
    ChatError,
    conversation_list,
    mark_read,
    message_list,
    open_direct_conversation,
    send_conversation_message,
)

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


class ConversationSocketManager:
    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)

    async def connect(self, conversation_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections[str(conversation_id)].add(websocket)

    def disconnect(self, conversation_id: str, websocket: WebSocket) -> None:
        sockets = self._connections.get(str(conversation_id))
        if not sockets:
            return
        sockets.discard(websocket)
        if not sockets:
            self._connections.pop(str(conversation_id), None)

    async def broadcast_message(self, conversation_id: str, message: dict) -> None:
        sockets = list(self._connections.get(str(conversation_id), set()))
        stale = []
        for websocket in sockets:
            try:
                await websocket.send_json({"type": "message", "message": message})
            except Exception:
                stale.append(websocket)
        for websocket in stale:
            self.disconnect(conversation_id, websocket)


socket_manager = ConversationSocketManager()


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
async def create_message(conversation_id: str, payload: MessageCreate, user=Depends(require_user)):
    try:
        message = send_conversation_message(user["id"], conversation_id, payload.content)
    except ChatError as error:
        return chat_error_response(error)
    preview = message["content"][:140].strip()
    if len(message["content"]) > 140:
        preview += "..."
    for recipient_id in conversation_recipient_ids(user["id"], conversation_id):
        create_general_notification_event(
            user_id=recipient_id,
            event_type="direct_message",
            symbol="DM",
            coin_id=f"conversation:{conversation_id}",
            title=f"New message from {user['username']}",
            message=preview or "Open Messages to reply.",
            link_url="/?tab=messages",
        )
    await socket_manager.broadcast_message(conversation_id, message)
    return JSONResponse({"message": message}, status_code=201)


@router.post("/{conversation_id}/read")
def mark_conversation_seen(conversation_id: str, user=Depends(require_user)):
    try:
        mark_read(user["id"], conversation_id)
    except ChatError as error:
        return chat_error_response(error)
    return {"ok": True}


@router.websocket("/{conversation_id}/ws")
async def conversation_websocket(websocket: WebSocket, conversation_id: str, token: str = ""):
    try:
        user = decode_access_token(token)
    except Exception:
        await websocket.close(code=1008)
        return

    try:
        message_list(user["id"], conversation_id, limit=1)
    except ChatError:
        await websocket.close(code=1008)
        return

    await socket_manager.connect(conversation_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        socket_manager.disconnect(conversation_id, websocket)
