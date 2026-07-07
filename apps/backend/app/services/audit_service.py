from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.audit_log import AuditLog
from app.db.models.user import User
from app.schemas.admin import AuditLogRead


def record_audit_event(
    db: Session,
    actor_user_id: str | None,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    outcome: str = "success",
    security_level: int | None = None,
    detail: str | None = None,
    metadata: dict | None = None,
) -> AuditLog | None:
    try:
        log = AuditLog(
            actor_user_id=actor_user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            outcome=outcome,
            security_level=security_level,
            detail=detail,
            extra_metadata=metadata or {},
        )
        db.add(log)
        db.commit()
        db.refresh(log)
        return log
    except Exception:
        db.rollback()
        return None


def list_audit_logs(db: Session, current_user: User, limit: int = 100) -> list[AuditLogRead]:
    query = select(AuditLog)
    if not current_user.is_admin:
        query = query.where(AuditLog.actor_user_id == current_user.id)
    rows = db.scalars(query.order_by(AuditLog.created_at.desc()).limit(limit)).all()
    return [to_audit_log_read(row) for row in rows]


def to_audit_log_read(log: AuditLog) -> AuditLogRead:
    return AuditLogRead(
        id=log.id,
        actor_user_id=log.actor_user_id,
        action=log.action,
        resource_type=log.resource_type,
        resource_id=log.resource_id,
        outcome=log.outcome,
        security_level=log.security_level,
        detail=log.detail,
        metadata=log.extra_metadata,
        created_at=log.created_at,
    )
