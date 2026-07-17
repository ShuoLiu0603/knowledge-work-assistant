from dataclasses import dataclass

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security_levels import MAX_SECURITY_LEVEL
from app.db.models.document import Document
from app.db.models.knowledge_base import KnowledgeBase, KnowledgeBaseMember
from app.db.models.user import User
from app.schemas.knowledge_base import KnowledgeBaseCreate, KnowledgeBaseRead, KnowledgeBaseUpdate
from app.services.audit_service import record_audit_event
from app.services.cleanup_service import create_external_cleanup_job, run_external_cleanup_job, to_cleanup_metadata
from app.services.department_service import require_department

ROLE_RANK = {
    "viewer": 1,
    "editor": 2,
    "owner": 3,
}

SEARCH_SCOPES = {"single", "department", "public", "accessible", "all"}


@dataclass(frozen=True)
class KnowledgeBaseSearchScope:
    primary_knowledge_base_id: str | None
    knowledge_base_ids: list[str]
    scope_type: str
    department_id: str | None
    max_security_level: int


def create_knowledge_base(db: Session, user_id: str, payload: KnowledgeBaseCreate) -> KnowledgeBaseRead:
    user = require_user(db, user_id)
    if payload.visibility == "public" and not user.is_admin:
        record_audit_event(
            db,
            actor_user_id=user_id,
            action="knowledge_base.create",
            resource_type="knowledge_base",
            outcome="denied",
            detail="Only admins can create public knowledge bases",
            metadata={
                "name": payload.name,
                "visibility": payload.visibility,
            },
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only admins can create public knowledge bases")
    department_id = resolve_department_id_for_write(db, user, payload.visibility, payload.department_id)
    owner_id = user_id
    if payload.visibility == "department" and department_id is not None:
        department = require_department(db, department_id)
        owner_id = department.admin_user_id or user_id

    knowledge_base = KnowledgeBase(
        owner_id=owner_id,
        department_id=department_id,
        name=payload.name,
        description=payload.description,
        visibility=payload.visibility,
    )
    db.add(knowledge_base)
    db.flush()

    member = KnowledgeBaseMember(
        knowledge_base_id=knowledge_base.id,
        user_id=owner_id,
        role="owner",
    )
    db.add(member)
    db.commit()
    db.refresh(knowledge_base)
    record_audit_event(
        db,
        actor_user_id=user_id,
        action="knowledge_base.create",
        resource_type="knowledge_base",
        resource_id=knowledge_base.id,
        metadata={
            "name": knowledge_base.name,
            "visibility": knowledge_base.visibility,
            "department_id": knowledge_base.department_id,
            "owner_id": knowledge_base.owner_id,
        },
    )
    return to_read_model(
        knowledge_base,
        effective_role(user, knowledge_base, "owner" if owner_id == user_id else None),
    )


def list_knowledge_bases(db: Session, user_id: str) -> list[KnowledgeBaseRead]:
    user = require_user(db, user_id)
    visible: dict[str, tuple[KnowledgeBase, str]] = {}

    member_rows = db.execute(
        select(KnowledgeBase, KnowledgeBaseMember.role)
        .join(KnowledgeBaseMember, KnowledgeBaseMember.knowledge_base_id == KnowledgeBase.id)
        .where(KnowledgeBaseMember.user_id == user_id)
    ).all()
    for knowledge_base, role in member_rows:
        visible[knowledge_base.id] = (knowledge_base, effective_role(user, knowledge_base, role))

    public_items = db.scalars(select(KnowledgeBase).where(KnowledgeBase.visibility == "public")).all()
    for knowledge_base in public_items:
        existing = visible.get(knowledge_base.id)
        existing_role = existing[1] if existing else None
        visible[knowledge_base.id] = (
            knowledge_base,
            strongest_role(existing_role, effective_role(user, knowledge_base, None)),
        )

    if user.is_admin:
        department_items = db.scalars(select(KnowledgeBase).where(KnowledgeBase.visibility == "department")).all()
    elif user.department_id:
        department_items = db.scalars(
            select(KnowledgeBase).where(
                KnowledgeBase.visibility == "department",
                KnowledgeBase.department_id == user.department_id,
            )
        ).all()
    else:
        department_items = []

    for knowledge_base in department_items:
        existing = visible.get(knowledge_base.id)
        existing_role = existing[1] if existing else None
        visible[knowledge_base.id] = (
            knowledge_base,
            strongest_role(existing_role, effective_role(user, knowledge_base, None)),
        )

    rows = sorted(
        visible.values(),
        key=lambda item: (item[0].updated_at, item[0].created_at),
        reverse=True,
    )
    return [to_read_model(knowledge_base, role) for knowledge_base, role in rows]


def get_knowledge_base(db: Session, user_id: str, kb_id: str) -> KnowledgeBaseRead:
    knowledge_base, role = ensure_kb_access(db, user_id, kb_id, required_role="viewer")
    return to_read_model(knowledge_base, role)


def update_knowledge_base(
    db: Session,
    user_id: str,
    kb_id: str,
    payload: KnowledgeBaseUpdate,
) -> KnowledgeBaseRead:
    user = require_user(db, user_id)
    knowledge_base, role = ensure_kb_access(db, user_id, kb_id, required_role="editor")
    update_data = payload.model_dump(exclude_unset=True)
    previous_values = {
        "name": knowledge_base.name,
        "description": knowledge_base.description,
        "visibility": knowledge_base.visibility,
        "department_id": knowledge_base.department_id,
    }
    next_name = update_data.get("name", knowledge_base.name)
    next_description = update_data.get("description", knowledge_base.description)
    next_visibility = update_data.get("visibility", knowledge_base.visibility)
    next_department_id = knowledge_base.department_id

    if "visibility" in update_data:
        if next_visibility == "public" and not user.is_admin:
            record_audit_event(
                db,
                actor_user_id=user_id,
                action="knowledge_base.update",
                resource_type="knowledge_base",
                resource_id=knowledge_base.id,
                outcome="denied",
                detail="Only admins can publish knowledge bases",
                metadata={
                    "requested_visibility": next_visibility,
                    "previous_visibility": knowledge_base.visibility,
                },
            )
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only admins can publish knowledge bases")
        if knowledge_base.visibility == "public" and next_visibility != "public" and knowledge_base.owner_id != user_id:
            record_audit_event(
                db,
                actor_user_id=user_id,
                action="knowledge_base.update",
                resource_type="knowledge_base",
                resource_id=knowledge_base.id,
                outcome="denied",
                detail="Only the owner can make a public knowledge base private",
                metadata={
                    "requested_visibility": next_visibility,
                    "previous_visibility": knowledge_base.visibility,
                },
            )
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the owner can make a public knowledge base private")
        next_department_id = resolve_department_id_for_write(
            db,
            user,
            next_visibility,
            update_data.get("department_id", knowledge_base.department_id),
        )
    elif "department_id" in update_data:
        next_department_id = resolve_department_id_for_write(
            db,
            user,
            knowledge_base.visibility,
            update_data["department_id"],
        )

    knowledge_base.name = next_name
    knowledge_base.description = next_description
    knowledge_base.visibility = next_visibility
    knowledge_base.department_id = next_department_id
    db.add(knowledge_base)
    db.commit()
    db.refresh(knowledge_base)
    record_audit_event(
        db,
        actor_user_id=user_id,
        action="knowledge_base.update",
        resource_type="knowledge_base",
        resource_id=knowledge_base.id,
        metadata={
            "previous": previous_values,
            "current": {
                "name": knowledge_base.name,
                "description": knowledge_base.description,
                "visibility": knowledge_base.visibility,
                "department_id": knowledge_base.department_id,
            },
            "updated_fields": sorted(update_data),
        },
    )
    return to_read_model(knowledge_base, effective_role(user, knowledge_base, role))


def delete_knowledge_base(db: Session, user_id: str, kb_id: str) -> None:
    knowledge_base, _role = ensure_kb_access(db, user_id, kb_id, required_role="owner")
    object_keys = list_knowledge_base_object_keys(db, kb_id)
    audit_metadata = {
        "name": knowledge_base.name,
        "visibility": knowledge_base.visibility,
        "department_id": knowledge_base.department_id,
        "object_count": len(object_keys),
    }
    cleanup_job = create_external_cleanup_job(
        db,
        actor_user_id=user_id,
        resource_type="knowledge_base",
        resource_id=kb_id,
        object_keys=object_keys,
        metadata=audit_metadata,
    )
    db.delete(knowledge_base)
    db.commit()
    cleanup_job = run_external_cleanup_job(db, cleanup_job.id)
    record_audit_event(
        db,
        actor_user_id=user_id,
        action="knowledge_base.delete",
        resource_type="knowledge_base",
        resource_id=kb_id,
        metadata={**audit_metadata, **to_cleanup_metadata(cleanup_job)},
    )


def list_knowledge_base_object_keys(db: Session, kb_id: str) -> list[str]:
    return [
        object_key
        for object_key in db.scalars(select(Document.object_key).where(Document.knowledge_base_id == kb_id)).all()
        if object_key
    ]


def ensure_kb_access(
    db: Session,
    user_id: str,
    kb_id: str,
    required_role: str = "viewer",
) -> tuple[KnowledgeBase, str]:
    user = require_user(db, user_id)
    knowledge_base = db.get(KnowledgeBase, kb_id)
    if knowledge_base is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge base not found")

    member = db.scalar(
        select(KnowledgeBaseMember).where(
            KnowledgeBaseMember.knowledge_base_id == kb_id,
            KnowledgeBaseMember.user_id == user_id,
        )
    )
    if member is None:
        if has_implicit_view_access(user, knowledge_base):
            role = effective_role(user, knowledge_base, None)
            if ROLE_RANK[role] >= ROLE_RANK[required_role]:
                return knowledge_base, role
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient knowledge base role")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge base not found")

    role = effective_role(user, knowledge_base, member.role)
    if ROLE_RANK[role] < ROLE_RANK[required_role]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient knowledge base role")

    return knowledge_base, role


