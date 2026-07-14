from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.retrieval_log import RetrievalLog
from app.rag.advanced_retrieval import AdvancedRetrievalResult
from app.schemas.retrieval_log import RetrievalLogRead
from app.services.knowledge_base_service import ensure_kb_access


def create_retrieval_log(
    db: Session,
    user_id: str,
    kb_id: str | None,
    result: AdvancedRetrievalResult,
    conversation_id: str | None = None,
    message_id: str | None = None,
) -> RetrievalLog:
    log = RetrievalLog(
        user_id=user_id,
        knowledge_base_id=kb_id,
        scope_type=getattr(result, "scope_type", "single"),
        searched_knowledge_base_ids=getattr(result, "searched_knowledge_base_ids", [kb_id] if kb_id else []),
        conversation_id=conversation_id,
        message_id=message_id,
        query=result.query,
        retrieval_routes=result.retrieval_routes,
        candidates=result.candidates,
        selected_chunks=result.selected_chunk_logs,
        rrf_k=result.rrf_k,
        reranker_enabled=getattr(result, "reranker_enabled", False),
        compression_chars_saved=result.compression_chars_saved,
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


def attach_retrieval_log_to_message(db: Session, log: RetrievalLog, message_id: str) -> RetrievalLog:
    log.message_id = message_id
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


def list_retrieval_logs(
    db: Session,
    user_id: str,
    knowledge_base_id: str | None = None,
    conversation_id: str | None = None,
    message_id: str | None = None,
) -> list[RetrievalLogRead]:
    query = select(RetrievalLog).where(RetrievalLog.user_id == user_id)
    if knowledge_base_id:
        ensure_kb_access(db, user_id, knowledge_base_id, required_role="viewer")
        query = query.where(RetrievalLog.knowledge_base_id == knowledge_base_id)
    if conversation_id:
        query = query.where(RetrievalLog.conversation_id == conversation_id)
    if message_id:
        query = query.where(RetrievalLog.message_id == message_id)

    logs = db.scalars(query.order_by(RetrievalLog.created_at.desc(), RetrievalLog.id.desc())).all()
    visible_logs = []
    for log in logs:
        try:
            ensure_retrieval_log_access(db, user_id, log)
        except HTTPException as exc:
            if exc.status_code in {status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND}:
                continue
            raise
        visible_logs.append(log)
    return [to_retrieval_log_read(log) for log in visible_logs]


def get_retrieval_log(db: Session, user_id: str, log_id: str) -> RetrievalLogRead:
    log = db.get(RetrievalLog, log_id)
    if log is None or log.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Retrieval log not found")
    ensure_retrieval_log_access(db, user_id, log)
    return to_retrieval_log_read(log)


def ensure_retrieval_log_access(db: Session, user_id: str, log: RetrievalLog) -> None:
    if log.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Retrieval log not found")
    for knowledge_base_id in retrieval_log_provenance_ids(log):
        ensure_kb_access(db, user_id, knowledge_base_id, required_role="viewer")


def retrieval_log_provenance_ids(log: RetrievalLog) -> list[str]:
    knowledge_base_ids = normalized_knowledge_base_ids(log.searched_knowledge_base_ids)
    if log.knowledge_base_id:
        knowledge_base_ids.append(log.knowledge_base_id)
    declared_knowledge_base_ids = list(dict.fromkeys(knowledge_base_ids))
    can_attribute_legacy_rows = len(declared_knowledge_base_ids) == 1

    has_unattributed_retrieval_data = False
    for value in (log.candidates, log.selected_chunks):
        if not isinstance(value, list):
            if value:
                has_unattributed_retrieval_data = True
            continue
        for row in value:
            if not isinstance(row, dict):
                if row:
                    has_unattributed_retrieval_data = True
                continue
            knowledge_base_id = row.get("knowledge_base_id")
            if isinstance(knowledge_base_id, str) and knowledge_base_id:
                knowledge_base_ids.append(knowledge_base_id)
            elif row and not can_attribute_legacy_rows:
                has_unattributed_retrieval_data = True

    if has_unattributed_retrieval_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Retrieval log provenance is unavailable",
        )
    return list(dict.fromkeys(knowledge_base_ids))


def normalized_knowledge_base_ids(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def to_retrieval_log_read(log: RetrievalLog) -> RetrievalLogRead:
    return RetrievalLogRead(
        id=log.id,
        user_id=log.user_id,
        knowledge_base_id=log.knowledge_base_id,
        scope_type=log.scope_type or "single",
        searched_knowledge_base_ids=log.searched_knowledge_base_ids or ([log.knowledge_base_id] if log.knowledge_base_id else []),
        conversation_id=log.conversation_id,
        message_id=log.message_id,
        query=log.query,
        retrieval_routes=log.retrieval_routes,
        candidates=log.candidates,
        selected_chunks=log.selected_chunks,
        rrf_k=log.rrf_k,
        reranker_enabled=log.reranker_enabled,
        compression_chars_saved=log.compression_chars_saved,
        created_at=log.created_at,
    )
