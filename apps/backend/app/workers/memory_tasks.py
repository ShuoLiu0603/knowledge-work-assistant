from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from celery.exceptions import Retry
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session, aliased

from app.core.config import get_settings
from app.db.models.agent_run import AgentRun
from app.db.models.conversation import Conversation
from app.db.models.conversation import Message
from app.db.models.retrieval_log import RetrievalLog
from app.db.models.user_memory import UserMemory, UserMemoryUpdateJob
from app.db.session import SessionLocal, init_db
from app.services.memory_service import (
    process_user_memory,
    reconcile_user_memories,
    should_update_conversation_summary,
    sync_memory_vectors_by_ids,
    to_memory_action_dict,
    update_conversation_summary,
)
from app.services.agent_service import apply_deferred_memory_update, attach_agent_run_to_message
from app.services.conversation_service import acquire_conversation_run_lease, release_conversation_run_lease
from app.memory import commands as memory_commands
from app.memory import jobs as memory_jobs
from app.memory import reconcile as memory_reconcile
from app.memory.types import MemoryAction
from app.services.retrieval_log_service import attach_retrieval_log_to_message
from app.workers.celery_app import (
    MEMORY_TASK_OPTIONS,
    RELIABLE_TASK_OPTIONS,
    RETRY_BACKOFF_MAX_SECONDS,
    celery_app,
    task_can_retry,
    task_retry_countdown,
)


@dataclass(frozen=True)
class MemoryJobClaim:
    outcome: str
    job: UserMemoryUpdateJob | None
    retry_after_seconds: int = 0


class MemoryJobLeaseActive(RuntimeError):
    pass


@celery_app.task(bind=True, name="process_memory_update_job", **MEMORY_TASK_OPTIONS)
def process_memory_update_job(self, job_id: str) -> dict:
    init_db()
    lease_token = str(uuid.uuid4())
    with SessionLocal() as db:
        claim = claim_memory_update_job(db, job_id, lease_token=lease_token)
        if claim.outcome == "missing":
            return {"job_id": job_id, "status": "missing"}
        if claim.outcome == "completed":
            job = claim.job
            return {
                "job_id": job_id,
                "status": "completed",
                "action_count": len(job.actions or []) if job is not None else 0,
            }
        if claim.outcome == "leased":
            if task_can_retry(self):
                raise self.retry(
                    exc=MemoryJobLeaseActive(f"Memory update job {job_id} has an active lease"),
                    countdown=claim.retry_after_seconds,
                )
            return {
                "job_id": job_id,
                "status": "processing",
                "retry_after_seconds": claim.retry_after_seconds,
            }
        if claim.outcome == "blocked":
            if task_can_retry(self):
                retry_error = retry_memory_task(
                    self,
                    db,
                    job_id,
                    error=MemoryJobLeaseActive(f"Memory update job {job_id} is waiting for an earlier user job"),
                    countdown=task_retry_countdown(self.request.retries),
                )
                return {
                    "job_id": job_id,
                    "status": "queued",
                    "error_message": f"worker dispatch failed: {retry_error}",
                }
            return {"job_id": job_id, "status": "queued", "blocked_by_earlier_job": True}
        if claim.outcome != "claimed" or claim.job is None:
            return {"job_id": job_id, "status": claim.job.status if claim.job is not None else "unavailable"}

        job = claim.job
        try:
            source_message = db.get(Message, job.message_id) if job.message_id else None
            if source_message is not None and not source_message.memory_enabled:
                actions = [
                    MemoryAction(
                        "ignore",
                        None,
                        "",
                        "source message disabled memory",
                    )
                ]
            else:
                actions = process_user_memory(
                    db,
                    job.user_id,
                    job.user_message,
                    conversation_id=job.conversation_id,
                    message_id=job.message_id,
                    assistant_text=job.assistant_message,
                    autocommit=False,
                    respect_no_memory_marker=False,
                )
            action_dicts = [to_memory_action_dict(action) for action in actions]
            completed = complete_memory_update_job(
                db,
                job_id,
                lease_token=lease_token,
                actions=action_dicts,
            )
            sync_memory_ids = memory_commands.pop_queued_memory_vector_sync_ids(db)
            if completed is None:
                return {"job_id": job_id, "status": "lease_lost"}
            if sync_memory_ids:
                sync_memory_vectors_by_ids(db, sync_memory_ids)
            return {
                "job_id": completed.id,
                "status": completed.status,
                "action_count": len(action_dicts),
                "actions": action_dicts,
            }
        except Exception as exc:
            db.rollback()
            will_retry = (
                not bool(getattr(self.request, "called_directly", False))
                and job.attempts <= get_settings().celery_task_max_retries
            )
            failed = fail_memory_update_job(
                db,
                job_id,
                lease_token=lease_token,
                error_message=str(exc),
                status="queued" if will_retry else "failed",
            )
            result = {
                "job_id": job_id,
                "status": failed.status if failed is not None else "lease_lost",
                "error_message": str(exc),
            }
            if will_retry:
                retry_error = retry_memory_task(
                    self,
                    db,
                    job_id,
                    error=exc,
                    countdown=task_retry_countdown(job.attempts - 1),
                )
                result["error_message"] = f"worker dispatch failed: {retry_error}"
            return result