def require_user(db: Session, user_id: str) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid user")
    return user


def effective_role(user: User, knowledge_base: KnowledgeBase, member_role: str | None) -> str:
    if knowledge_base.visibility == "department":
        if user.is_admin or (
            knowledge_base.department is not None
            and knowledge_base.department.admin_user_id == user.id
        ):
            return "owner"
        return "viewer"
    if user.is_admin and knowledge_base.visibility == "public":
        return "owner"
    if member_role:
        return member_role
    if has_implicit_view_access(user, knowledge_base):
        return "viewer"
    return "viewer"


def strongest_role(left: str | None, right: str) -> str:
    if left is None:
        return right
    return left if ROLE_RANK[left] >= ROLE_RANK[right] else right


def to_read_model(knowledge_base: KnowledgeBase, role: str) -> KnowledgeBaseRead:
    return KnowledgeBaseRead(
        id=knowledge_base.id,
        owner_id=knowledge_base.owner_id,
        department_id=knowledge_base.department_id,
        department_name=knowledge_base.department.name if knowledge_base.department else None,
        name=knowledge_base.name,
        description=knowledge_base.description,
        visibility=knowledge_base.visibility,
        role=role,
        created_at=knowledge_base.created_at,
        updated_at=knowledge_base.updated_at,
    )


