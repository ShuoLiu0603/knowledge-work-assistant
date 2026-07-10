from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models.user_memory import UserMemoryUpdateJob


def create_memory_update_job(
    db: Session,
    *,
    user_id: str,
    text: str,
    conversation_id: str | None,
    message_id: str | None,
    assistant_text: str,
) -> UserMemoryUpdateJob:
    if message_id:
        existing = db.scalar(
            select(UserMemoryUpdateJob).where(
                UserMemoryUpdateJob.user_id == user_id,
                UserMemoryUpdateJob.message_id == message_id,
            )
        )
        if existing is not None:
            return existing
    job = UserMemoryUpdateJob(
        user_id=user_id,
        conversation_id=conversation_id,
        message_id=message_id,
        user_message=text,
        assistant_message=assistant_text,
        status="queued",
    )
    db.add(job)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if message_id:
            existing = db.scalar(
                select(UserMemoryUpdateJob).where(
                    UserMemoryUpdateJob.user_id == user_id,
                    UserMemoryUpdateJob.message_id == message_id,
                )
            )
            if existing is not None:
                return existing
        raise
    db.refresh(job)
    return job


def dispatch_memory_update_job(job_id: str) -> None:
    from app.workers.memory_tasks import process_memory_update_job

    process_memory_update_job.delay(job_id)


def claim_memory_update_job_dispatch(
    db: Session,
    job_id: str,
    *,
    statuses: tuple[str, ...] = ("queued",),
    redispatch_before: datetime | None = None,
    now: datetime | None = None,
) -> bool:
    now = now or datetime.now(timezone.utc)
    dispatchable = UserMemoryUpdateJob.dispatched_at.is_(None)
    if redispatch_before is not None:
        dispatchable = or_(dispatchable, UserMemoryUpdateJob.dispatched_at <= redispatch_before)
    result = db.execute(
        update(UserMemoryUpdateJob)
        .where(
            UserMemoryUpdateJob.id == job_id,
            UserMemoryUpdateJob.status.in_(statuses),
            dispatchable,
        )
        .values(dispatched_at=now, error_message="", updated_at=now)
        .execution_options(synchronize_session=False)
    )
    db.commit()
    db.expire_all()
    return result.rowcount == 1


def record_memory_update_job_dispatch_failure(
    db: Session,
    job_id: str,
    error: Exception | str,
    *,
    statuses: tuple[str, ...] = ("queued", "processing"),
) -> UserMemoryUpdateJob | None:
    now = datetime.now(timezone.utc)
    db.execute(
        update(UserMemoryUpdateJob)
        .where(
            UserMemoryUpdateJob.id == job_id,
            UserMemoryUpdateJob.status.in_(statuses),
        )
        .values(
            dispatched_at=None,
            error_message=f"worker dispatch failed: {error}",
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    db.commit()
    db.expire_all()
    return db.get(UserMemoryUpdateJob, job_id)
