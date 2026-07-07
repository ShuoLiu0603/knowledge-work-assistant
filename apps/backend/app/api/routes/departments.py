from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_admin
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.department import DepartmentCreate, DepartmentRead
from app.services.department_service import create_department, list_departments

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
    _admin_user: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> DepartmentRead:
    return create_department(db, payload)
