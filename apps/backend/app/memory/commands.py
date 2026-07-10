from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models.user_memory import UserMemory
from app.memory import events as memory_events
from app.memory import policy
from app.memory import vector_index
from app.memory.types import MemoryEmbedding, MemorySource

VECTOR_SYNC_SESSION_KEY = "memory_vector_sync_ids"


def queue_memory_vector_sync(db: Session, *memories: UserMemory) -> None:
    memory_ids = db.info.setdefault(VECTOR_SYNC_SESSION_KEY, set())
    for memory in memories:
        if memory.id:
            memory_ids.add(memory.id)


def pop_queued_memory_vector_sync_ids(db: Session) -> set[str]:
    return set(db.info.pop(VECTOR_SYNC_SESSION_KEY, set()))


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
    canonical_key: str | None = None,
    memory_layer: str | None = None,
    profile_slot: str | None = None,
    scope_type: str = "user",
    scope_id: str | None = None,
    pinned: bool | None = None,
    expires_at: datetime | None = None,
    enforce_profile_singleton: bool = True,
    enforce_canonical_key_conflicts: bool = True,
    retry_singleton_conflict: bool = True,
    event_type: str | None = None,
    event_reason: str = "",
    autocommit: bool = True,
) -> UserMemory:
    now = datetime.now(timezone.utc)
    metadata = extra_metadata or {}
    layer = memory_layer or policy.memory_layer_for_fields(kind, category, metadata)
    slot = profile_slot if profile_slot is not None else policy.profile_slot_for_fields(kind, category)
    explicit_canonical_key = policy.normalize_canonical_key(canonical_key or metadata.get("canonical_key", ""))
    resolved_canonical_key = (
        explicit_canonical_key
        or policy.canonical_key_for_profile_slot(slot)
        or policy.canonical_key_for_fields(
            kind=kind,
            category=category,
            normalized_content=normalized,
        )
    )
    scope = policy.memory_scope_id(user_id, scope_id)
    is_pinned = policy.pinned_for_layer(layer) if pinned is None else pinned
    conflicts = dedupe_memory_rows(
        [
            *(
                list_active_profile_singleton_conflicts(
                    db,
                    user_id=user_id,
                    scope_type=scope_type,
                    scope_id=scope,
                    profile_slot=slot,
                    status=status,
                    memory_layer=layer,
                )
                if enforce_profile_singleton
                else []
            ),
            *(
                list_active_canonical_key_conflicts(
                    db,
                    user_id=user_id,
                    scope_type=scope_type,
                    scope_id=scope,
                    canonical_key=resolved_canonical_key,
                    status=status,
                )
                if enforce_canonical_key_conflicts
                else []
            ),
        ]
    )
    for conflict in conflicts:
        conflict.status = "superseded"
        conflict.invalid_at = now
        conflict.last_touched_at = now
        conflict.revision += 1
        db.add(conflict)
    if conflicts:
        db.flush()

    memory = UserMemory(
        user_id=user_id,
        content=content.strip(),
        normalized_content=normalized,
        content_hash=content_hash,
        category=category,
        memory_layer=layer,
        profile_slot=slot,
        scope_type=scope_type,
        scope_id=scope,
        pinned=is_pinned,
        revision=1,
        expires_at=expires_at,
        source_text=source.text,
        source_conversation_id=source.conversation_id,
        source_message_id=source.message_id,
        embedding=embedding.vector,
        embedding_model=embedding.model,
        embedding_dimension=embedding.dimension,
        status=status,
        kind=kind,
        extra_metadata={
            **metadata,
            "canonical_key": resolved_canonical_key,
            "memory_layer": layer,
            "profile_slot": slot,
        },
        canonical_key=resolved_canonical_key,
        valid_at=now,
        last_touched_at=now,
    )
    db.add(memory)
    try:
        db.flush()
    except IntegrityError:
        if not autocommit:
            raise
        db.rollback()
        if not should_retry_active_memory_conflict(
            retry_singleton_conflict=retry_singleton_conflict,
            enforce_profile_singleton=enforce_profile_singleton,
            enforce_canonical_key_conflicts=enforce_canonical_key_conflicts,
            status=status,
            memory_layer=layer,
            profile_slot=slot,
            canonical_key=resolved_canonical_key,
        ):
            raise
        return create_memory_row(
            db,
            user_id,
            content,
            normalized,
            content_hash,
            category,
            source,
            embedding,
            status=status,
            kind=kind,
            extra_metadata=extra_metadata,
            canonical_key=canonical_key,
            memory_layer=memory_layer,
            profile_slot=profile_slot,
            scope_type=scope_type,
            scope_id=scope_id,
            pinned=pinned,
            expires_at=expires_at,
            enforce_profile_singleton=enforce_profile_singleton,
            enforce_canonical_key_conflicts=enforce_canonical_key_conflicts,
            retry_singleton_conflict=False,
            event_type=event_type,
            event_reason=event_reason,
            autocommit=autocommit,
        )
    for conflict in conflicts:
        previous_status = "active"
        conflict.superseded_by_id = memory.id
        db.add(conflict)
        memory_events.record_memory_event(
            db,
            conflict,
            "supersede",
            reason=f"active memory superseded by {memory.id}",
            previous_status=previous_status,
            new_status=conflict.status,
            payload={"superseded_by_id": memory.id},
        )
    memory_events.record_memory_event(
        db,
        memory,
        event_type or ("pending" if status == "pending" else "create"),
        reason=event_reason,
        new_status=status,
    )
    if not autocommit:
        queue_memory_vector_sync(db, memory, *conflicts)
        return memory
    db.commit()
    db.refresh(memory)
    for conflict in conflicts:
        vector_index.try_sync_memory_vector(conflict)
    vector_index.try_sync_memory_vector(memory)
    return memory


