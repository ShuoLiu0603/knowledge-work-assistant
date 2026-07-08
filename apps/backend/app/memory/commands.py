from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.models.user_memory import UserMemory
from app.memory import events as memory_events
from app.memory import vector_index
from app.memory.types import MemoryEmbedding, MemorySource


def create_memory_row(
    db: Session,
    user_id: str,
    content: str,
    normalized: str,
    content_hash: str,
    category: str,
    source: MemorySource,
    embedding: MemoryEmbedding,
    status: str = "active",
    kind: str = "preference",
    extra_metadata: dict | None = None,
    event_type: str | None = None,
    event_reason: str = "",
) -> UserMemory:
    now = datetime.now(timezone.utc)
    memory = UserMemory(
        user_id=user_id,
        content=content.strip(),
        normalized_content=normalized,
        content_hash=content_hash,
        category=category,
        source_text=source.text,
        source_conversation_id=source.conversation_id,
        source_message_id=source.message_id,
        embedding=embedding.vector,
        embedding_model=embedding.model,
        embedding_dimension=embedding.dimension,
        status=status,
        kind=kind,
        extra_metadata=extra_metadata or {},
        valid_at=now,
        last_touched_at=now,
    )
    db.add(memory)
    db.flush()
    memory_events.record_memory_event(
        db,
        memory,
        event_type or ("pending" if status == "pending" else "create"),
        reason=event_reason,
        new_status=status,
    )
    db.commit()
    db.refresh(memory)
    vector_index.try_sync_memory_vector(memory)
    return memory


def touch_memory(
    db: Session,
    memory: UserMemory,
    *,
    confidence: float,
    sensitivity: str,
    existing_confidence: float,
    auto_promote_confidence: float,
) -> tuple[UserMemory, str]:
    previous_status = memory.status
    memory.touched_count += 1
    memory.last_touched_at = datetime.now(timezone.utc)
    memory.extra_metadata = {
        **(memory.extra_metadata or {}),
        "confidence": max(existing_confidence, confidence),
        "sensitivity": sensitivity,
    }
    reason = "exact content_hash match"
    if memory.status == "pending" and sensitivity == "low" and confidence >= auto_promote_confidence:
        memory.status = "active"
        memory.invalid_at = None
        memory.extra_metadata = {
            **memory.extra_metadata,
            "decision": "auto_activated_from_pending",
        }
        reason = "pending exact match promoted to active"
    db.add(memory)
    db.flush()
    memory_events.record_memory_event(
        db,
        memory,
        "touch",
        reason=reason,
        previous_status=previous_status,
        new_status=memory.status,
    )
    db.commit()
    db.refresh(memory)
    vector_index.try_sync_memory_vector(memory)
    return memory, reason


def soft_delete_memory(db: Session, memory: UserMemory, actor_user_id: str) -> None:
    previous_status = memory.status
    now = datetime.now(timezone.utc)
    memory.status = "deleted"
    memory.invalid_at = now
    memory.last_touched_at = now
    db.add(memory)
    db.flush()
    memory_events.record_memory_event(
        db,
        memory,
        "delete",
        actor_type="user",
        actor_user_id=actor_user_id,
        reason="manual memory delete",
        previous_status=previous_status,
        new_status=memory.status,
    )
    db.commit()
    vector_index.try_delete_memory_vector(memory.id)
