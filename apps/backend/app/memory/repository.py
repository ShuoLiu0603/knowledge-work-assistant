from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, not_, or_, select
from sqlalchemy.orm import Session

from app.db.models.user_memory import UserMemory, UserMemoryRecallLog
from app.memory import policy


def list_user_memories(db: Session, user_id: str, status: str | None = None) -> list[UserMemory]:
    query = select(UserMemory).where(UserMemory.user_id == user_id)
    if status:
        query = query.where(UserMemory.status == status)
    else:
        query = query.where(UserMemory.status != "deleted")
    return db.scalars(query.order_by(UserMemory.updated_at.desc(), UserMemory.created_at.desc())).all()


def list_memory_editor_candidates(db: Session, user_id: str, limit: int) -> list[UserMemory]:
    now = datetime.now(timezone.utc)
    return db.scalars(
        select(UserMemory)
        .where(
            UserMemory.user_id == user_id,
            UserMemory.status.in_(("active", "pending")),
            or_(UserMemory.expires_at.is_(None), UserMemory.expires_at > now),
        )
        .order_by(UserMemory.last_touched_at.desc(), UserMemory.updated_at.desc(), UserMemory.created_at.desc())
        .limit(limit)
    ).all()


def list_active_memories(db: Session, user_id: str) -> list[UserMemory]:
    now = datetime.now(timezone.utc)
    return db.scalars(
        select(UserMemory)
        .where(*active_memory_filters(user_id, now))
        .order_by(UserMemory.last_touched_at.desc())
    ).all()


def count_active_memories(db: Session, user_id: str, *, include_profile: bool = True) -> int:
    now = datetime.now(timezone.utc)
    query = select(func.count(UserMemory.id)).where(*active_memory_filters(user_id, now))
    if not include_profile:
        query = query.where(not_(profile_memory_filter()))
    return int(db.scalar(query) or 0)


def list_recent_active_memories(
    db: Session,
    user_id: str,
    *,
    limit: int,
    include_profile: bool = True,
) -> list[UserMemory]:
    if limit <= 0:
        return []
    now = datetime.now(timezone.utc)
    query = select(UserMemory).where(*active_memory_filters(user_id, now))
    if not include_profile:
        query = query.where(not_(profile_memory_filter()))
    return db.scalars(
        query.order_by(UserMemory.last_touched_at.desc(), UserMemory.updated_at.desc(), UserMemory.created_at.desc())
        .limit(limit)
    ).all()


def list_active_memories_by_ids(
    db: Session,
    user_id: str,
    memory_ids: list[str],
    *,
    include_profile: bool = True,
) -> list[UserMemory]:
    if not memory_ids:
        return []
    now = datetime.now(timezone.utc)
    ordered_ids = list(dict.fromkeys(memory_ids))
    query = select(UserMemory).where(
        *active_memory_filters(user_id, now),
        UserMemory.id.in_(ordered_ids),
    )
    if not include_profile:
        query = query.where(not_(profile_memory_filter()))
    rows = db.scalars(query).all()
    rows_by_id = {memory.id: memory for memory in rows}
    return [rows_by_id[memory_id] for memory_id in ordered_ids if memory_id in rows_by_id]


def list_active_profile_memories(db: Session, user_id: str, limit: int = 20) -> list[UserMemory]:
    now = datetime.now(timezone.utc)
    memories = db.scalars(
        select(UserMemory)
        .where(
            *active_memory_filters(user_id, now),
            profile_memory_filter(),
        )
        .order_by(UserMemory.last_touched_at.desc(), UserMemory.updated_at.desc(), UserMemory.created_at.desc())
    ).all()
    profiles = [memory for memory in memories if policy.is_profile_memory(memory)]
    profiles.sort(key=lambda memory: (policy.profile_memory_priority(memory), memory.last_touched_at), reverse=True)
    return profiles[:limit]


def active_memory_filters(user_id: str, now: datetime) -> tuple:
    return (
        UserMemory.user_id == user_id,
        UserMemory.status == "active",
        or_(UserMemory.expires_at.is_(None), UserMemory.expires_at > now),
    )


def profile_memory_filter():
    return or_(
        UserMemory.memory_layer == "profile",
        UserMemory.pinned.is_(True),
        UserMemory.profile_slot != "",
        UserMemory.category.in_(policy.STICKY_MEMORY_CATEGORIES),
        UserMemory.kind.in_(policy.PROFILE_MEMORY_KINDS),
    )


def list_active_memories_by_category(db: Session, user_id: str, category: str) -> list[UserMemory]:
    now = datetime.now(timezone.utc)
    return db.scalars(
        select(UserMemory).where(
            UserMemory.user_id == user_id,
            UserMemory.status == "active",
            UserMemory.category == category,
            or_(UserMemory.expires_at.is_(None), UserMemory.expires_at > now),
        )
    ).all()


def list_active_or_pending_memories_by_category(db: Session, user_id: str, category: str) -> list[UserMemory]:
    now = datetime.now(timezone.utc)
    return db.scalars(
        select(UserMemory).where(
            UserMemory.user_id == user_id,
            UserMemory.status.in_(("active", "pending")),
            UserMemory.category == category,
            or_(UserMemory.expires_at.is_(None), UserMemory.expires_at > now),
        )
    ).all()


def list_active_or_pending_memories_by_canonical_key(db: Session, user_id: str, canonical_key: str) -> list[UserMemory]:
    if not canonical_key:
        return []
    now = datetime.now(timezone.utc)
    return db.scalars(
        select(UserMemory).where(
            UserMemory.user_id == user_id,
            UserMemory.status.in_(("active", "pending")),
            UserMemory.canonical_key == canonical_key,
            or_(UserMemory.expires_at.is_(None), UserMemory.expires_at > now),
        )
    ).all()


def find_exact_memory(
    db: Session,
    user_id: str,
    content_hash: str,
    statuses: set[str],
) -> UserMemory | None:
    return db.scalar(
        select(UserMemory).where(
            UserMemory.user_id == user_id,
            UserMemory.content_hash == content_hash,
            UserMemory.status.in_(statuses),
        )
    )


def get_user_memory(db: Session, user_id: str, memory_id: str) -> UserMemory | None:
    memory = db.get(UserMemory, memory_id)
    if memory is None or memory.user_id != user_id:
        return None
    return memory


def create_recall_log(
    db: Session,
    *,
    user_id: str,
    query: str,
    recall_mode: str,
    requested_limit: int,
    recall_limit: int,
    active_count: int,
    selected_count: int,
    threshold: float | None,
    candidates: list[dict],
    selected_memory_ids: list[str],
    conversation_id: str | None = None,
    message_id: str | None = None,
) -> UserMemoryRecallLog:
    log = UserMemoryRecallLog(
        user_id=user_id,
        conversation_id=conversation_id,
        message_id=message_id,
        query=query,
        recall_mode=recall_mode,
        requested_limit=requested_limit,
        recall_limit=recall_limit,
        active_count=active_count,
        selected_count=selected_count,
        threshold=threshold,
        candidates=candidates,
        selected_memory_ids=selected_memory_ids,
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log
