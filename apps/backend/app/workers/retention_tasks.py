from __future__ import annotations

from app.core.config import get_settings
from app.db.session import SessionLocal, init_db
from app.services.retention_service import apply_operational_retention
from app.workers.celery_app import RELIABLE_TASK_OPTIONS, RETRY_BACKOFF_MAX_SECONDS, celery_app


@celery_app.task(
    name="apply_operational_retention",
    autoretry_for=(Exception,),
    retry_backoff=get_settings().celery_task_retry_backoff_seconds,
    retry_backoff_max=RETRY_BACKOFF_MAX_SECONDS,
    retry_jitter=True,
    **RELIABLE_TASK_OPTIONS,
)
def apply_operational_retention_task(dry_run: bool = False) -> dict:
    init_db()
    with SessionLocal() as db:
        return serialize_retention_result(apply_operational_retention(db, dry_run=dry_run))


def serialize_retention_result(result: dict) -> dict:
    return {
        **result,
        "generated_at": result["generated_at"].isoformat(),
        "cutoffs": {
            key: value.isoformat() if value is not None else None
            for key, value in result["cutoffs"].items()
        },
    }
