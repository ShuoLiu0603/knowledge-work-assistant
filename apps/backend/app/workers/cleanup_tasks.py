from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_, select

from app.core.config import get_settings
from app.db.models.external_cleanup_job import ExternalCleanupJob
from app.db.session import SessionLocal, init_db
from app.services.cleanup_service import run_external_cleanup_job
from app.workers.celery_app import (
    RELIABLE_TASK_OPTIONS,
    RETRY_BACKOFF_MAX_SECONDS,
    celery_app,
    task_can_retry,
    task_retry_countdown,
)


@celery_app.task(bind=True, name="process_external_cleanup_job", **RELIABLE_TASK_OPTIONS)
def process_external_cleanup_job(self, job_id: str) -> dict:
    init_db()
    with SessionLocal() as db:
        job = run_external_cleanup_job(db, job_id)
        if job is None:
            return {"job_id": job_id, "status": "missing"}
        if job.status == "failed" and task_can_retry(self):
            error = RuntimeError(job.error_message or f"External cleanup job {job.id} failed")
            raise self.retry(exc=error, countdown=task_retry_countdown(self.request.retries))
        return {
            "job_id": job.id,
            "resource_type": job.resource_type,
            "resource_id": job.resource_id,
            "status": job.status,
            "attempts": job.attempts,
        }


def list_recoverable_external_cleanup_job_ids(
    db,
    *,
    now: datetime | None = None,
    limit: int = get_settings().worker_recovery_batch_size,
) -> list[str]:
    now = now or datetime.now(timezone.utc)
    queued_before = now - timedelta(seconds=get_settings().memory_update_job_recovery_interval_seconds)
    retry_before = now - timedelta(seconds=get_settings().memory_update_job_lease_seconds)
    query = (
        select(ExternalCleanupJob.id)
        .where(
            or_(
                and_(
                    ExternalCleanupJob.status == "processing",
                    or_(
                        ExternalCleanupJob.lease_expires_at.is_(None),
                        ExternalCleanupJob.lease_expires_at <= now,
                    ),
                ),
                and_(
                    ExternalCleanupJob.status == "queued",
                    ExternalCleanupJob.updated_at <= queued_before,
                ),
                and_(
                    ExternalCleanupJob.status == "failed",
                    ExternalCleanupJob.updated_at <= retry_before,
                ),
            )
        )
        .order_by(ExternalCleanupJob.updated_at.asc(), ExternalCleanupJob.id.asc())
        .limit(limit)
    )
    return list(db.scalars(query).all())


@celery_app.task(
    name="recover_stale_external_cleanup_jobs",
    autoretry_for=(Exception,),
    retry_backoff=get_settings().celery_task_retry_backoff_seconds,
    retry_backoff_max=RETRY_BACKOFF_MAX_SECONDS,
    retry_jitter=True,
    **RELIABLE_TASK_OPTIONS,
)
def recover_stale_external_cleanup_jobs() -> dict:
    init_db()
    with SessionLocal() as db:
        job_ids = list_recoverable_external_cleanup_job_ids(db)
    for job_id in job_ids:
        process_external_cleanup_job.delay(job_id)
    return {
        "status": "completed",
        "stale_count": len(job_ids),
        "dispatched_job_ids": job_ids,
    }