def has_implicit_view_access(user: User, knowledge_base: KnowledgeBase) -> bool:
    if knowledge_base.visibility == "public":
        return True
    if user.is_admin and knowledge_base.visibility == "department":
        return True
    return bool(
        knowledge_base.visibility == "department"
        and knowledge_base.department_id
        and user.department_id == knowledge_base.department_id
    )


def resolve_department_id_for_write(
    db: Session,
    user: User,
    visibility: str,
    requested_department_id: str | None,
) -> str | None:
    if visibility in {"private", "public"}:
        return None

    department_id = requested_department_id or user.department_id
    if not department_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Department knowledge bases require a department",
        )
    department = require_department(db, department_id)
    if not user.is_admin and department.admin_user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the department admin can manage department knowledge bases",
        )
    return department_id


def resolve_search_scope(
    db: Session,
    user_id: str,
    primary_kb_id: str | None = None,
    scope_type: str = "single",
    department_id: str | None = None,
) -> KnowledgeBaseSearchScope:
    user = require_user(db, user_id)
    normalized_scope = normalize_search_scope(scope_type)
    if normalized_scope not in SEARCH_SCOPES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid search scope")

    if normalized_scope == "single":
        if not primary_kb_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Single knowledge base search requires a knowledge base")
        primary_kb, _role = ensure_kb_access(db, user_id, primary_kb_id, required_role="viewer")
        kb_ids = [primary_kb.id]
        max_level = MAX_SECURITY_LEVEL if user.is_admin or primary_kb.visibility == "private" else user.security_level
        target_department_id = None
        primary_kb_id = primary_kb.id
    elif normalized_scope == "department":
        target_department_id = department_id or user.department_id
        if not target_department_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User has no department")
        if not user.is_admin and target_department_id != user.department_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot search another department")
        kb_ids = department_search_kb_ids(db, user_id, target_department_id)
        max_level = MAX_SECURITY_LEVEL if user.is_admin else user.security_level
        primary_kb_id = None
    elif normalized_scope == "public":
        target_department_id = None
        kb_ids = public_search_kb_ids(db, user_id)
        max_level = MAX_SECURITY_LEVEL if user.is_admin else user.security_level
        primary_kb_id = None
    else:
        target_department_id = None
        kb_ids = [item.id for item in list_knowledge_bases(db, user_id)]
        max_level = MAX_SECURITY_LEVEL if user.is_admin else user.security_level
        primary_kb_id = None

    return KnowledgeBaseSearchScope(
        primary_knowledge_base_id=primary_kb_id,
        knowledge_base_ids=kb_ids,
        scope_type=normalized_scope,
        department_id=target_department_id,
        max_security_level=max_level,
    )


def normalize_search_scope(scope_type: str | None) -> str:
    normalized = (scope_type or "single").strip().lower()
    return "accessible" if normalized == "all" else normalized


def department_search_kb_ids(db: Session, user_id: str, department_id: str) -> list[str]:
    require_department(db, department_id)
    accessible = list_knowledge_bases(db, user_id)
    return [
        item.id
        for item in accessible
        if item.visibility == "department" and item.department_id == department_id
    ]


def public_search_kb_ids(db: Session, user_id: str) -> list[str]:
    return [item.id for item in list_knowledge_bases(db, user_id) if item.visibility == "public"]
