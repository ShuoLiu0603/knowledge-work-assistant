from __future__ import annotations

from datetime import datetime, timezone

from app.db.models.user_memory import UserMemoryUpdateJob
from app.db.session import SessionLocal, init_db
from app.services.memory_service import process_user_memory, to_memory_action_dict
from app.workers.celery_app import celery_app


@celery_app.task(name="process_memory_update_job")
def process_memory_update_job(job_id: str) -> dict:
    init_db()
    with SessionLocal() as db:
        job = db.get(UserMemoryUpdateJob, job_id)
        if job is None:
            return {"job_id": job_id, "status": "missing"}
        if job.status == "completed":
            return {"job_id": job.id, "status": job.status, "action_count": len(job.actions or [])}

        job.status = "processing"
        job.attempts += 1
        job.error_message = ""
        job.updated_at = datetime.now(timezone.utc)
        db.add(job)
        db.commit()

        try:
            actions = process_user_memory(
                db,
                job.user_id,
                job.user_message,
                conversation_id=job.conversation_id,
                message_id=job.message_id,
                assistant_text=job.assistant_message,
            )
            action_dicts = [to_memory_action_dict(action) for action in actions]
            job.status = "completed"
            job.actions = action_dicts
            job.error_message = ""
            job.updated_at = datetime.now(timezone.utc)
            db.add(job)
            db.commit()
            return {
                "job_id": job.id,
                "status": job.status,
                "action_count": len(action_dicts),
                "actions": action_dicts,
            }
        except Exception as exc:
            db.rollback()
            job = db.get(UserMemoryUpdateJob, job_id)
            if job is None:
                return {"job_id": job_id, "status": "missing_after_failure", "error_message": str(exc)}
            job.status = "failed"
            job.error_message = str(exc)
            job.updated_at = datetime.now(timezone.utc)
            db.add(job)
            db.commit()
            return {"job_id": job.id, "status": job.status, "error_message": job.error_message}
