from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from threading import Event

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.runtime import run_agent_turn
from app.agents.memory_agent import update_user_memories
from app.agents.state import AgentRunState, ensure_agent_run_active
from app.db.models.agent_run import AgentRun
from app.db.models.conversation import Conversation, Message
from app.db.models.retrieval_log import RetrievalLog
from app.schemas.agent import AgentRunRead
from app.schemas.qa import CitationRead
from app.services.knowledge_base_service import ensure_kb_access, resolve_search_scope
from app.services.retrieval_log_service import ensure_retrieval_log_access, retrieval_log_provenance_ids


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
    cancel_event: Event | None = None,
    deadline_monotonic: float | None = None,
    defer_memory_update: bool = False,
    memory_enabled: bool | None = None,
) -> AgentRun:
    normalized_input = input_text.strip()
    if not normalized_input:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Agent input cannot be empty")
    conversation = None
    if conversation_id:
        conversation = db.get(Conversation, conversation_id)
        if conversation is None or conversation.user_id != user_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    scope = resolve_search_scope(
        db,
        user_id,
        knowledge_base_id,
        scope_type=search_scope,
        department_id=department_id,
    )
    state = AgentRunState(
        user_id=user_id,
        knowledge_base_id=scope.primary_knowledge_base_id,
        input=normalized_input,
        top_k=top_k,
        search_scope=scope.scope_type,
        search_department_id=scope.department_id,
        conversation_id=conversation_id,
        message_id=message_id,
        token_callback=on_token,
        cancel_event=cancel_event,
        deadline_monotonic=deadline_monotonic,
        defer_memory_update=defer_memory_update,
        memory_enabled=memory_enabled,
        searched_knowledge_base_ids=[],
    )
    started_at = datetime.now(timezone.utc)
    run_agent_turn(db, state)
    ensure_agent_run_active(state)
    retrieval_log = None
    if state.retrieval_log_id:
        retrieval_log = db.get(RetrievalLog, state.retrieval_log_id)
        if retrieval_log is not None:
            state.searched_knowledge_base_ids = list(
                dict.fromkeys(
                    [
                        *state.searched_knowledge_base_ids,
                        *list(retrieval_log.searched_knowledge_base_ids or []),
                    ]
                )
            )
    if (
        state.status == "completed"
        and state.rag_searched
        and retrieval_log is None
    ):
        state.status = "failed"
        state.retrieval_log_id = None
        state.answer = ""
        state.citations = []
        state.error_message = "Completed retrieval Agent run is missing search provenance"
    if state.status == "failed":
        db.rollback()

    if conversation is not None:
        conversation.searched_knowledge_base_ids = list(
            dict.fromkeys(
                [
                    *list(conversation.searched_knowledge_base_ids or []),
                    *state.searched_knowledge_base_ids,
                ]
            )
        )
        db.add(conversation)

    run = AgentRun(
        user_id=user_id,
        knowledge_base_id=scope.primary_knowledge_base_id,
        conversation_id=conversation_id,
        message_id=message_id,
        retrieval_log_id=state.retrieval_log_id,
        input=state.input,
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


def apply_deferred_memory_update(
    db: Session,
    run: AgentRun,
    *,
    source_message_id: str | None,
) -> AgentRun:
    if run.status != "completed":
        return run
    stored_state = run.state if isinstance(run.state, dict) else {}
    source_message = db.get(Message, source_message_id) if source_message_id else None
    memory_enabled = (
        source_message.memory_enabled
        if source_message is not None
        else bool(stored_state.get("memory_enabled", True))
    )
    state = AgentRunState(
        user_id=run.user_id,
        knowledge_base_id=run.knowledge_base_id,
        input=run.input,
        search_scope=str(stored_state.get("search_scope") or "single"),
        search_department_id=stored_state.get("search_department_id"),
        conversation_id=run.conversation_id,
        message_id=source_message_id,
        answer=run.answer,
        trace=list(run.trace or []),
        status=run.status,
        searched_knowledge_base_ids=stored_searched_knowledge_base_ids(run),
        memory_enabled=memory_enabled,
    )
    update_user_memories(db, state)
    run.trace = state.trace
    run.state = {
        **stored_state,
        "memory_actions": state.memory_actions,
        "memory_enabled": state.memory_enabled,
    }
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
    limit: int = 50,
    offset: int = 0,
) -> list[AgentRunRead]:
    query = select(AgentRun).where(AgentRun.user_id == user_id)
    if knowledge_base_id:
        ensure_kb_access(db, user_id, knowledge_base_id, required_role="viewer")
        query = query.where(AgentRun.knowledge_base_id == knowledge_base_id)
    if conversation_id:
        query = query.where(AgentRun.conversation_id == conversation_id)
    if message_id:
        query = query.where(AgentRun.message_id == message_id)

    bounded_limit = max(1, min(limit, 200))
    bounded_offset = max(offset, 0)
    ordered_query = query.order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
    visible_runs: list[AgentRun] = []
    scan_offset = 0
    batch_size = 200
    target_count = bounded_offset + bounded_limit
    while len(visible_runs) < target_count:
        batch = db.scalars(ordered_query.offset(scan_offset).limit(batch_size)).all()
        if not batch:
            break
        scan_offset += len(batch)
        for run in batch:
            try:
                ensure_agent_run_access(db, user_id, run)
            except HTTPException as exc:
                if exc.status_code in {status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND}:
                    continue
                raise
            visible_runs.append(run)
            if len(visible_runs) >= target_count:
                break
        if len(batch) < batch_size:
            break
    return [to_agent_run_read(run) for run in visible_runs[bounded_offset:target_count]]


