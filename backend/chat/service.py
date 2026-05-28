from dataclasses import dataclass

from backend.chat.store import (
    can_start_direct_conversation,
    create_message,
    get_or_create_direct_conversation,
    list_conversations,
    list_messages,
    mark_conversation_read,
    user_is_conversation_participant,
)


@dataclass
class ChatError(Exception):
    message: str
    status_code: int = 400


def open_direct_conversation(user_id: int | str, friend_id: int | str) -> dict:
    if not can_start_direct_conversation(user_id, friend_id):
        raise ChatError("You can only start a conversation with a friend", 403)
    return get_or_create_direct_conversation(user_id, friend_id)


def conversation_list(user_id: int | str) -> list[dict]:
    return list_conversations(user_id)


def message_list(user_id: int | str, conversation_id: int | str, limit: int = 50, before: int | None = None) -> list[dict]:
    if not user_is_conversation_participant(user_id, conversation_id):
        raise ChatError("Conversation not found", 404)
    return list_messages(user_id, conversation_id, limit=limit, before=before)


def send_conversation_message(user_id: int | str, conversation_id: int | str, content: str) -> dict:
    if not user_is_conversation_participant(user_id, conversation_id):
        raise ChatError("Conversation not found", 404)

    clean_content = (content or "").strip()
    if not clean_content:
        raise ChatError("Message content is required", 400)
    if len(clean_content) > 5000:
        raise ChatError("Message content is too long", 400)

    return create_message(user_id, conversation_id, clean_content)


def mark_read(user_id: int | str, conversation_id: int | str) -> None:
    if not user_is_conversation_participant(user_id, conversation_id):
        raise ChatError("Conversation not found", 404)
    mark_conversation_read(user_id, conversation_id)
