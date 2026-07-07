from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.knowledge_base import KnowledgeBaseCreate, KnowledgeBaseRead, KnowledgeBaseUpdate
from app.services.knowledge_base_service import (
    create_knowledge_base,
    delete_knowledge_base,
    get_knowledge_base,
    list_knowledge_bases,
    update_knowledge_base,
)

router = APIRouter(prefix="/knowledge-bases")


@router.get("", response_model=list[KnowledgeBaseRead])
def list_items(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[KnowledgeBaseRead]:
    return list_knowledge_bases(db, current_user.id)


@router.post("", response_model=KnowledgeBaseRead, status_code=status.HTTP_201_CREATED)
def create_item(
    payload: KnowledgeBaseCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> KnowledgeBaseRead:
    return create_knowledge_base(db, current_user.id, payload)


@router.get("/{kb_id}", response_model=KnowledgeBaseRead)
def get_item(
    kb_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> KnowledgeBaseRead:
    return get_knowledge_base(db, current_user.id, kb_id)


@router.patch("/{kb_id}", response_model=KnowledgeBaseRead)
def update_item(
    kb_id: str,
    payload: KnowledgeBaseUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> KnowledgeBaseRead:
    return update_knowledge_base(db, current_user.id, kb_id, payload)


@router.delete("/{kb_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item(
    kb_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    delete_knowledge_base(db, current_user.id, kb_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
