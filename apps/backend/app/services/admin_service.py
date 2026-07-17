from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.core.security_levels import validate_security_level
from app.db.models.conversation import Conversation, Message
from app.db.models.department import Department
from app.db.models.document import Document
from app.db.models.external_cleanup_job import ExternalCleanupJob
from app.db.models.feedback import Feedback
from app.db.models.knowledge_base import KnowledgeBase, KnowledgeBaseMember
from app.db.models.llm_call_log import LlmCallLog
from app.db.models.retrieval_log import RetrievalLog
from app.db.models.user import User
from app.db.models.user_memory import UserMemory
from app.schemas.admin import AdminMetricsRead, AdminUserCreate, AdminUserRead, AdminUserUpdate
from app.services.audit_service import record_audit_event
from app.services.cleanup_service import create_external_cleanup_job
from app.services.department_service import require_department


def get_admin_metrics(db: Session, current_user: User) -> AdminMetricsRead:
    scoped_user_id = None if current_user.is_admin else current_user.id

    conversation_count = count_rows(db, select(func.count(Conversation.id)), Conversation.user_id, scoped_user_id)
    message_count = count_messages(db, scoped_user_id)
    retrieval_logs = list_retrieval_logs_for_metrics(db, scoped_user_id)
    llm_rows = list_llm_logs_for_metrics(db, scoped_user_id)
    feedback_rows = list_feedbacks_for_metrics(db, scoped_user_id)
    cleanup_rows = list_external_cleanup_jobs_for_metrics(db, scoped_user_id)

    llm_call_count = len(llm_rows)
    total_tokens = sum(row.total_tokens for row in llm_rows)
    latency_values = [row.latency_ms for row in llm_rows if row.latency_ms is not None]
    positive_feedback_count = sum(1 for row in feedback_rows if row.rating > 0)
    negative_feedback_count = sum(1 for row in feedback_rows if row.rating < 0)
    selected_counts = [len(row.selected_chunks or []) for row in retrieval_logs]

    return AdminMetricsRead(
        generated_at=datetime.now(timezone.utc),
        scope="global" if current_user.is_admin else "current_user",
        conversation_count=conversation_count,
        message_count=message_count,
        retrieval_log_count=len(retrieval_logs),
        llm_call_count=llm_call_count,
        total_tokens=total_tokens,
        average_llm_latency_ms=average(latency_values),
        fallback_call_count=sum(1 for row in llm_rows if row.fallback_used),
        feedback_count=len(feedback_rows),
        positive_feedback_count=positive_feedback_count,
        negative_feedback_count=negative_feedback_count,
        positive_feedback_rate=safe_ratio(positive_feedback_count, len(feedback_rows)),
        average_selected_chunks=average(selected_counts),
        external_cleanup_job_count=len(cleanup_rows),
        failed_external_cleanup_job_count=sum(1 for row in cleanup_rows if row.status == "failed"),
        queued_external_cleanup_job_count=sum(1 for row in cleanup_rows if row.status == "queued"),
        recent_llm_errors=[
            {
                "id": row.id,
                "provider": row.provider,
                "model_name": row.model_name,
                "status": row.status,
                "error_message": row.error_message,
                "created_at": row.created_at.isoformat(),
            }
            for row in llm_rows
            if row.error_message
        ][:10],
    )


def list_admin_users(db: Session) -> list[AdminUserRead]:
    users = db.scalars(select(User).order_by(User.created_at.desc(), User.email.asc())).all()
    return [to_admin_user_read(user) for user in users]


