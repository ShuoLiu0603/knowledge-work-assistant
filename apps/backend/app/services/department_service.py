from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.department import Department
from app.db.models.knowledge_base import KnowledgeBase, KnowledgeBaseMember
from app.db.models.user import User
from app.schemas.department import DepartmentAdminUpdate, DepartmentCreate, DepartmentRead
from app.services.audit_service import record_audit_event


def list_departments(db: Session) -> list[DepartmentRead]:
    departments = db.scalars(select(Department).order_by(Department.name.asc())).all()
    return [to_department_read(department) for department in departments]


def create_department(db: Session, payload: DepartmentCreate, actor_user_id: str | None = None) -> DepartmentRead:
    existing = db.scalar(select(Department).where(Department.name == payload.name))
    if existing is not None:
        record_audit_event(
            db,
            actor_user_id=actor_user_id,
            action="department.create",
            resource_type="department",
            outcome="denied",
            detail="Department name already exists",
            metadata={"name": payload.name},
        )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Department name already exists")
    if actor_user_id == payload.admin_user_id:
        detail = "Administrators cannot assign themselves as department admin"
        record_audit_event(
            db,
            actor_user_id=actor_user_id,
            action="department.create",
            resource_type="department",
            outcome="denied",
            detail=detail,
            metadata={"name": payload.name, "admin_user_id": payload.admin_user_id},
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)

    admin_user = require_department_admin_candidate(db, payload.admin_user_id)
    if admin_user.department_id is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="New department admin must not already belong to a department",
        )

    department = Department(
        name=payload.name,
        description=payload.description,
        admin_user_id=admin_user.id,
    )
    db.add(department)
    db.flush()
    admin_user.department_id = department.id
    db.add(admin_user)
    db.commit()
    db.refresh(department)
    record_audit_event(
        db,
        actor_user_id=actor_user_id,
        action="department.create",
        resource_type="department",
        resource_id=department.id,
        metadata={"name": department.name, "admin_user_id": department.admin_user_id},
    )
    return to_department_read(department)


def update_department_admin(
    db: Session,
    department_id: str,
    payload: DepartmentAdminUpdate,
    actor_user_id: str | None = None,
) -> DepartmentRead:
    department = require_department(db, department_id)
    if actor_user_id in {department.admin_user_id, payload.admin_user_id}:
        detail = "Administrators cannot change their own department-admin assignment"
        record_audit_event(
            db,
            actor_user_id=actor_user_id,
            action="department.update_admin",
            resource_type="department",
            resource_id=department.id,
            outcome="denied",
            detail=detail,
            metadata={
                "previous_admin_user_id": department.admin_user_id,
                "requested_admin_user_id": payload.admin_user_id,
            },
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)
    admin_user = require_department_admin_candidate(db, payload.admin_user_id, department_id=department.id)
    previous_admin_user_id = department.admin_user_id

    if admin_user.department_id is None:
        admin_user.department_id = department.id
        db.add(admin_user)
    department.admin_user_id = admin_user.id
    db.add(department)
    transferred_knowledge_base_count = transfer_department_knowledge_bases(
        db,
        department.id,
        admin_user.id,
    )
    db.commit()
    db.refresh(department)
    record_audit_event(
        db,
        actor_user_id=actor_user_id,
        action="department.update_admin",
        resource_type="department",
        resource_id=department.id,
        metadata={
            "previous_admin_user_id": previous_admin_user_id,
            "new_admin_user_id": department.admin_user_id,
            "transferred_knowledge_base_count": transferred_knowledge_base_count,
        },
    )
    return to_department_read(department)


def delete_department(
    db: Session,
    department_id: str,
    actor_user_id: str | None = None,
) -> None:
    department = require_department(db, department_id)
    if actor_user_id == department.admin_user_id:
        detail = "Administrators cannot delete a department they administer"
        record_audit_event(
            db,
            actor_user_id=actor_user_id,
            action="department.delete",
            resource_type="department",
            resource_id=department.id,
            outcome="denied",
            detail=detail,
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)

    other_member_id = db.scalar(
        select(User.id).where(
            User.department_id == department.id,
            User.id != department.admin_user_id,
        ).limit(1)
    )
    if other_member_id is not None:
        detail = "Reassign all department members before deleting this department"
        record_audit_event(
            db,
            actor_user_id=actor_user_id,
            action="department.delete",
            resource_type="department",
            resource_id=department.id,
            outcome="denied",
            detail=detail,
        )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)

    knowledge_base_id = db.scalar(
        select(KnowledgeBase.id).where(KnowledgeBase.department_id == department.id).limit(1)
    )
    if knowledge_base_id is not None:
        detail = "Delete or move all department knowledge bases before deleting this department"
        record_audit_event(
            db,
            actor_user_id=actor_user_id,
            action="department.delete",
            resource_type="department",
            resource_id=department.id,
            outcome="denied",
            detail=detail,
        )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)

    department_name = department.name
    previous_admin_user_id = department.admin_user_id
    admin_user = db.get(User, previous_admin_user_id) if previous_admin_user_id else None
    if admin_user is not None and admin_user.department_id == department.id:
        admin_user.department_id = None
        db.add(admin_user)
    department.admin_user_id = None
    db.add(department)
    db.flush()
    db.delete(department)
    db.commit()
    record_audit_event(
        db,
        actor_user_id=actor_user_id,
        action="department.delete",
        resource_type="department",
        resource_id=department_id,
        metadata={
            "name": department_name,
            "previous_admin_user_id": previous_admin_user_id,
        },
    )


def require_department(db: Session, department_id: str) -> Department:
    department = db.get(Department, department_id)
    if department is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Department not found")
    return department


def require_department_admin_candidate(
    db: Session,
    user_id: str,
    *,
    department_id: str | None = None,
) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Department admin user not found")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Department admin must be active")
    if user.department_id is not None and user.department_id != department_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Department admin already belongs to another department",
        )
    existing_assignment = db.scalar(
        select(Department).where(
            Department.admin_user_id == user.id,
            Department.id != department_id,
        )
    )
    if existing_assignment is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User already administers another department",
        )
    return user


def transfer_department_knowledge_bases(
    db: Session,
    department_id: str,
    new_owner_id: str,
) -> int:
    knowledge_bases = db.scalars(
        select(KnowledgeBase).where(
            KnowledgeBase.visibility == "department",
            KnowledgeBase.department_id == department_id,
        )
    ).all()
    for knowledge_base in knowledge_bases:
        previous_owner_id = knowledge_base.owner_id
        knowledge_base.owner_id = new_owner_id
        db.add(knowledge_base)
        if previous_owner_id != new_owner_id:
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
    return len(knowledge_bases)


def to_department_read(department: Department) -> DepartmentRead:
    return DepartmentRead(
        id=department.id,
        name=department.name,
        description=department.description,
        admin_user_id=department.admin_user_id,
        admin_username=department.admin_user.username if department.admin_user else None,
        created_at=department.created_at,
        updated_at=department.updated_at,
    )