def should_retry_active_memory_conflict(
    *,
    retry_singleton_conflict: bool,
    enforce_profile_singleton: bool,
    enforce_canonical_key_conflicts: bool,
    status: str,
    memory_layer: str,
    profile_slot: str,
    canonical_key: str,
) -> bool:
    profile_retry = (
        retry_singleton_conflict
        and enforce_profile_singleton
        and status == "active"
        and memory_layer == "profile"
        and policy.is_profile_singleton_slot(profile_slot)
    )
    canonical_retry = (
        retry_singleton_conflict
        and enforce_canonical_key_conflicts
        and status == "active"
        and bool(canonical_key)
    )
    return profile_retry or canonical_retry


def list_active_profile_singleton_conflicts(
    db: Session,
    *,
    user_id: str,
    scope_type: str,
    scope_id: str,
    profile_slot: str,
    status: str,
    memory_layer: str,
) -> list[UserMemory]:
    if status != "active":
        return []
    if memory_layer != "profile" or not policy.is_profile_singleton_slot(profile_slot):
        return []
    return db.scalars(
        select(UserMemory).where(
            UserMemory.user_id == user_id,
            UserMemory.status == "active",
            UserMemory.memory_layer == "profile",
            UserMemory.profile_slot == profile_slot,
            UserMemory.scope_type == scope_type,
            UserMemory.scope_id == scope_id,
        )
    ).all()


def list_active_canonical_key_conflicts(
    db: Session,
    *,
    user_id: str,
    scope_type: str,
    scope_id: str,
    canonical_key: str,
    status: str,
) -> list[UserMemory]:
    if status != "active" or not canonical_key:
        return []
    return db.scalars(
        select(UserMemory).where(
            UserMemory.user_id == user_id,
            UserMemory.status == "active",
            UserMemory.scope_type == scope_type,
            UserMemory.scope_id == scope_id,
            UserMemory.canonical_key == canonical_key,
        )
    ).all()


def dedupe_memory_rows(memories: list[UserMemory]) -> list[UserMemory]:
    seen = set()
    result = []
    for memory in memories:
        if memory.id in seen:
            continue
        seen.add(memory.id)
        result.append(memory)
    return result


