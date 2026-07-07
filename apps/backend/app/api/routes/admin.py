from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_admin
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.admin import AdminMetricsRead, AdminUserRead, AdminUserUpdate, AuditLogRead
from app.services.admin_service import get_admin_metrics, list_admin_users, update_admin_user
from app.services.audit_service import list_audit_logs

router = APIRouter(prefix="/admin")


@router.get("/metrics", response_model=AdminMetricsRead)
def metrics(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> AdminMetricsRead:
    return get_admin_metrics(db, current_user)


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
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[AuditLogRead]:
    return list_audit_logs(db, current_user, limit=limit)