def get_agent_run(db: Session, user_id: str, run_id: str) -> AgentRunRead:
    run = db.get(AgentRun, run_id)
    if run is None or run.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent run not found")
    ensure_agent_run_access(db, user_id, run)
    return to_agent_run_read(run)


def to_agent_run_read(run: AgentRun) -> AgentRunRead:
    return AgentRunRead(
        id=run.id,
        user_id=run.user_id,
        knowledge_base_id=run.knowledge_base_id,
        conversation_id=run.conversation_id,
        message_id=run.message_id,
        retrieval_log_id=run.retrieval_log_id,
        retrieval_log_ids=stored_retrieval_log_ids(run),
        searched_knowledge_base_ids=stored_searched_knowledge_base_ids(run),
        input=run.input,
        status=run.status,
        answer=run.answer,
        citations=[CitationRead(**citation) for citation in run.citations],
        trace=run.trace,
        state=run.state,
        error_message=run.error_message,
        created_at=run.created_at,
        updated_at=run.updated_at,
    )


def state_snapshot(state: AgentRunState, started_at: datetime) -> dict:
    return {
        "input": state.input,
        "status": state.status,
        "conversation_id": state.conversation_id,
        "message_id": state.message_id,
        "search_scope": state.search_scope,
        "search_department_id": state.search_department_id,
        "searched_knowledge_base_ids": state.searched_knowledge_base_ids,
        "retrieval_log_id": state.retrieval_log_id,
        "retrieval_log_ids": state.retrieval_log_ids,
        "llm_log_id": state.llm_log_id,
        "llm_log_ids": state.llm_log_ids,
        "citation_count": len(state.citations),
        "conversation_summary": state.conversation_summary,
        "short_term_memory_count": len(state.short_term_memory),
        "profile_memory_count": len(state.profile_memories),
        "profile_memory_ids": [memory.get("id") for memory in state.profile_memories],
        "long_term_memory_count": len(state.long_term_memories),
        "long_term_memory_ids": [memory.get("id") for memory in state.long_term_memories],
        "memory_recalled": state.memory_recalled,
        "rag_searched": state.rag_searched,
        "rag_chunk_count": len(state.rag_chunks),
        "rag_chunk_ids": [chunk.chunk_id for chunk in state.rag_chunks],
        "model_call_count": state.model_call_count,
        "tool_call_count": state.tool_call_count,
        "memory_tool_call_count": state.memory_tool_call_count,
        "rag_tool_call_count": state.rag_tool_call_count,
        "memory_queries": state.memory_queries,
        "rag_queries": state.rag_queries,
        "tool_history": [
            {
                key: observation.get(key)
                for key in (
                    "tool",
                    "query",
                    "status",
                    "result_count",
                    "new_result_count",
                    "duplicate_result_count",
                    "retrieval_log_id",
                    "error",
                )
                if observation.get(key) is not None
            }
            for observation in state.tool_observations
        ],
        "memory_actions": state.memory_actions,
        "memory_enabled": state.memory_enabled,
        "started_at": started_at.isoformat(),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


def ensure_agent_run_access(db: Session, user_id: str, run: AgentRun) -> None:
    state = run.state if isinstance(run.state, dict) else {}
    has_explicit_provenance = isinstance(state.get("searched_knowledge_base_ids"), list)
    knowledge_base_ids = stored_searched_knowledge_base_ids(run)
    retrieval_log = None
    if run.retrieval_log_id:
        retrieval_log = db.get(RetrievalLog, run.retrieval_log_id)
        if retrieval_log is not None:
            ensure_retrieval_log_access(db, user_id, retrieval_log)
            knowledge_base_ids.extend(retrieval_log_provenance_ids(retrieval_log))
    if not has_explicit_provenance and not run.knowledge_base_id and retrieval_log is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent run provenance is unavailable",
        )
    for knowledge_base_id in dict.fromkeys(knowledge_base_ids):
        ensure_kb_access(db, user_id, knowledge_base_id, required_role="viewer")


def stored_searched_knowledge_base_ids(run: AgentRun) -> list[str]:
    state = run.state if isinstance(run.state, dict) else {}
    stored = state.get("searched_knowledge_base_ids")
    if isinstance(stored, list):
        normalized = [value for value in stored if isinstance(value, str) and value]
        return list(dict.fromkeys(normalized))
    return [run.knowledge_base_id] if run.knowledge_base_id else []


def stored_retrieval_log_ids(run: AgentRun) -> list[str]:
    state = run.state if isinstance(run.state, dict) else {}
    stored = state.get("retrieval_log_ids")
    normalized = [value for value in stored if isinstance(value, str) and value] if isinstance(stored, list) else []
    if run.retrieval_log_id:
        normalized.append(run.retrieval_log_id)
    return list(dict.fromkeys(normalized))
