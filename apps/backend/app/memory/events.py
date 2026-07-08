from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models.user_memory import UserMemory, UserMemoryEvent


def record_memory_event(
    db: Session,
    memory: UserMemory,
    event_type: str,
    *,
    actor_type: str = "system",
    actor_user_id: str | None = None,
    source: str = "memory_service",
    reason: str = "",
    previous_status: str | None = None,
    new_status: str | None = None,
    payload: dict | None = None,
) -> UserMemoryEvent:
    event = UserMemoryEvent(
        user_id=memory.user_id,
        memory_id=memory.id,
        event_type=event_type,
        actor_type=actor_type,
        actor_user_id=actor_user_id,
        source=source,
        reason=reason,
        previous_status=previous_status,
        new_status=new_status or memory.status,
        payload={**memory_snapshot(memory), **(payload or {})},
    )
    db.add(event)
    return event


def memory_snapshot(memory: UserMemory) -> dict:
    return {
        "content": memory.content,
        "content_hash": memory.content_hash,
        "status": memory.status,
        "kind": memory.kind,
        "category": memory.category,
        "source_conversation_id": memory.source_conversation_id,
        "source_message_id": memory.source_message_id,
        "embedding_model": memory.embedding_model,
        "embedding_dimension": memory.embedding_dimension,
        "merge_count": memory.merge_count,
        "touched_count": memory.touched_count,
        "superseded_by_id": memory.superseded_by_id,
        "metadata": memory.extra_metadata or {},
    }