def claim_memory_update_job(
    db: Session,
    job_id: str,
    *,
    lease_token: str,
    now: datetime | None = None,
    lease_seconds: int | None = None,
) -> MemoryJobClaim:
    now = now or datetime.now(timezone.utc)
    lease_seconds = lease_seconds or get_settings().memory_update_job_lease_seconds
    lease_expires_at = now + timedelta(seconds=lease_seconds)
    earlier_job = aliased(UserMemoryUpdateJob)
    earlier_unfinished_exists = (
        select(1)
        .select_from(earlier_job)
        .where(
            earlier_job.user_id == UserMemoryUpdateJob.user_id,
            earlier_job.status.in_(("queued", "processing")),
            or_(
                earlier_job.created_at < UserMemoryUpdateJob.created_at,
                and_(
                    earlier_job.created_at == UserMemoryUpdateJob.created_at,
                    earlier_job.id < UserMemoryUpdateJob.id,
                ),
            ),
        )
        .correlate(UserMemoryUpdateJob)
        .exists()
    )
    claimable = or_(
        UserMemoryUpdateJob.status == "queued",
        and_(
            UserMemoryUpdateJob.status == "processing",
            or_(
                UserMemoryUpdateJob.lease_expires_at.is_(None),
                UserMemoryUpdateJob.lease_expires_at <= now,
            ),
        ),
    )
    result = db.execute(
        update(UserMemoryUpdateJob)
        .where(UserMemoryUpdateJob.id == job_id, claimable, ~earlier_unfinished_exists)
        .values(
            status="processing",
            attempts=UserMemoryUpdateJob.attempts + 1,
            error_message="",
            lease_token=lease_token,
            lease_expires_at=lease_expires_at,
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    db.commit()
    db.expire_all()
    job = db.get(UserMemoryUpdateJob, job_id)
    if result.rowcount == 1:
        return MemoryJobClaim("claimed", job)
    if job is None:
        return MemoryJobClaim("missing", None)
    if job.status == "completed":
        return MemoryJobClaim("completed", job)
    if job.status == "processing" and job.lease_expires_at is not None:
        remaining = (as_utc(job.lease_expires_at) - now).total_seconds()
        return MemoryJobClaim("leased", job, max(1, math.ceil(remaining)))
    if job.status == "queued" and get_earlier_unfinished_job(db, job) is not None:
        return MemoryJobClaim("blocked", job)
    return MemoryJobClaim("unavailable", job)


def complete_memory_update_job(
    db: Session,
    job_id: str,
    *,
    lease_token: str,
    actions: list[dict],
    now: datetime | None = None,
) -> UserMemoryUpdateJob | None:
    now = now or datetime.now(timezone.utc)
    result = db.execute(
        update(UserMemoryUpdateJob)
        .where(
            UserMemoryUpdateJob.id == job_id,
            UserMemoryUpdateJob.status == "processing",
            UserMemoryUpdateJob.lease_token == lease_token,
        )
        .values(
            status="completed",
            actions=actions,
            error_message="",
            lease_token="",
            lease_expires_at=None,
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.rollback()
        return None
    db.commit()
    db.expire_all()
    return db.get(UserMemoryUpdateJob, job_id)


def fail_memory_update_job(
    db: Session,
    job_id: str,
    *,
    lease_token: str,
    error_message: str,
    status: str = "failed",
    now: datetime | None = None,
) -> UserMemoryUpdateJob | None:
    now = now or datetime.now(timezone.utc)
    result = db.execute(
        update(UserMemoryUpdateJob)
        .where(
            UserMemoryUpdateJob.id == job_id,
            UserMemoryUpdateJob.status == "processing",
            UserMemoryUpdateJob.lease_token == lease_token,
        )
        .values(
            status=status,
            error_message=error_message,
            lease_token="",
            lease_expires_at=None,
            dispatched_at=None if status == "queued" else UserMemoryUpdateJob.dispatched_at,
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    db.commit()
    if result.rowcount != 1:
        return None
    db.expire_all()
    return db.get(UserMemoryUpdateJob, job_id)


def retry_memory_task(task, db: Session, job_id: str, *, error: Exception, countdown: int) -> Exception:
    memory_jobs.claim_memory_update_job_dispatch(db, job_id)
    try:
        raise task.retry(exc=error, countdown=countdown)
    except Retry:
        raise
    except Exception as dispatch_error:
        memory_jobs.record_memory_update_job_dispatch_failure(db, job_id, dispatch_error)
        return dispatch_error


def list_recoverable_memory_update_job_ids(
    db: Session,
    *,
    now: datetime | None = None,
    limit: int = get_settings().worker_recovery_batch_size,
) -> list[str]:
    now = now or datetime.now(timezone.utc)
    queued_before = now - timedelta(seconds=get_settings().memory_update_job_recovery_interval_seconds)
    redispatch_before = now - timedelta(seconds=get_settings().memory_update_job_lease_seconds)
    query = (
        select(UserMemoryUpdateJob.id)
        .where(
            or_(
                and_(
                    UserMemoryUpdateJob.status == "processing",
                    or_(
                        UserMemoryUpdateJob.lease_expires_at.is_(None),
                        UserMemoryUpdateJob.lease_expires_at <= now,
                    ),
                    or_(
                        UserMemoryUpdateJob.dispatched_at.is_(None),
                        UserMemoryUpdateJob.dispatched_at <= redispatch_before,
                    ),
                ),
                and_(
                    UserMemoryUpdateJob.status == "queued",
                    UserMemoryUpdateJob.updated_at <= queued_before,
                    or_(
                        UserMemoryUpdateJob.dispatched_at.is_(None),
                        UserMemoryUpdateJob.dispatched_at <= redispatch_before,
                        UserMemoryUpdateJob.error_message.like("worker dispatch failed:%"),
                    ),
                ),
            ),
        )
        .order_by(UserMemoryUpdateJob.updated_at.asc(), UserMemoryUpdateJob.id.asc())
        .limit(limit)
    )
    return list(db.scalars(query).all())


@celery_app.task(
    name="recover_stale_memory_update_jobs",
    autoretry_for=(Exception,),
    retry_backoff=get_settings().celery_task_retry_backoff_seconds,
    retry_backoff_max=RETRY_BACKOFF_MAX_SECONDS,
    retry_jitter=True,
    **RELIABLE_TASK_OPTIONS,
)
def recover_stale_memory_update_jobs() -> dict:
    init_db()
    with SessionLocal() as db:
        job_ids = list_recoverable_memory_update_job_ids(db)
        dispatched_job_ids = []
        redispatch_before = datetime.now(timezone.utc) - timedelta(
            seconds=get_settings().memory_update_job_lease_seconds
        )
        for job_id in job_ids:
            if not memory_jobs.claim_memory_update_job_dispatch(
                db,
                job_id,
                statuses=("queued", "processing"),
                redispatch_before=redispatch_before,
            ):
                continue
            try:
                process_memory_update_job.delay(job_id)
            except Exception as exc:
                memory_jobs.record_memory_update_job_dispatch_failure(db, job_id, exc)
                raise
            dispatched_job_ids.append(job_id)
    return {
        "status": "completed",
        "stale_count": len(dispatched_job_ids),
        "dispatched_job_ids": dispatched_job_ids,
    }


@celery_app.task(
    name="update_conversation_summary",
    autoretry_for=(Exception,),
    retry_backoff=get_settings().celery_task_retry_backoff_seconds,
    retry_backoff_max=RETRY_BACKOFF_MAX_SECONDS,
    retry_jitter=True,
    **RELIABLE_TASK_OPTIONS,
)
def update_conversation_summary_task(conversation_id: str, user_id: str) -> dict:
    init_db()
    lease = acquire_conversation_run_lease(
        f"summary:{conversation_id}",
        lease_seconds=summary_update_lease_seconds(),
    )
    if lease is None:
        return {"conversation_id": conversation_id, "status": "busy"}
    try:
        with SessionLocal() as db:
            conversation = db.get(Conversation, conversation_id)
            if conversation is None:
                return {"conversation_id": conversation_id, "status": "missing"}
            if conversation.user_id != user_id:
                return {"conversation_id": conversation_id, "status": "owner_mismatch"}
            if not should_update_conversation_summary(db, conversation_id):
                return {"conversation_id": conversation_id, "status": "skipped"}

            summary = update_conversation_summary(db, conversation, "", "", user_id)
            return {
                "conversation_id": conversation_id,
                "status": "completed",
                "summary_length": len(summary),
                "summary_message_count": conversation.summary_message_count,
            }
    finally:
        release_conversation_run_lease(lease)


def summary_update_lease_seconds() -> int:
    settings = get_settings()
    return max(
        settings.conversation_summary_lease_min_seconds,
        int(
            settings.llm_timeout_seconds * settings.conversation_summary_max_unprocessed
            + settings.conversation_lease_grace_seconds
        ),
    )


def list_summary_recovery_candidates(
    db: Session,
    *,
    limit: int = get_settings().worker_recovery_batch_size,
) -> list[tuple[str, str]]:
    bounded_limit = max(1, min(limit, 500))
    message_counts = (
        select(Message.conversation_id, func.count(Message.id).label("message_count"))
        .group_by(Message.conversation_id)
        .subquery()
    )
    rows = db.execute(
        select(Conversation.id, Conversation.user_id)
        .join(message_counts, message_counts.c.conversation_id == Conversation.id)
        .where(message_counts.c.message_count > Conversation.summary_message_count)
        .order_by(Conversation.updated_at.asc(), Conversation.id.asc())
        .limit(bounded_limit * get_settings().worker_recovery_scan_multiplier)
    ).all()
    candidates = []
    for conversation_id, user_id in rows:
        if not should_update_conversation_summary(db, conversation_id):
            continue
        candidates.append((conversation_id, user_id))
        if len(candidates) >= bounded_limit:
            break
    return candidates


@celery_app.task(
    name="recover_stale_conversation_summaries",
    autoretry_for=(Exception,),
    retry_backoff=get_settings().celery_task_retry_backoff_seconds,
    retry_backoff_max=RETRY_BACKOFF_MAX_SECONDS,
    retry_jitter=True,
    **RELIABLE_TASK_OPTIONS,
)
def recover_stale_conversation_summaries() -> dict:
    init_db()
    with SessionLocal() as db:
        candidates = list_summary_recovery_candidates(db)
    for conversation_id, user_id in candidates:
        update_conversation_summary_task.delay(conversation_id, user_id)
    return {
        "status": "completed",
        "recovered_count": len(candidates),
        "conversation_ids": [conversation_id for conversation_id, _user_id in candidates],
    }


def list_deferred_agent_run_ids(
    db: Session,
    *,
    now: datetime | None = None,
    limit: int = get_settings().worker_recovery_batch_size,
) -> list[str]:
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=get_settings().memory_update_job_recovery_interval_seconds)
    candidates = db.scalars(
        select(AgentRun)
        .where(
            AgentRun.status == "completed",
            AgentRun.conversation_id.is_not(None),
            AgentRun.updated_at <= cutoff,
        )
        .order_by(AgentRun.updated_at.asc(), AgentRun.id.asc())
        .limit(limit * get_settings().worker_recovery_scan_multiplier)
    ).all()
    deferred = []
    for run in candidates:
        state = run.state if isinstance(run.state, dict) else {}
        actions = state.get("memory_actions")
        if isinstance(actions, list) and any(
            isinstance(action, dict) and action.get("action") == "deferred"
            for action in actions
        ):
            deferred.append(run.id)
            if len(deferred) >= limit:
                break
    return deferred


@celery_app.task(
    name="recover_deferred_agent_memory_updates",
    autoretry_for=(Exception,),
    retry_backoff=get_settings().celery_task_retry_backoff_seconds,
    retry_backoff_max=RETRY_BACKOFF_MAX_SECONDS,
    retry_jitter=True,
    **RELIABLE_TASK_OPTIONS,
)
def recover_deferred_agent_memory_updates() -> dict:
    if get_settings().memory_update_mode.strip().lower() == "disabled":
        return {"status": "disabled", "recovered_count": 0, "agent_run_ids": []}
    init_db()
    recovered_ids = []
    with SessionLocal() as db:
        for run_id in list_deferred_agent_run_ids(db):
            run = db.get(AgentRun, run_id)
            if run is None:
                continue
            state = run.state if isinstance(run.state, dict) else {}
            source_message_id = state.get("message_id")
            if not isinstance(source_message_id, str) or not source_message_id:
                continue
            source_message = db.get(Message, source_message_id)
            if source_message is None or source_message.conversation_id != run.conversation_id:
                continue
            assistant_message_id = db.scalar(
                select(Message.id)
                .where(
                    Message.conversation_id == run.conversation_id,
                    Message.role == "assistant",
                    Message.status == "completed",
                    or_(
                        Message.created_at > source_message.created_at,
                        and_(
                            Message.created_at == source_message.created_at,
                            Message.id > source_message.id,
                        ),
                    ),
                )
                .order_by(Message.created_at.asc(), Message.id.asc())
                .limit(1)
            )
            if not assistant_message_id:
                continue
            if run.message_id != assistant_message_id:
                run = attach_agent_run_to_message(db, run, assistant_message_id)
            if run.retrieval_log_id:
                retrieval_log = db.get(RetrievalLog, run.retrieval_log_id)
                if (
                    retrieval_log is not None
                    and retrieval_log.conversation_id == run.conversation_id
                    and retrieval_log.message_id != assistant_message_id
                ):
                    attach_retrieval_log_to_message(db, retrieval_log, assistant_message_id)
            apply_deferred_memory_update(
                db,
                run,
                source_message_id=source_message.id,
            )
            recovered_ids.append(run.id)
    return {
        "status": "completed",
        "recovered_count": len(recovered_ids),
        "agent_run_ids": recovered_ids,
    }


@celery_app.task(
    name="reconcile_memory_vector_indexes",
    autoretry_for=(Exception,),
    retry_backoff=get_settings().celery_task_retry_backoff_seconds,
    retry_backoff_max=RETRY_BACKOFF_MAX_SECONDS,
    retry_jitter=True,
    **RELIABLE_TASK_OPTIONS,
)
def reconcile_memory_vector_indexes() -> dict:
    if not get_settings().memory_vector_index_enabled:
        return {"status": "disabled", "user_count": 0, "finding_count": 0, "applied_count": 0}

    init_db()
    with SessionLocal() as db:
        user_ids = list(db.scalars(select(UserMemory.user_id).distinct()).all())
        findings = [
            finding
            for user_id in user_ids
            for finding in memory_reconcile.reconcile_vector_index(db, user_id, apply=True)
        ]
    return {
        "status": "completed",
        "user_count": len(user_ids),
        "finding_count": len(findings),
        "applied_count": sum(1 for finding in findings if finding.applied),
    }


@celery_app.task(
    name="reconcile_user_memories",
    autoretry_for=(Exception,),
    retry_backoff=get_settings().celery_task_retry_backoff_seconds,
    retry_backoff_max=RETRY_BACKOFF_MAX_SECONDS,
    retry_jitter=True,
    **RELIABLE_TASK_OPTIONS,
)
def reconcile_user_memory_task(user_id: str, apply: bool = False, llm_review: bool = False) -> dict:
    init_db()
    with SessionLocal() as db:
        return reconcile_user_memories(db, user_id, apply=apply, llm_review=llm_review)


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def get_earlier_unfinished_job(
    db: Session,
    job: UserMemoryUpdateJob,
) -> UserMemoryUpdateJob | None:
    query = (
        select(UserMemoryUpdateJob)
        .where(
            UserMemoryUpdateJob.user_id == job.user_id,
            UserMemoryUpdateJob.status.in_(("queued", "processing")),
            or_(
                UserMemoryUpdateJob.created_at < job.created_at,
                and_(
                    UserMemoryUpdateJob.created_at == job.created_at,
                    UserMemoryUpdateJob.id < job.id,
                ),
            ),
        )
        .order_by(UserMemoryUpdateJob.created_at.asc(), UserMemoryUpdateJob.id.asc())
        .limit(1)
    )
    return db.scalar(query)
