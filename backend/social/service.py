from dataclasses import dataclass

from backend.social.store import (
    create_friend_request,
    create_friendship,
    delete_friend_request,
    get_friend_request,
    list_friend_requests,
    list_friends,
    pending_friend_request_between,
    user_exists,
    users_are_friends,
)
from backend.users.store import get_public_user_profile


@dataclass
class SocialError(Exception):
    message: str
    status_code: int = 400


def send_friend_request(from_user_id: int | str, to_user_id: int | str, message: str | None = None) -> dict:
    if str(from_user_id) == str(to_user_id):
        raise SocialError("You cannot send a friend request to yourself", 400)
    if not user_exists(to_user_id):
        raise SocialError("User not found", 404)
    if users_are_friends(from_user_id, to_user_id):
        raise SocialError("You are already friends", 409)
    if pending_friend_request_between(from_user_id, to_user_id):
        raise SocialError("A friend request is already pending", 409)
    return create_friend_request(from_user_id, to_user_id, message)


def accept_friend_request(user_id: int | str, request_id: int | str) -> dict:
    request = get_friend_request(request_id)
    if not request:
        raise SocialError("Friend request not found", 404)
    if str(request["to_user_id"]) != str(user_id):
        raise SocialError("You cannot accept this friend request", 403)

    create_friendship(request["from_user_id"], request["to_user_id"])
    delete_friend_request(request_id)
    friend = get_public_user_profile(request["from_user_id"])
    if not friend:
        raise SocialError("Friend user no longer exists", 404)
    return friend


def decline_friend_request(user_id: int | str, request_id: int | str) -> None:
    request = get_friend_request(request_id)
    if not request:
        raise SocialError("Friend request not found", 404)
    if str(request["to_user_id"]) != str(user_id):
        raise SocialError("You cannot decline this friend request", 403)
    delete_friend_request(request_id)


def friend_request_payload(user_id: int | str) -> dict:
    return list_friend_requests(user_id)


def friend_list_payload(user_id: int | str) -> list[dict]:
    return list_friends(user_id)