def create_admin_user(
    db: Session,
    payload: AdminUserCreate,
    actor_user_id: str | None = None,
) -> AdminUserRead:
    existing = db.scalar(select(User).where(User.email == payload.email))
    if existing is not None:
        record_audit_event(
            db,
            actor_user_id=actor_user_id,
            action="admin.create_user",
            resource_type="user",
            resource_id=existing.id,
            outcome="denied",
            detail="Email is already registered",
            metadata={"email": payload.email},
        )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email is already registered")
    if payload.department_id is not None:
        require_department(db, payload.department_id)

    user = User(
        email=payload.email,
        username=payload.username,
        hashed_password=hash_password(payload.password),
        is_admin=payload.is_admin,
        security_level=validate_security_level(payload.security_level),
        department_id=payload.department_id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    record_audit_event(
        db,
        actor_user_id=actor_user_id,
        action="admin.create_user",
        resource_type="user",
        resource_id=user.id,
        security_level=user.security_level,
        metadata={
            "email": user.email,
            "is_admin": user.is_admin,
            "department_id": user.department_id,
        },
    )
    return to_admin_user_read(user)


def update_admin_user(
    db: Session,
    user_id: str,
    payload: AdminUserUpdate,
    actor_user_id: str | None = None,
) -> AdminUserRead:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if actor_user_id == user.id:
        detail = "Administrators cannot modify their own account"
        record_audit_event(
            db,
            actor_user_id=actor_user_id,
            action="admin.update_user",
            resource_type="user",
            resource_id=user.id,
            outcome="denied",
            detail=detail,
            metadata={"fields": sorted(payload.model_fields_set)},
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)

    previous_security_level = user.security_level
    previous_department_id = user.department_id
    next_security_level = user.security_level
    next_is_active = user.is_active
    next_is_admin = user.is_admin
    if payload.security_level is not None:
        next_security_level = validate_security_level(payload.security_level)
    if payload.is_active is not None:
        next_is_active = payload.is_active
    if payload.is_admin is not None:
        next_is_admin = payload.is_admin
    next_department_id = user.department_id
    if "department_id" in payload.model_fields_set and payload.department_id is not None:
        require_department(db, payload.department_id)
    if "department_id" in payload.model_fields_set:
        next_department_id = payload.department_id

    managed_department = db.scalar(select(Department).where(Department.admin_user_id == user.id))
    if managed_department is not None and (
        not next_is_active or next_department_id != managed_department.id
    ):
        detail = "Replace the department admin before deactivating or moving this user"
        record_audit_event(
            db,
            actor_user_id=actor_user_id,
            action="admin.update_user",
            resource_type="user",
            resource_id=user.id,
            outcome="denied",
            detail=detail,
            metadata={"department_id": managed_department.id},
        )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)

    if removes_last_active_admin(db, user, next_is_admin=next_is_admin, next_is_active=next_is_active):
        detail = "At least one active admin must remain"
        record_audit_event(
            db,
            actor_user_id=actor_user_id,
            action="admin.update_user",
            resource_type="user",
            resource_id=user.id,
            outcome="denied",
            security_level=user.security_level,
            detail=detail,
            metadata={
                "requested_is_active": next_is_active,
                "requested_is_admin": next_is_admin,
            },
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)

    user.security_level = next_security_level
    user.is_active = next_is_active
    user.is_admin = next_is_admin
    if "department_id" in payload.model_fields_set:
        user.department_id = next_department_id

    db.add(user)
    db.commit()
    db.refresh(user)
    record_audit_event(
        db,
        actor_user_id=actor_user_id,
        action="admin.update_user",
        resource_type="user",
        resource_id=user.id,
        security_level=user.security_level,
        metadata={
            "previous_security_level": previous_security_level,
            "new_security_level": user.security_level,
            "previous_department_id": previous_department_id,
            "new_department_id": user.department_id,
            "is_active": user.is_active,
            "is_admin": user.is_admin,
        },
    )
    return to_admin_user_read(user)


