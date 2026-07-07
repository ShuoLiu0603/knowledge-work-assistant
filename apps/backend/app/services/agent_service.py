from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.graph import run_agent_graph
from app.agents.state import AgentGraphState
from app.db.models.agent_run import AgentRun
from app.schemas.agent import AgentRunRead
from app.schemas.qa import CitationRead
from app.services.knowledge_base_service import ensure_kb_access


def run_agent(
    db: Session,
    user_id: str,
    knowledge_base_id: str | None,
    input_text: str,
    top_k: int | None = None,
    search_scope: str = "single",
    department_id: str | None = None,
    conversation_id: str | None = None,
    message_id: str | None = None,
    on_token: Callable[[str], None] | None = None,
) -> AgentRun:
    state = AgentGraphState(
        user_id=user_id,
        knowledge_base_id=knowledge_base_id,
        input=input_text.strip(),
        top_k=top_k,
        search_scope=search_scope,
        search_department_id=department_id,
        conversation_id=conversation_id,
        message_id=message_id,
        token_callback=on_token,
    )
    started_at = datetime.now(timezone.utc)
    run_agent_graph(db, state)

    run = AgentRun(
        user_id=user_id,
        knowledge_base_id=knowledge_base_id,
        conversation_id=conversation_id,
        message_id=message_id,
        retrieval_log_id=state.retrieval_log_id,
        input=state.input,
        intent=state.intent,
        status=state.status,
        answer=state.answer,
        citations=[citation.model_dump(mode="json") for citation in state.citations],
        trace=state.trace,
        state=state_snapshot(state, started_at),
        error_message=state.error_message,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    if run.status == "failed":
        raise RuntimeError(run.error_message or "Agent run failed")
    return run


def attach_agent_run_to_message(db: Session, run: AgentRun, message_id: str) -> AgentRun:
    run.message_id = message_id
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def list_agent_runs(
    db: Session,
    user_id: str,
    knowledge_base_id: str | None = None,
    conversation_id: str | None = None,
    message_id: str | None = None,
) -> list[AgentRunRead]:
    query = select(AgentRun).where(AgentRun.user_id == user_id)
    if knowledge_base_id:
        ensure_kb_access(db, user_id, knowledge_base_id, required_role="viewer")
        query = query.where(AgentRun.knowledge_base_id == knowledge_base_id)
    if conversation_id:
        query = query.where(AgentRun.conversation_id == conversation_id)
    if message_id:
        query = query.where(AgentRun.message_id == message_id)

    runs = db.scalars(query.order_by(AgentRun.created_at.desc())).all()
    for run in runs:
        if run.knowledge_base_id:
            ensure_kb_access(db, user_id, run.knowledge_base_id, required_role="viewer")
    return [to_agent_run_read(run) for run in runs]


def get_agent_run(db: Session, user_id: str, run_id: str) -> AgentRunRead:
    run = db.get(AgentRun, run_id)
    if run is None or run.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent run not found")
    if run.knowledge_base_id:
        ensure_kb_access(db, user_id, run.knowledge_base_id, required_role="viewer")
    return to_agent_run_read(run)


def to_agent_run_read(run: AgentRun) -> AgentRunRead:
    return AgentRunRead(
        id=run.id,
        user_id=run.user_id,
        knowledge_base_id=run.knowledge_base_id,
        conversation_id=run.conversation_id,
        message_id=run.message_id,
        retrieval_log_id=run.retrieval_log_id,
        input=run.input,
        intent=run.intent,
        status=run.status,
        answer=run.answer,
        citations=[CitationRead(**citation) for citation in run.citations],
        trace=run.trace,
        state=run.state,
        error_message=run.error_message,
        created_at=run.created_at,
        updated_at=run.updated_at,
    )


def state_snapshot(state: AgentGraphState, started_at: datetime) -> dict:
    return {
        "input": state.input,
        "intent": state.intent,
        "status": state.status,
        "conversation_id": state.conversation_id,
        "message_id": state.message_id,
        "search_scope": state.search_scope,
        "search_department_id": state.search_department_id,
        "retrieval_log_id": state.retrieval_log_id,
        "llm_log_id": state.llm_log_id,
        "llm_log_ids": state.llm_log_ids,
        "citation_count": len(state.citations),
        "conversation_summary": state.conversation_summary,
        "short_term_memory_count": len(state.short_term_memory),
        "long_term_memories": state.long_term_memories,
        "memory_actions": state.memory_actions,
        "started_at": started_at.isoformat(),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
