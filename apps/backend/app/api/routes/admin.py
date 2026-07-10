from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_admin
from app.db.models.external_cleanup_job import ExternalCleanupJob
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.admin import (
    AdminMetricsRead,
    AdminUserRead,
    AdminUserUpdate,
    AuditLogRead,
    ExternalCleanupJobRead,
    RetentionRunRead,
)
from app.services.admin_service import get_admin_metrics, list_admin_users, update_admin_user
from app.services.audit_service import list_audit_logs
from app.services.cleanup_service import list_external_cleanup_jobs, retry_external_cleanup_job
from app.services.retention_service import apply_operational_retention

router = APIRouter(prefix="/admin")


@router.get("/metrics", response_model=AdminMetricsRead)
def metrics(
    admin_user: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> AdminMetricsRead:
    return get_admin_metrics(db, admin_user)


@router.get("/users", response_model=list[AdminUserRead])
def users(
    _admin_user: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> list[AdminUserRead]:
    return list_admin_users(db)


@router.patch("/users/{user_id}", response_model=AdminUserRead)
def update_user(
    user_id: str,
    payload: AdminUserUpdate,
    admin_user: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> AdminUserRead:
    return update_admin_user(db, user_id, payload, actor_user_id=admin_user.id)


@router.get("/audit-logs", response_model=list[AuditLogRead])
def audit_logs(
    admin_user: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[AuditLogRead]:
    return list_audit_logs(db, admin_user, limit=limit)


@router.get("/external-cleanup-jobs", response_model=list[ExternalCleanupJobRead])
def external_cleanup_jobs(
    _admin_user: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    status: Annotated[str | None, Query()] = None,
    resource_type: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[ExternalCleanupJobRead]:
    jobs = list_external_cleanup_jobs(db, status=status, resource_type=resource_type, limit=limit)
    return [to_external_cleanup_job_read(job) for job in jobs]


@router.post("/external-cleanup-jobs/{job_id}/retry", response_model=ExternalCleanupJobRead)
def retry_external_cleanup(
    job_id: str,
    _admin_user: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> ExternalCleanupJobRead:
    job = retry_external_cleanup_job(db, job_id)
    return to_external_cleanup_job_read(job)


@router.post("/retention/run", response_model=RetentionRunRead)
def run_retention(
    admin_user: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    dry_run: Annotated[bool, Query()] = True,
) -> RetentionRunRead:
    return RetentionRunRead(**apply_operational_retention(db, dry_run=dry_run, actor_user_id=admin_user.id))


def to_external_cleanup_job_read(job: ExternalCleanupJob) -> ExternalCleanupJobRead:
    return ExternalCleanupJobRead(
        id=job.id,
        actor_user_id=job.actor_user_id,
        resource_type=job.resource_type,
        resource_id=job.resource_id,
        action=job.action,
        status=job.status,
        attempts=job.attempts,
        object_keys=job.object_keys,
        error_message=job.error_message,
        metadata=job.extra_metadata,
        created_at=job.created_at,
        updated_at=job.updated_at,
        completed_at=job.completed_at,
    )
