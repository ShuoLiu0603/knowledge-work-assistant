from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.department import Department
from app.schemas.department import DepartmentCreate, DepartmentRead


def list_departments(db: Session) -> list[DepartmentRead]:
    departments = db.scalars(select(Department).order_by(Department.name.asc())).all()
    return [to_department_read(department) for department in departments]


def create_department(db: Session, payload: DepartmentCreate) -> DepartmentRead:
    existing = db.scalar(select(Department).where(Department.name == payload.name))
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Department name already exists")

    department = Department(name=payload.name, description=payload.description)
    db.add(department)
    db.commit()
    db.refresh(department)
    return to_department_read(department)


def require_department(db: Session, department_id: str) -> Department:
    department = db.get(Department, department_id)
    if department is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Department not found")
    return department


def to_department_read(department: Department) -> DepartmentRead:
    return DepartmentRead(
        id=department.id,
        name=department.name,
        description=department.description,
        created_at=department.created_at,
        updated_at=department.updated_at,
    )
