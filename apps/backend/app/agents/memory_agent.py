from __future__ import annotations

from sqlalchemy.orm import Session

from app.agents.state import AgentGraphState, add_trace
from app.core.config import get_settings
from app.db.models.conversation import Conversation
from app.memory.jobs import create_memory_update_job, dispatch_memory_update_job
from app.services.memory_service import (
    build_memory_context_for_question,
    get_recent_db_messages,
    get_short_term_memory,
    process_user_memory,
    retrieve_relevant_memories,
    should_skip_memory_for_turn,
    to_memory_action_dict,
)

ALLOWED_MEMORY_UPDATE_MODES = {"sync", "async", "disabled"}


def load_memory_context(db: Session, state: AgentGraphState) -> AgentGraphState:
    if should_skip_memory_for_turn(state.input):
        state.conversation_summary = None
        state.short_term_memory = []
        state.long_term_memories = []
        state.memory_context = build_memory_context_for_question(
            db,
            state.user_id,
            state.input,
            conversation_id=state.conversation_id,
            preloaded_short_memory=[],
            preloaded_long_memories=[],
            conversation_summary=None,
        )
        add_trace(
            state,
            node="memory_agent",
            action="load_context_skipped",
            input_data={"conversation_id": state.conversation_id},
            output_data={"reason": "user requested no memory for this turn"},
        )
        return state

    conversation = db.get(Conversation, state.conversation_id) if state.conversation_id else None
    state.conversation_summary = conversation.summary if conversation else None
    state.short_term_memory = get_short_term_memory(state.user_id, state.conversation_id)
    if not state.short_term_memory:
        state.short_term_memory = get_recent_db_messages(db, state.conversation_id)

    memories = retrieve_relevant_memories(
        db,
        state.user_id,
        state.input,
        conversation_id=state.conversation_id,
        message_id=state.message_id,
    )
    state.long_term_memories = [
        {
            "id": memory.id,
            "content": memory.content,
            "category": memory.category,
            "kind": memory.kind,
            "status": memory.status,
            "metadata": memory.extra_metadata or {},
        }
        for memory in memories
    ]
    state.memory_context = build_memory_context_for_question(
        db,
        state.user_id,
        state.input,
        conversation_id=state.conversation_id,
        preloaded_short_memory=state.short_term_memory,
        preloaded_long_memories=state.long_term_memories,
        conversation_summary=state.conversation_summary,
    )
    add_trace(
        state,
        node="memory_agent",
        action="load_context",
        input_data={"conversation_id": state.conversation_id},
        output_data={
            "short_term_memory_count": len(state.short_term_memory),
            "long_term_memory_count": len(state.long_term_memories),
            "has_conversation_summary": bool(state.conversation_summary),
            "memory_context_chars": len(state.memory_context),
        },
    )
    return state


def update_user_memories(db: Session, state: AgentGraphState) -> AgentGraphState:
    if should_skip_memory_for_turn(state.input):
        state.memory_actions = [
            {
                "action": "ignore",
                "memory_id": None,
                "content": "",
                "reason": "user requested no memory for this turn",
            }
        ]
        add_trace(
            state,
            node="memory_agent",
            action="update_user_memories_skipped",
            input_data={"input": state.input},
            output_data={"reason": "user requested no memory for this turn"},
        )
        return state

    mode = memory_update_mode()
    if mode == "disabled":
        state.memory_actions = [
            {
                "action": "ignore",
                "memory_id": None,
                "content": "",
                "reason": "memory update disabled",
            }
        ]
        add_trace(
            state,
            node="memory_agent",
            action="update_user_memories_disabled",
            input_data={"input": state.input},
            output_data={"mode": mode},
        )
        return state
    if mode == "async":
        return enqueue_user_memory_update(db, state)

    try:
        actions = process_user_memory(
            db,
            state.user_id,
            state.input,
            conversation_id=state.conversation_id,
            assistant_text=state.answer,
            message_id=state.message_id,
        )
        state.memory_actions = [to_memory_action_dict(action) for action in actions]
    except Exception as exc:
        db.rollback()
        state.memory_actions = [
            {
                "action": "ignore",
                "memory_id": None,
                "content": "",
                "reason": f"memory update failed: {exc}",
            }
        ]
        add_trace(
            state,
            node="memory_agent",
            action="update_user_memories_failed",
            input_data={"input": state.input},
            output_data={"error_message": str(exc)},
        )
        return state
    add_trace(
        state,
        node="memory_agent",
        action="update_user_memories",
        input_data={"input": state.input},
        output_data={"actions": state.memory_actions},
    )
    return state


def enqueue_user_memory_update(db: Session, state: AgentGraphState) -> AgentGraphState:
    try:
        job = create_memory_update_job(
            db,
            user_id=state.user_id,
            text=state.input,
            conversation_id=state.conversation_id,
            message_id=state.message_id,
            assistant_text=state.answer,
        )
    except Exception as exc:
        state.memory_actions = [
            {
                "action": "ignore",
                "memory_id": None,
                "content": "",
                "reason": f"memory update enqueue failed: {exc}",
            }
        ]
        add_trace(
            state,
            node="memory_agent",
            action="enqueue_user_memories_failed",
            input_data={"input": state.input, "conversation_id": state.conversation_id},
            output_data={"error_message": str(exc)},
        )
        return state

    job_id = job.id
    dispatch_error = ""
    try:
        enqueue_memory_update(job_id)
    except Exception as exc:
        dispatch_error = str(exc)

    state.memory_actions = [
        {
            "action": "queued",
            "memory_id": None,
            "job_id": job_id,
            "content": "",
            "reason": "memory update queued" if not dispatch_error else f"memory update queued; worker dispatch failed: {dispatch_error}",
        }
    ]
    add_trace(
        state,
        node="memory_agent",
        action="enqueue_user_memories",
        input_data={"input": state.input, "conversation_id": state.conversation_id},
        output_data={"queued": True, "job_id": job_id, "dispatch_error": dispatch_error},
    )
    return state


def enqueue_memory_update(job_id: str) -> None:
    dispatch_memory_update_job(job_id)


def memory_update_mode() -> str:
    mode = get_settings().memory_update_mode.strip().lower()
    return mode if mode in ALLOWED_MEMORY_UPDATE_MODES else "sync"
