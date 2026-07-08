from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models.user import User
from app.db.models.user_memory import UserMemory, UserMemoryEvent, UserMemoryRecallLog, UserMemoryUpdateJob
from app.db.session import get_db
from app.schemas.memory import (
    UserMemoryCreate,
    UserMemoryEventRead,
    UserMemoryExportRead,
    UserMemoryRead,
    UserMemoryRecallMetricsRead,
    UserMemoryRecallLogRead,
    UserMemoryUpdate,
    UserMemoryUpdateJobRead,
)
from app.services.memory_service import (
    approve_user_memory,
    create_manual_memory,
    delete_user_memory,
    export_user_memory_data,
    get_user_memory_recall_metrics,
    list_user_memory_update_jobs,
    list_user_memories,
    purge_user_memory,
    reject_user_memory,
    restore_user_memory,
    retry_user_memory_update_job,
    update_user_memory,
)

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


@router.get("/export", response_model=UserMemoryExportRead)
def export_items(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> UserMemoryExportRead:
    export = export_user_memory_data(db, current_user.id)
    return UserMemoryExportRead(
        user_id=export["user_id"],
        exported_at=export["exported_at"],
        memories=[to_memory_read(memory) for memory in export["memories"]],
        events=[to_memory_event_read(event) for event in export["events"]],
        recall_logs=[to_memory_recall_log_read(log) for log in export["recall_logs"]],
        update_jobs=[to_memory_update_job_read(job) for job in export["update_jobs"]],
    )


@router.get("/recall-metrics", response_model=UserMemoryRecallMetricsRead)
def get_recall_metrics(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> UserMemoryRecallMetricsRead:
    return UserMemoryRecallMetricsRead(**get_user_memory_recall_metrics(db, current_user.id))


@router.get("/update-jobs", response_model=list[UserMemoryUpdateJobRead])
def list_update_jobs(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    status: Annotated[str | None, Query()] = None,
) -> list[UserMemoryUpdateJobRead]:
    jobs = list_user_memory_update_jobs(db, current_user.id, status=status)
    return [to_memory_update_job_read(job) for job in jobs]


@router.post("/update-jobs/{job_id}/retry", response_model=UserMemoryUpdateJobRead)
def retry_update_job(
    job_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> UserMemoryUpdateJobRead:
    job = retry_user_memory_update_job(db, current_user.id, job_id)
    return to_memory_update_job_read(job)


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


@router.post("/{memory_id}/approve", response_model=UserMemoryRead)
def approve_item(
    memory_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> UserMemoryRead:
    memory = approve_user_memory(db, current_user.id, memory_id)
    return to_memory_read(memory)


@router.post("/{memory_id}/reject", response_model=UserMemoryRead)
def reject_item(
    memory_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> UserMemoryRead:
    memory = reject_user_memory(db, current_user.id, memory_id)
    return to_memory_read(memory)


@router.post("/{memory_id}/restore", response_model=UserMemoryRead)
def restore_item(
    memory_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> UserMemoryRead:
    memory = restore_user_memory(db, current_user.id, memory_id)
    return to_memory_read(memory)


@router.delete("/{memory_id}/purge", status_code=status.HTTP_204_NO_CONTENT)
def purge_item(
    memory_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    purge_user_memory(db, current_user.id, memory_id)


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
        source_conversation_id=memory.source_conversation_id,
        source_message_id=memory.source_message_id,
        embedding_model=memory.embedding_model,
        embedding_dimension=memory.embedding_dimension,
        merge_count=memory.merge_count,
        touched_count=memory.touched_count,
        superseded_by_id=memory.superseded_by_id,
        metadata=memory.extra_metadata,
        valid_at=memory.valid_at,
        invalid_at=memory.invalid_at,
        created_at=memory.created_at,
        updated_at=memory.updated_at,
        last_touched_at=memory.last_touched_at,
    )


def to_memory_update_job_read(job: UserMemoryUpdateJob) -> UserMemoryUpdateJobRead:
    return UserMemoryUpdateJobRead(
        id=job.id,
        user_id=job.user_id,
        conversation_id=job.conversation_id,
        message_id=job.message_id,
        user_message=job.user_message,
        assistant_message=job.assistant_message,
        status=job.status,
        attempts=job.attempts,
        actions=job.actions,
        error_message=job.error_message,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def to_memory_event_read(event: UserMemoryEvent) -> UserMemoryEventRead:
    return UserMemoryEventRead(
        id=event.id,
        user_id=event.user_id,
        memory_id=event.memory_id,
        event_type=event.event_type,
        actor_type=event.actor_type,
        actor_user_id=event.actor_user_id,
        source=event.source,
        reason=event.reason,
        previous_status=event.previous_status,
        new_status=event.new_status,
        payload=event.payload,
        created_at=event.created_at,
    )


def to_memory_recall_log_read(log: UserMemoryRecallLog) -> UserMemoryRecallLogRead:
    return UserMemoryRecallLogRead(
        id=log.id,
        user_id=log.user_id,
        conversation_id=log.conversation_id,
        message_id=log.message_id,
        query=log.query,
        recall_mode=log.recall_mode,
        requested_limit=log.requested_limit,
        recall_limit=log.recall_limit,
        active_count=log.active_count,
        selected_count=log.selected_count,
        threshold=log.threshold,
        candidates=log.candidates,
        selected_memory_ids=log.selected_memory_ids,
        created_at=log.created_at,
    )
