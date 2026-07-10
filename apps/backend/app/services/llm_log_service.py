from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.llm_call_log import LlmCallLog
from app.llm.provider import LlmCompletion
from app.schemas.llm_log import LlmCallLogRead


def create_llm_call_log(
    db: Session,
    completion: LlmCompletion,
    user_id: str | None = None,
    conversation_id: str | None = None,
    agent_name: str | None = None,
    autocommit: bool = True,
) -> LlmCallLog:
    log = LlmCallLog(
        user_id=user_id,
        conversation_id=conversation_id,
        agent_name=agent_name,
        provider=completion.provider,
        model_name=completion.model_name,
        prompt_tokens=completion.prompt_tokens,
        completion_tokens=completion.completion_tokens,
        total_tokens=completion.total_tokens,
        latency_ms=completion.latency_ms,
        status=completion.status,
        fallback_used=completion.fallback_used,
        error_message=completion.error_message,
    )
    db.add(log)
    if autocommit:
        db.commit()
        db.refresh(log)
    else:
        db.flush()
    return log


def list_llm_call_logs(
    db: Session,
    user_id: str,
    conversation_id: str | None = None,
    agent_name: str | None = None,
    limit: int = 100,
) -> list[LlmCallLogRead]:
    query = select(LlmCallLog).where(LlmCallLog.user_id == user_id)
    if conversation_id:
        query = query.where(LlmCallLog.conversation_id == conversation_id)
    if agent_name:
        query = query.where(LlmCallLog.agent_name == agent_name)
    logs = db.scalars(query.order_by(LlmCallLog.created_at.desc()).limit(limit)).all()
    return [to_llm_call_log_read(log) for log in logs]


def get_llm_call_log(db: Session, user_id: str, log_id: str) -> LlmCallLogRead:
    log = db.get(LlmCallLog, log_id)
    if log is None or log.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="LLM call log not found")
    return to_llm_call_log_read(log)


def to_llm_call_log_read(log: LlmCallLog) -> LlmCallLogRead:
    return LlmCallLogRead(
        id=log.id,
        user_id=log.user_id,
        conversation_id=log.conversation_id,
        agent_name=log.agent_name,
        provider=log.provider,
        model_name=log.model_name,
        prompt_tokens=log.prompt_tokens,
        completion_tokens=log.completion_tokens,
        total_tokens=log.total_tokens,
        estimated_cost=float(log.estimated_cost) if log.estimated_cost is not None else None,
        latency_ms=log.latency_ms,
        status=log.status,
        fallback_used=log.fallback_used,
        error_message=log.error_message,
        created_at=log.created_at,
    )