def delete_admin_user(
    db: Session,
    user_id: str,
    actor_user_id: str | None = None,
) -> None:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if actor_user_id == user.id:
        detail = "Administrators cannot delete their own account"
        record_audit_event(
            db,
            actor_user_id=actor_user_id,
            action="admin.delete_user",
            resource_type="user",
            resource_id=user.id,
            outcome="denied",
            detail=detail,
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)

    managed_department = db.scalar(select(Department).where(Department.admin_user_id == user.id))
    if managed_department is not None:
        detail = "Replace the department admin before deleting this user"
        record_audit_event(
            db,
            actor_user_id=actor_user_id,
            action="admin.delete_user",
            resource_type="user",
            resource_id=user.id,
            outcome="denied",
            detail=detail,
            metadata={"department_id": managed_department.id},
        )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)
    if removes_last_active_admin(db, user, next_is_admin=False, next_is_active=False):
        detail = "At least one active admin must remain"
        record_audit_event(
            db,
            actor_user_id=actor_user_id,
            action="admin.delete_user",
            resource_type="user",
            resource_id=user.id,
            outcome="denied",
            detail=detail,
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)

    owned_knowledge_bases = db.scalars(
        select(KnowledgeBase).where(KnowledgeBase.owner_id == user.id)
    ).all()
    deleted_knowledge_bases: list[KnowledgeBase] = []
    transferred_knowledge_base_count = 0
    for knowledge_base in owned_knowledge_bases:
        if knowledge_base.visibility == "private":
            deleted_knowledge_bases.append(knowledge_base)
            continue
        new_owner_id = actor_user_id
        if (
            knowledge_base.visibility == "department"
            and knowledge_base.department is not None
            and knowledge_base.department.admin_user_id not in {None, user.id}
        ):
            new_owner_id = knowledge_base.department.admin_user_id
        if new_owner_id is None or new_owner_id == user.id:
            deleted_knowledge_bases.append(knowledge_base)
            continue
        transfer_knowledge_base_owner(db, knowledge_base, new_owner_id)
        transferred_knowledge_base_count += 1
    db.flush()

    memory_ids = list(db.scalars(select(UserMemory.id).where(UserMemory.user_id == user.id)).all())
    cleanup_job_ids: list[str] = []
    for knowledge_base in deleted_knowledge_bases:
        object_keys = list(
            db.scalars(
                select(Document.object_key).where(
                    Document.knowledge_base_id == knowledge_base.id,
                    Document.object_key != "",
                )
            ).all()
        )
        cleanup_job = create_external_cleanup_job(
            db,
            actor_user_id=actor_user_id,
            resource_type="knowledge_base",
            resource_id=knowledge_base.id,
            object_keys=object_keys,
            metadata={"name": knowledge_base.name, "deleted_with_user_id": user.id},
        )
        cleanup_job_ids.append(cleanup_job.id)
        db.delete(knowledge_base)
    for memory_id in memory_ids:
        cleanup_job = create_external_cleanup_job(
            db,
            actor_user_id=actor_user_id,
            resource_type="user_memory",
            resource_id=memory_id,
            object_keys=[],
            metadata={"deleted_with_user_id": user.id},
        )
        cleanup_job_ids.append(cleanup_job.id)

    audit_metadata = {
        "email": user.email,
        "owned_knowledge_base_count": len(owned_knowledge_bases),
        "deleted_knowledge_base_count": len(deleted_knowledge_bases),
        "transferred_knowledge_base_count": transferred_knowledge_base_count,
        "memory_count": len(memory_ids),
        "cleanup_job_ids": cleanup_job_ids,
    }
    db.delete(user)
    db.commit()
    record_audit_event(
        db,
        actor_user_id=actor_user_id,
        action="admin.delete_user",
        resource_type="user",
        resource_id=user_id,
        metadata=audit_metadata,
    )
    if cleanup_job_ids:
        dispatch_external_cleanup_jobs(cleanup_job_ids)


