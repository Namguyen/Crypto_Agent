import hashlib
import os
from typing import Optional

from backend.ai.embeddings import blob_to_vector, embed_text, vector_dot, vector_to_blob
from backend.auth.store import list_notes_for_retrieval, upsert_note_embedding


def note_content_hash(content: str) -> str:
    return hashlib.sha256((content or "").encode("utf-8")).hexdigest()


def min_relevance_score() -> float:
    try:
        return float(os.getenv("NOTE_RETRIEVAL_MIN_SCORE", "0.1"))
    except ValueError:
        return 0.1


def index_note_for_user(user_id: int | str, note_id: int | str, content: str) -> Optional[dict]:
    model, vector = embed_text(content)
    if not model or not vector:
        return None
    upsert_note_embedding(
        user_id=user_id,
        note_id=note_id,
        model=model,
        content_hash=note_content_hash(content),
        dimension=len(vector),
        vector=vector_to_blob(vector),
    )
    return {"model": model, "dimension": len(vector)}


def ensure_note_embedding(row: dict, model: str) -> Optional[list[float]]:
    content = row["content"] or ""
    content_hash = note_content_hash(content)
    dimension = int(row["dimension"] or 0)
    stored_vector = row["vector"]
    stale = (
        not stored_vector
        or row["model"] != model
        or row["content_hash"] != content_hash
        or dimension <= 0
    )

    if stale:
        note_model, note_vector = embed_text(content)
        if not note_model or not note_vector:
            return None
        upsert_note_embedding(
            user_id=row["user_id"],
            note_id=row["id"],
            model=note_model,
            content_hash=content_hash,
            dimension=len(note_vector),
            vector=vector_to_blob(note_vector),
        )
        row["model"] = note_model
        row["dimension"] = len(note_vector)
        row["vector"] = vector_to_blob(note_vector)
        return note_vector if note_model == model else None

    return blob_to_vector(stored_vector, dimension)


def retrieve_user_notes(user_id: int | str, query: str, limit: int = 4) -> list[dict]:
    clean_query = (query or "").strip()
    if not clean_query:
        return []

    query_model, query_vector = embed_text(clean_query)
    if not query_model or not query_vector:
        return []

    scored = []
    for row in list_notes_for_retrieval(user_id):
        note_vector = ensure_note_embedding(row, query_model)
        if not note_vector or len(note_vector) != len(query_vector):
            continue
        score = vector_dot(query_vector, note_vector)
        if score < min_relevance_score():
            continue
        scored.append(
            {
                "id": str(row["id"]),
                "content": row["content"],
                "createdAt": int(row["created_at"]),
                "score": round(float(score), 4),
            }
        )

    safe_limit = max(1, min(int(limit or 4), 10))
    scored.sort(key=lambda item: item["score"], reverse=True)
    return scored[:safe_limit]
