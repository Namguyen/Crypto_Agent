from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from backend.ai.agent import summarize_forum_thread
from backend.auth.dependencies import require_user
from backend.auth.store import create_general_notification_event
from backend.forum.store import (
    clear_topic_summary,
    create_post,
    create_topic,
    forum_reply_notification_recipients,
    get_post,
    get_topic,
    list_posts,
    list_topics,
    save_topic_summary,
    set_post_reaction,
)

router = APIRouter(prefix="/api/forum", tags=["forum"])
ALLOWED_REACTIONS = {"like", "fire", "bull", "bear"}


class TopicCreate(BaseModel):
    title: str = Field(..., min_length=3, max_length=120)
    body: str = Field(..., min_length=1, max_length=5000)


class PostCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=5000)


class ReactionUpdate(BaseModel):
    reaction: str | None = Field(default=None, max_length=16)


def clean_text(value: str) -> str:
    return " ".join((value or "").strip().split())


def topic_payload(topic_id: str, current_user_id: int | str | None = None) -> dict | JSONResponse:
    topic = get_topic(topic_id)
    if not topic:
        return JSONResponse({"error": "Topic not found"}, status_code=404)
    return {"topic": topic, "posts": list_posts(topic_id, current_user_id=current_user_id)}


@router.get("/topics")
def forum_topics(limit: int = Query(50, ge=1, le=100), user=Depends(require_user)):
    return {"topics": list_topics(limit=limit)}


@router.post("/topics")
def forum_topic_create(payload: TopicCreate, user=Depends(require_user)):
    title = clean_text(payload.title)
    body = payload.body.strip()
    if len(title) < 3:
        return JSONResponse({"error": "Title must be at least 3 characters"}, status_code=400)
    topic = create_topic(user["id"], title, body)
    return JSONResponse({"topic": topic}, status_code=201)


@router.get("/topics/{topic_id}")
def forum_topic_detail(topic_id: str, user=Depends(require_user)):
    return topic_payload(topic_id, user["id"])


@router.post("/topics/{topic_id}/posts")
def forum_post_create(topic_id: str, payload: PostCreate, user=Depends(require_user)):
    content = payload.content.strip()
    if not content:
        return JSONResponse({"error": "Reply content is required"}, status_code=400)
    post = create_post(user["id"], topic_id, content)
    if not post:
        return JSONResponse({"error": "Topic not found"}, status_code=404)
    topic = get_topic(topic_id)
    if topic:
        preview = content[:140].strip()
        if len(content) > 140:
            preview += "..."
        for recipient_id in forum_reply_notification_recipients(topic_id, user["id"]):
            create_general_notification_event(
                user_id=recipient_id,
                event_type="forum_reply",
                symbol="FORUM",
                coin_id=f"forum:{topic_id}",
                title=f"New reply: {topic['title']}",
                message=f"{user['username']} replied: {preview}",
                link_url=f"/forum?topic={topic_id}",
            )
    return JSONResponse({"post": post, **topic_payload(topic_id, user["id"])}, status_code=201)


@router.post("/posts/{post_id}/reaction")
def forum_post_reaction(post_id: str, payload: ReactionUpdate, user=Depends(require_user)):
    reaction = (payload.reaction or "").strip().lower()
    if reaction and reaction not in ALLOWED_REACTIONS:
        return JSONResponse({"error": "Unsupported reaction"}, status_code=400)

    updated = set_post_reaction(user["id"], post_id, reaction or None)
    if not updated:
        return JSONResponse({"error": "Post not found"}, status_code=404)
    return {
        "post": get_post(post_id, current_user_id=user["id"]),
        **topic_payload(updated["topic_id"], user["id"]),
    }


@router.post("/topics/{topic_id}/summary")
def forum_topic_summary(topic_id: str, user=Depends(require_user)):
    topic = get_topic(topic_id)
    if not topic:
        return JSONResponse({"error": "Topic not found"}, status_code=404)
    posts = list_posts(topic_id, current_user_id=user["id"])
    try:
        summary, model = summarize_forum_thread(topic, posts)
    except Exception as exc:
        return JSONResponse({"error": f"Could not summarize topic: {exc}"}, status_code=502)
    updated = save_topic_summary(topic_id, summary, model)
    return {"summary": summary, "model": model, "topic": updated or topic}


@router.delete("/topics/{topic_id}/summary")
def forum_topic_summary_clear(topic_id: str, user=Depends(require_user)):
    topic = get_topic(topic_id)
    if not topic:
        return JSONResponse({"error": "Topic not found"}, status_code=404)
    updated = clear_topic_summary(topic_id)
    return {"ok": True, "topic": updated or {**topic, "summary": "", "summaryModel": "", "summaryUpdatedAt": None}}
