from __future__ import annotations

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
    job = UserMemoryUpdateJob(
        user_id=user_id,
        conversation_id=conversation_id,
        message_id=message_id,
        user_message=text,
        assistant_message=assistant_text,
        status="queued",
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def dispatch_memory_update_job(job_id: str) -> None:
    from app.workers.memory_tasks import process_memory_update_job

    process_memory_update_job.delay(job_id)
