from celery import Celery
from celery.schedules import crontab

from app.core.config import get_settings, validate_runtime_settings

settings = get_settings()
validate_runtime_settings(settings)
RETRY_BACKOFF_MAX_SECONDS = settings.celery_task_retry_backoff_max_seconds
RELIABLE_TASK_OPTIONS = {
    "acks_late": True,
    "reject_on_worker_lost": True,
    "max_retries": settings.celery_task_max_retries,
}
MEMORY_TASK_OPTIONS = {**RELIABLE_TASK_OPTIONS, "max_retries": None}

celery_app = Celery(
    "agentic_rag_worker",
    broker=settings.redis_url,
    backend=settings.redis_url,
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    result_expires=settings.celery_result_expires_seconds,
    accept_content=["json"],
    task_track_started=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_default_retry_delay=settings.celery_task_retry_backoff_seconds,
    worker_prefetch_multiplier=settings.celery_worker_prefetch_multiplier,
    broker_connection_retry_on_startup=True,
    broker_transport_options={
        "visibility_timeout": max(
            settings.celery_broker_visibility_timeout_min_seconds,
            settings.memory_update_job_lease_seconds
            * settings.celery_broker_visibility_timeout_lease_multiplier,
        ),
    },
    timezone="UTC",
    beat_schedule={
        "recover-stale-memory-update-jobs": {
            "task": "recover_stale_memory_update_jobs",
            "schedule": settings.memory_update_job_recovery_interval_seconds,
            "options": {"expires": settings.memory_update_job_recovery_interval_seconds},
        },
        "recover-stale-external-cleanup-jobs": {
            "task": "recover_stale_external_cleanup_jobs",
            "schedule": settings.memory_update_job_recovery_interval_seconds,
            "options": {"expires": settings.memory_update_job_recovery_interval_seconds},
        },
        "recover-deferred-agent-memory-updates": {
            "task": "recover_deferred_agent_memory_updates",
            "schedule": settings.memory_update_job_recovery_interval_seconds,
            "options": {"expires": settings.memory_update_job_recovery_interval_seconds},
        },
        "recover-stale-conversation-summaries": {
            "task": "recover_stale_conversation_summaries",
            "schedule": settings.memory_update_job_recovery_interval_seconds,
            "options": {"expires": settings.memory_update_job_recovery_interval_seconds},
        },
        "apply-operational-retention-daily": {
            "task": "apply_operational_retention",
            "schedule": crontab(hour=settings.operational_retention_hour_utc, minute=0),
            "kwargs": {"dry_run": False},
            "options": {"expires": settings.celery_operational_retention_task_expires_seconds},
        },
        "reconcile-memory-vector-indexes-daily": {
            "task": "reconcile_memory_vector_indexes",
            "schedule": crontab(hour=(settings.operational_retention_hour_utc + 1) % 24, minute=0),
            "options": {"expires": settings.celery_operational_retention_task_expires_seconds},
        },
    },
)


def task_can_retry(task) -> bool:
    request = task.request
    return not bool(getattr(request, "called_directly", False)) and (
        task.max_retries is None or request.retries < task.max_retries
    )


def task_retry_countdown(retries: int) -> int:
    return min(
        settings.celery_task_retry_backoff_seconds * (2 ** max(retries, 0)),
        RETRY_BACKOFF_MAX_SECONDS,
    )

import app.workers.document_tasks  # noqa: E402,F401
import app.workers.memory_tasks  # noqa: E402,F401
import app.workers.cleanup_tasks  # noqa: E402,F401
import app.workers.retention_tasks  # noqa: E402,F401