def touch_memory(
    db: Session,
    memory: UserMemory,
    *,
    sensitivity: str,
    activate_pending: bool = False,
    autocommit: bool = True,
) -> tuple[UserMemory, str]:
    previous_status = memory.status
    conflicts: list[UserMemory] = []
    memory.touched_count += 1
    memory.revision += 1
    memory.last_touched_at = datetime.now(timezone.utc)
    memory.extra_metadata = {
        **(memory.extra_metadata or {}),
        "sensitivity": sensitivity,
        "canonical_key": memory.canonical_key,
        "memory_layer": memory.memory_layer,
        "profile_slot": memory.profile_slot,
    }
    reason = "exact content_hash match"
    if memory.status == "pending" and sensitivity == "low" and activate_pending:
        conflicts = supersede_activation_conflicts(
            db,
            memory,
            reason=f"pending memory {memory.id} activated and superseded conflicting active memory",
        )
        memory.status = "active"
        memory.invalid_at = None
        memory.extra_metadata = {
            **memory.extra_metadata,
            "decision": "auto_activated_from_pending",
            "superseded_conflict_ids": [conflict.id for conflict in conflicts],
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
    if not autocommit:
        queue_memory_vector_sync(db, memory, *conflicts)
        return memory, reason
    db.commit()
    db.refresh(memory)
    for conflict in conflicts:
        vector_index.try_sync_memory_vector(conflict)
    vector_index.try_sync_memory_vector(memory)
    return memory, reason


def supersede_activation_conflicts(
    db: Session,
    memory: UserMemory,
    *,
    actor_type: str = "system",
    actor_user_id: str | None = None,
    source: str = "memory_service",
    reason: str,
    payload: dict | None = None,
) -> list[UserMemory]:
    conflicts = list_activation_conflicts_for_existing_memory(db, memory)
    if not conflicts:
        return []

    now = datetime.now(timezone.utc)
    for conflict in conflicts:
        previous_status = conflict.status
        conflict.status = "superseded"
        conflict.superseded_by_id = memory.id
        conflict.invalid_at = now
        conflict.last_touched_at = now
        conflict.revision += 1
        db.add(conflict)
        memory_events.record_memory_event(
            db,
            conflict,
            "supersede",
            actor_type=actor_type,
            actor_user_id=actor_user_id,
            source=source,
            reason=reason,
            previous_status=previous_status,
            new_status=conflict.status,
            payload={**(payload or {}), "superseded_by_id": memory.id},
        )
    db.flush(conflicts)
    return conflicts


def list_activation_conflicts_for_existing_memory(db: Session, memory: UserMemory) -> list[UserMemory]:
    rows: list[UserMemory] = []
    scope_type = memory.scope_type or "user"
    scope_id = memory.scope_id or memory.user_id
    with db.no_autoflush:
        rows.extend(
            row
            for row in list_active_canonical_key_conflicts(
                db,
                user_id=memory.user_id,
                scope_type=scope_type,
                scope_id=scope_id,
                canonical_key=memory.canonical_key,
                status="active",
            )
            if row.id != memory.id
        )
        if memory.memory_layer == "profile" and policy.is_profile_singleton_slot(memory.profile_slot):
            rows.extend(
                row
                for row in list_active_profile_singleton_conflicts(
                    db,
                    user_id=memory.user_id,
                    scope_type=scope_type,
                    scope_id=scope_id,
                    profile_slot=memory.profile_slot,
                    status="active",
                    memory_layer=memory.memory_layer,
                )
                if row.id != memory.id
            )
        if memory.content_hash:
            rows.extend(
                db.scalars(
                    select(UserMemory).where(
                        UserMemory.user_id == memory.user_id,
                        UserMemory.status == "active",
                        UserMemory.id != memory.id,
                        UserMemory.content_hash == memory.content_hash,
                    )
                ).all()
            )
    return dedupe_memory_rows(rows)


def soft_delete_memory(db: Session, memory: UserMemory, actor_user_id: str) -> None:
    previous_status = memory.status
    now = datetime.now(timezone.utc)
    memory.status = "deleted"
    memory.invalid_at = now
    memory.last_touched_at = now
    memory.revision += 1
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
