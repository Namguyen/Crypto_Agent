import hashlib
import math
import os
import re
import struct
from functools import lru_cache
from typing import Iterable


DEFAULT_EMBEDDING_MODEL = "all-MiniLM-L6-v2"
DEFAULT_EMBEDDING_DIMENSION = 384
HASH_EMBEDDING_MODEL = "local-hash-v1"
TOKEN_RE = re.compile(r"[a-z0-9]+")


def embedding_backend() -> str:
    return os.getenv("NOTE_EMBEDDING_BACKEND", "auto").strip().lower()


def embedding_model_name() -> str:
    return os.getenv("NOTE_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL).strip() or DEFAULT_EMBEDDING_MODEL


@lru_cache(maxsize=1)
def sentence_transformer_model():
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(embedding_model_name())


def normalize_vector(values: Iterable[float]) -> list[float]:
    vector = [float(value) for value in values]
    norm = math.sqrt(sum(value * value for value in vector))
    if not norm:
        return [0.0 for _ in vector]
    return [value / norm for value in vector]


def hash_embedding(text: str, dimension: int = DEFAULT_EMBEDDING_DIMENSION) -> list[float]:
    vector = [0.0] * dimension
    tokens = TOKEN_RE.findall((text or "").lower())
    for token in tokens:
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "little") % dimension
        sign = 1.0 if digest[4] & 1 else -1.0
        vector[index] += sign
    return normalize_vector(vector)


def embed_text(text: str) -> tuple[str, list[float]]:
    backend = embedding_backend()
    if backend == "hash":
        return HASH_EMBEDDING_MODEL, hash_embedding(text)
    if backend == "off":
        return "", []

    try:
        model = sentence_transformer_model()
        encoded = model.encode(text or "", normalize_embeddings=True)
        values = encoded.tolist() if hasattr(encoded, "tolist") else list(encoded)
        return embedding_model_name(), [float(value) for value in values]
    except Exception:
        if backend == "sentence-transformers":
            raise
        return HASH_EMBEDDING_MODEL, hash_embedding(text)


def vector_to_blob(vector: Iterable[float]) -> bytes:
    values = [float(value) for value in vector]
    if not values:
        return b""
    return struct.pack(f"<{len(values)}f", *values)


def blob_to_vector(blob: bytes, dimension: int) -> list[float]:
    if not blob or dimension <= 0:
        return []
    expected_size = dimension * 4
    if len(blob) != expected_size:
        return []
    return list(struct.unpack(f"<{dimension}f", blob))


def vector_dot(left: Iterable[float], right: Iterable[float]) -> float:
    return sum(a * b for a, b in zip(left, right))
