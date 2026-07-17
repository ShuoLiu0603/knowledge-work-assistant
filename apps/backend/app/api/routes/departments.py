from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_admin
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.department import DepartmentAdminUpdate, DepartmentCreate, DepartmentRead
from app.services.department_service import (
    create_department,
    delete_department,
    list_departments,
    update_department_admin,
)

router = APIRouter(prefix="/departments")


@router.get("", response_model=list[DepartmentRead])
def list_items(
    _current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[DepartmentRead]:
    return list_departments(db)


@router.post("", response_model=DepartmentRead, status_code=status.HTTP_201_CREATED)
def create_item(
    payload: DepartmentCreate,
    admin_user: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> DepartmentRead:
    return create_department(db, payload, actor_user_id=admin_user.id)


@router.patch("/{department_id}/admin", response_model=DepartmentRead)
def update_admin(
    department_id: str,
    payload: DepartmentAdminUpdate,
    admin_user: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> DepartmentRead:
    return update_department_admin(db, department_id, payload, actor_user_id=admin_user.id)


@router.delete("/{department_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item(
    department_id: str,
    admin_user: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    delete_department(db, department_id, actor_user_id=admin_user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
