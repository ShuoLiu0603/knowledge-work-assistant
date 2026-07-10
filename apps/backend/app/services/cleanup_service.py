from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid

from fastapi import HTTPException, status as http_status
from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models.external_cleanup_job import ExternalCleanupJob
from app.memory.vector_index import delete_memory_vector
from app.rag.vector_store import delete_document_vectors, delete_knowledge_base_vectors
from app.services.audit_service import record_audit_event
from app.storage.minio_client import remove_object

COMPLETED = "completed"
FAILED = "failed"
PROCESSING = "processing"
QUEUED = "queued"


def list_external_cleanup_jobs(
    db: Session,
    *,
    status: str | None = None,
    resource_type: str | None = None,
    limit: int = 100,
) -> list[ExternalCleanupJob]:
    query = select(ExternalCleanupJob)
    if status:
        query = query.where(ExternalCleanupJob.status == status.strip().lower())
    if resource_type:
        query = query.where(ExternalCleanupJob.resource_type == resource_type.strip().lower())
    return list(db.scalars(query.order_by(ExternalCleanupJob.updated_at.desc(), ExternalCleanupJob.created_at.desc()).limit(limit)).all())


def retry_external_cleanup_job(db: Session, job_id: str) -> ExternalCleanupJob:
    job = db.get(ExternalCleanupJob, job_id)
    if job is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="External cleanup job not found")
    if job.status == COMPLETED:
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail="External cleanup job is already completed")
    if job.status == PROCESSING and cleanup_lease_is_active(job):
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail="External cleanup job is already processing")
    return run_external_cleanup_job(db, job.id) or job


def create_external_cleanup_job(
    db: Session,
    *,
    actor_user_id: str | None,
    resource_type: str,
    resource_id: str,
    object_keys: list[str],
    metadata: dict | None = None,
) -> ExternalCleanupJob:
    job = ExternalCleanupJob(
        actor_user_id=actor_user_id,
        resource_type=resource_type,
        resource_id=resource_id,
        object_keys=list(dict.fromkeys(key for key in object_keys if key)),
        extra_metadata=metadata or {},
    )
    db.add(job)
    db.flush()
    return job


def run_external_cleanup_job(db: Session, job_id: str) -> ExternalCleanupJob | None:
    lease_token = uuid.uuid4().hex
    if not claim_external_cleanup_job(db, job_id, lease_token=lease_token):
        return db.get(ExternalCleanupJob, job_id)
    job = db.get(ExternalCleanupJob, job_id)
    if job is None:
        return None

    try:
        cleanup_external_resources(job)
    except Exception as exc:
        failed = finish_external_cleanup_job(
            db,
            job_id,
            lease_token=lease_token,
            status=FAILED,
            error_message=str(exc),
        )
        if failed is not None:
            record_external_cleanup_audit(db, failed, outcome=FAILED, detail=str(exc))
        return failed or db.get(ExternalCleanupJob, job_id)

    completed = finish_external_cleanup_job(
        db,
        job_id,
        lease_token=lease_token,
        status=COMPLETED,
    )
    if completed is not None:
        record_external_cleanup_audit(db, completed, outcome="success")
    return completed or db.get(ExternalCleanupJob, job_id)


def claim_external_cleanup_job(
    db: Session,
    job_id: str,
    *,
    lease_token: str,
    now: datetime | None = None,
) -> bool:
    now = now or datetime.now(timezone.utc)
    lease_expires_at = now + timedelta(seconds=get_settings().memory_update_job_lease_seconds)
    claimable = or_(
        ExternalCleanupJob.status.in_((QUEUED, FAILED)),
        and_(
            ExternalCleanupJob.status == PROCESSING,
            or_(
                ExternalCleanupJob.lease_expires_at.is_(None),
                ExternalCleanupJob.lease_expires_at <= now,
            ),
        ),
    )
    result = db.execute(
        update(ExternalCleanupJob)
        .where(ExternalCleanupJob.id == job_id, claimable)
        .values(
            status=PROCESSING,
            attempts=ExternalCleanupJob.attempts + 1,
            error_message="",
            lease_token=lease_token,
            lease_expires_at=lease_expires_at,
            completed_at=None,
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    db.commit()
    db.expire_all()
    return result.rowcount == 1


def finish_external_cleanup_job(
    db: Session,
    job_id: str,
    *,
    lease_token: str,
    status: str,
    error_message: str = "",
    now: datetime | None = None,
) -> ExternalCleanupJob | None:
    now = now or datetime.now(timezone.utc)
    result = db.execute(
        update(ExternalCleanupJob)
        .where(
            ExternalCleanupJob.id == job_id,
            ExternalCleanupJob.status == PROCESSING,
            ExternalCleanupJob.lease_token == lease_token,
        )
        .values(
            status=status,
            error_message=error_message,
            lease_token="",
            lease_expires_at=None,
            completed_at=now if status == COMPLETED else None,
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.rollback()
        return None
    db.commit()
    db.expire_all()
    return db.get(ExternalCleanupJob, job_id)


def cleanup_lease_is_active(job: ExternalCleanupJob, *, now: datetime | None = None) -> bool:
    if job.lease_expires_at is None:
        return False
    now = now or datetime.now(timezone.utc)
    lease_expires_at = job.lease_expires_at
    if lease_expires_at.tzinfo is None:
        lease_expires_at = lease_expires_at.replace(tzinfo=timezone.utc)
    return lease_expires_at > now


def cleanup_external_resources(job: ExternalCleanupJob) -> None:
    if job.resource_type == "document":
        delete_document_vectors(job.resource_id)
    elif job.resource_type == "knowledge_base":
        delete_knowledge_base_vectors(job.resource_id)
    elif job.resource_type == "user_memory":
        delete_memory_vector(job.resource_id)
    else:
        raise ValueError(f"Unsupported external cleanup resource type: {job.resource_type}")

    for object_key in job.object_keys or []:
        remove_object(str(object_key))


def record_external_cleanup_audit(
    db: Session,
    job: ExternalCleanupJob,
    *,
    outcome: str,
    detail: str | None = None,
) -> None:
    record_audit_event(
        db,
        actor_user_id=job.actor_user_id,
        action=f"{job.resource_type}.external_cleanup",
        resource_type=job.resource_type,
        resource_id=job.resource_id,
        outcome=outcome,
        detail=detail,
        metadata={
            "cleanup_job_id": job.id,
            "cleanup_status": job.status,
            "attempts": job.attempts,
            "object_count": len(job.object_keys or []),
            **(job.extra_metadata or {}),
        },
    )


def to_cleanup_metadata(job: ExternalCleanupJob | None) -> dict:
    if job is None:
        return {"cleanup_job_id": None, "cleanup_status": "missing"}
    return {
        "cleanup_job_id": job.id,
        "cleanup_status": job.status,
        "cleanup_attempts": job.attempts,
    }
