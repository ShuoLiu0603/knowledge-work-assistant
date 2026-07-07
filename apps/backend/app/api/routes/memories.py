from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models.user import User
from app.db.models.user_memory import UserMemory
from app.db.session import get_db
from app.schemas.memory import UserMemoryCreate, UserMemoryRead, UserMemoryUpdate
from app.services.memory_service import create_manual_memory, delete_user_memory, list_user_memories, update_user_memory

router = APIRouter(prefix="/memories")


@router.get("", response_model=list[UserMemoryRead])
def list_items(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    status: Annotated[str | None, Query()] = None,
) -> list[UserMemoryRead]:
    memories = list_user_memories(db, current_user.id, status=status)
    return [to_memory_read(memory) for memory in memories]


@router.post("", response_model=UserMemoryRead, status_code=status.HTTP_201_CREATED)
def create_item(
    payload: UserMemoryCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> UserMemoryRead:
    memory = create_manual_memory(
        db,
        current_user.id,
        payload.content.strip(),
        category=payload.category or "general",
        kind=payload.kind,
    )
    return to_memory_read(memory)


@router.patch("/{memory_id}", response_model=UserMemoryRead)
def update_item(
    memory_id: str,
    payload: UserMemoryUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> UserMemoryRead:
    memory = update_user_memory(
        db,
        current_user.id,
        memory_id,
        content=payload.content.strip() if payload.content is not None else None,
        status=payload.status,
        category=payload.category,
        kind=payload.kind,
    )
    return to_memory_read(memory)


@router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item(
    memory_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    delete_user_memory(db, current_user.id, memory_id)


def to_memory_read(memory: UserMemory) -> UserMemoryRead:
    return UserMemoryRead(
        id=memory.id,
        user_id=memory.user_id,
        content=memory.content,
        content_hash=memory.content_hash,
        status=memory.status,
        kind=memory.kind,
        category=memory.category,
        source_text=memory.source_text,
        merge_count=memory.merge_count,
        touched_count=memory.touched_count,
        superseded_by_id=memory.superseded_by_id,
        metadata=memory.extra_metadata,
        created_at=memory.created_at,
        updated_at=memory.updated_at,
        last_touched_at=memory.last_touched_at,
    )
