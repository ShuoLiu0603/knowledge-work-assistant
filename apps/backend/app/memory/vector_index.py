from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models.user_memory import UserMemory
from app.db.pgvector import cosine_distance, cosine_similarity, supports_pgvector


@dataclass(frozen=True)
class MemoryVectorHit:
    memory_id: str
    score: float
    payload: dict[str, Any] = field(default_factory=dict)


def is_memory_vector_index_enabled() -> bool:
    return bool(get_settings().memory_vector_index_enabled)


def search_active_memories(
    db: Session,
    user_id: str,
    query_vector: list[float],
    limit: int,
) -> list[MemoryVectorHit]:
    if not is_memory_vector_index_enabled() or limit <= 0 or not query_vector:
        return []

    if supports_pgvector(db):
        return search_pgvector_memories(db, user_id, query_vector, limit)
    return search_local_memories(db, user_id, query_vector, limit)


def search_pgvector_memories(
    db: Session,
    user_id: str,
    query_vector: list[float],
    limit: int,
) -> list[MemoryVectorHit]:
    distance = cosine_distance(UserMemory.embedding, query_vector)
    rows = db.execute(
        active_memory_query(user_id, len(query_vector), get_settings().embedding_model)
        .add_columns((1.0 - distance).label("score"))
        .order_by(distance.asc(), UserMemory.id.asc())
        .limit(limit)
    ).all()
    return [MemoryVectorHit(memory_id=memory.id, score=float(score or 0)) for memory, score in rows]


def search_local_memories(
    db: Session,
    user_id: str,
    query_vector: list[float],
    limit: int,
) -> list[MemoryVectorHit]:
    scored = [
        (cosine_similarity(query_vector, memory.embedding), memory.id)
        for memory in db.scalars(active_memory_query(user_id, len(query_vector), get_settings().embedding_model)).all()
    ]
    ranked = sorted(
        (item for item in scored if item[0] is not None),
        key=lambda item: (-float(item[0]), item[1]),
    )[:limit]
    return [MemoryVectorHit(memory_id=memory_id, score=float(score)) for score, memory_id in ranked]


def active_memory_query(user_id: str, embedding_dimension: int, embedding_model: str):
    now = datetime.now(timezone.utc)
    return select(UserMemory).where(
        UserMemory.user_id == user_id,
        UserMemory.status == "active",
        or_(UserMemory.expires_at.is_(None), UserMemory.expires_at > now),
        UserMemory.embedding.is_not(None),
        UserMemory.embedding_dimension == embedding_dimension,
        UserMemory.embedding_model == embedding_model,
    )
