from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.user_memory import UserMemory, UserMemoryRecallLog


def list_user_memories(db: Session, user_id: str, status: str | None = None) -> list[UserMemory]:
    query = select(UserMemory).where(UserMemory.user_id == user_id)
    if status:
        query = query.where(UserMemory.status == status)
    else:
        query = query.where(UserMemory.status != "deleted")
    return db.scalars(query.order_by(UserMemory.updated_at.desc(), UserMemory.created_at.desc())).all()


def list_memory_editor_candidates(db: Session, user_id: str, limit: int) -> list[UserMemory]:
    return db.scalars(
        select(UserMemory)
        .where(UserMemory.user_id == user_id, UserMemory.status.in_(("active", "pending")))
        .order_by(UserMemory.last_touched_at.desc(), UserMemory.updated_at.desc(), UserMemory.created_at.desc())
        .limit(limit)
    ).all()


def list_active_memories(db: Session, user_id: str) -> list[UserMemory]:
    return db.scalars(
        select(UserMemory)
        .where(UserMemory.user_id == user_id, UserMemory.status == "active")
        .order_by(UserMemory.last_touched_at.desc())
    ).all()


def list_active_memories_by_category(db: Session, user_id: str, category: str) -> list[UserMemory]:
    return db.scalars(
        select(UserMemory).where(
            UserMemory.user_id == user_id,
            UserMemory.status == "active",
            UserMemory.category == category,
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
