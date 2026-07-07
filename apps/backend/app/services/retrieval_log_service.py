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
        question=result.question,
        rewritten_query=result.rewritten_query,
        sub_questions=result.sub_questions,
        expanded_queries=result.expanded_queries,
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

    logs = db.scalars(query.order_by(RetrievalLog.created_at.desc())).all()
    for log in logs:
        if log.knowledge_base_id:
            ensure_kb_access(db, user_id, log.knowledge_base_id, required_role="viewer")
    return [to_retrieval_log_read(log) for log in logs]


def get_retrieval_log(db: Session, user_id: str, log_id: str) -> RetrievalLogRead:
    log = db.get(RetrievalLog, log_id)
    if log is None or log.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Retrieval log not found")
    if log.knowledge_base_id:
        ensure_kb_access(db, user_id, log.knowledge_base_id, required_role="viewer")
    return to_retrieval_log_read(log)


def to_retrieval_log_read(log: RetrievalLog) -> RetrievalLogRead:
    return RetrievalLogRead(
        id=log.id,
        user_id=log.user_id,
        knowledge_base_id=log.knowledge_base_id,
        scope_type=log.scope_type or "single",
        searched_knowledge_base_ids=log.searched_knowledge_base_ids or ([log.knowledge_base_id] if log.knowledge_base_id else []),
        conversation_id=log.conversation_id,
        message_id=log.message_id,
        question=log.question,
        rewritten_query=log.rewritten_query,
        sub_questions=log.sub_questions,
        expanded_queries=log.expanded_queries,
        retrieval_routes=log.retrieval_routes,
        candidates=log.candidates,
        selected_chunks=log.selected_chunks,
        rrf_k=log.rrf_k,
        reranker_enabled=log.reranker_enabled,
        compression_chars_saved=log.compression_chars_saved,
        created_at=log.created_at,
    )