def transfer_knowledge_base_owner(
    db: Session,
    knowledge_base: KnowledgeBase,
    new_owner_id: str,
) -> None:
    previous_owner_id = knowledge_base.owner_id
    knowledge_base.owner_id = new_owner_id
    db.add(knowledge_base)
    previous_membership = db.scalar(
        select(KnowledgeBaseMember).where(
            KnowledgeBaseMember.knowledge_base_id == knowledge_base.id,
            KnowledgeBaseMember.user_id == previous_owner_id,
        )
    )
    if previous_membership is not None:
        previous_membership.role = "viewer"
        db.add(previous_membership)
    new_membership = db.scalar(
        select(KnowledgeBaseMember).where(
            KnowledgeBaseMember.knowledge_base_id == knowledge_base.id,
            KnowledgeBaseMember.user_id == new_owner_id,
        )
    )
    if new_membership is None:
        db.add(
            KnowledgeBaseMember(
                knowledge_base_id=knowledge_base.id,
                user_id=new_owner_id,
                role="owner",
            )
        )
    else:
        new_membership.role = "owner"
        db.add(new_membership)


def dispatch_external_cleanup_jobs(job_ids: list[str]) -> None:
    from app.workers.cleanup_tasks import process_external_cleanup_job

    for job_id in job_ids:
        try:
            process_external_cleanup_job.delay(job_id)
        except Exception:
            continue


def removes_last_active_admin(db: Session, user: User, *, next_is_admin: bool, next_is_active: bool) -> bool:
    if not user.is_admin or not user.is_active:
        return False
    if next_is_admin and next_is_active:
        return False

    active_admin_count = int(
        db.scalar(
            select(func.count(User.id)).where(
                User.is_admin.is_(True),
                User.is_active.is_(True),
            )
        )
        or 0
    )
    return active_admin_count <= 1


def to_admin_user_read(user: User) -> AdminUserRead:
    return AdminUserRead(
        id=user.id,
        email=user.email,
        username=user.username,
        is_active=user.is_active,
        is_admin=user.is_admin,
        security_level=user.security_level,
        department_id=user.department_id,
        department_name=user.department_name,
        is_department_admin=user.is_department_admin,
        created_at=user.created_at,
    )


def count_rows(db: Session, query, user_column, user_id: str | None) -> int:
    if user_id is not None:
        query = query.where(user_column == user_id)
    return int(db.scalar(query) or 0)


def count_messages(db: Session, user_id: str | None) -> int:
    query = select(func.count(Message.id)).join(Conversation, Conversation.id == Message.conversation_id)
    if user_id is not None:
        query = query.where(Conversation.user_id == user_id)
    return int(db.scalar(query) or 0)


def list_retrieval_logs_for_metrics(db: Session, user_id: str | None) -> list[RetrievalLog]:
    query = select(RetrievalLog)
    if user_id is not None:
        query = query.where(RetrievalLog.user_id == user_id)
    return list(db.scalars(query.order_by(RetrievalLog.created_at.desc())).all())


def list_llm_logs_for_metrics(db: Session, user_id: str | None) -> list[LlmCallLog]:
    query = select(LlmCallLog)
    if user_id is not None:
        query = query.where(LlmCallLog.user_id == user_id)
    return list(db.scalars(query.order_by(LlmCallLog.created_at.desc())).all())


def list_feedbacks_for_metrics(db: Session, user_id: str | None) -> list[Feedback]:
    query = select(Feedback)
    if user_id is not None:
        query = query.where(Feedback.user_id == user_id)
    return list(db.scalars(query.order_by(Feedback.created_at.desc())).all())


def list_external_cleanup_jobs_for_metrics(db: Session, user_id: str | None) -> list[ExternalCleanupJob]:
    query = select(ExternalCleanupJob)
    if user_id is not None:
        query = query.where(ExternalCleanupJob.actor_user_id == user_id)
    return list(db.scalars(query.order_by(ExternalCleanupJob.updated_at.desc())).all())


def average(values: list[int | float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 3)


def safe_ratio(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 4)
