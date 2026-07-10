from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models.agent_run import AgentRun
from app.db.models.external_cleanup_job import ExternalCleanupJob
from app.db.models.llm_call_log import LlmCallLog
from app.db.models.retrieval_log import RetrievalLog
from app.db.models.user_memory import UserMemoryRecallLog, UserMemoryUpdateJob
from app.services.audit_service import record_audit_event


@dataclass(frozen=True)
class RetentionTarget:
    key: str
    model: type
    timestamp_column: object
    retention_days: int
    extra_filters: tuple = ()


def apply_operational_retention(
    db: Session,
    *,
    dry_run: bool = True,
    actor_user_id: str | None = None,
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now(timezone.utc)
    settings = get_settings()
    targets = [
        RetentionTarget(
            "llm_call_logs",
            LlmCallLog,
            LlmCallLog.created_at,
            settings.llm_call_log_retention_days,
        ),
        RetentionTarget(
            "retrieval_logs",
            RetrievalLog,
            RetrievalLog.created_at,
            settings.retrieval_log_retention_days,
        ),
        RetentionTarget(
            "agent_runs",
            AgentRun,
            AgentRun.created_at,
            settings.agent_run_retention_days,
        ),
        RetentionTarget(
            "memory_recall_logs",
            UserMemoryRecallLog,
            UserMemoryRecallLog.created_at,
            settings.memory_recall_log_retention_days,
        ),
        RetentionTarget(
            "memory_update_jobs",
            UserMemoryUpdateJob,
            UserMemoryUpdateJob.updated_at,
            settings.memory_update_job_retention_days,
            (UserMemoryUpdateJob.status.in_(("completed", "failed")),),
        ),
        RetentionTarget(
            "external_cleanup_jobs",
            ExternalCleanupJob,
            ExternalCleanupJob.updated_at,
            settings.external_cleanup_job_retention_days,
            (ExternalCleanupJob.status == "completed",),
        ),
    ]

    counts: dict[str, int] = {}
    cutoffs: dict[str, datetime | None] = {}
    for target in targets:
        cutoff = retention_cutoff(now, target.retention_days)
        cutoffs[target.key] = cutoff
        counts[target.key] = count_or_delete_target(db, target, cutoff=cutoff, dry_run=dry_run)

    if not dry_run:
        db.commit()
    record_retention_audit(db, actor_user_id=actor_user_id, dry_run=dry_run, counts=counts, cutoffs=cutoffs)
    return {
        "generated_at": now,
        "dry_run": dry_run,
        "deleted_counts": counts,
        "cutoffs": cutoffs,
    }


def retention_cutoff(now: datetime, retention_days: int) -> datetime | None:
    if retention_days <= 0:
        return None
    return now - timedelta(days=retention_days)


def count_or_delete_target(db: Session, target: RetentionTarget, *, cutoff: datetime | None, dry_run: bool) -> int:
    if cutoff is None:
        return 0
    filters = (target.timestamp_column < cutoff, *target.extra_filters)
    if dry_run:
        return int(db.scalar(select(func.count()).select_from(target.model).where(*filters)) or 0)
    result = db.execute(delete(target.model).where(*filters))
    return int(result.rowcount or 0)


def record_retention_audit(
    db: Session,
    *,
    actor_user_id: str | None,
    dry_run: bool,
    counts: dict[str, int],
    cutoffs: dict[str, datetime | None],
) -> None:
    record_audit_event(
        db,
        actor_user_id=actor_user_id,
        action="retention.dry_run" if dry_run else "retention.apply",
        resource_type="operational_data",
        resource_id=None,
        metadata={
            "dry_run": dry_run,
            "deleted_counts": counts,
            "cutoffs": {
                key: value.isoformat() if value is not None else None
                for key, value in cutoffs.items()
            },
        },
    )
