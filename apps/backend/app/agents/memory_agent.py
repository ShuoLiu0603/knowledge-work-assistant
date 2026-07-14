from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.agents.state import AgentRunState, add_trace
from app.core.config import get_settings
from app.db.models.conversation import Conversation
from app.memory.jobs import (
    claim_memory_update_job_dispatch,
    create_memory_update_job,
    dispatch_memory_update_job,
    record_memory_update_job_dispatch_failure,
)
from app.services.memory_service import (
    build_memory_context_for_question,
    filter_memory_history_messages,
    format_memory_context,
    get_conversation_memory_context_messages,
    get_recent_db_messages,
    get_short_term_memory,
    list_core_profile_context,
    process_user_memory,
    retrieve_relevant_memories,
    should_skip_memory_for_turn,
    to_memory_action_dict,
)

ALLOWED_MEMORY_UPDATE_MODES = {"sync", "async", "disabled"}
PROFILE_MEMORY_LIMIT = get_settings().memory_profile_limit
SEMANTIC_MEMORY_LIMIT = get_settings().memory_semantic_limit


def load_core_memory_context(db: Session, state: AgentRunState) -> AgentRunState:
    if state.memory_enabled is None:
        state.memory_enabled = not should_skip_memory_for_turn(state.input)
    if not state.memory_enabled:
        state.conversation_summary = None
        state.short_term_memory = []
        state.profile_memories = []
        state.long_term_memories = []
        state.memory_context = format_memory_context([], [], None, profile_memories=[])
        state.core_memory_context = state.memory_context
        add_trace(
            state,
            node="memory_agent",
            action="load_context_skipped",
            input_data={"conversation_id": state.conversation_id},
            output_data={"reason": "user requested no memory for this turn"},
        )
        return state

    conversation = db.get(Conversation, state.conversation_id) if state.conversation_id else None
    if conversation is not None and conversation.user_id != state.user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    state.conversation_summary = conversation.summary if conversation else None
    cached_short_memory = get_short_term_memory(state.user_id, state.conversation_id)
    if conversation is not None:
        state.short_term_memory, history_filter = get_conversation_memory_context_messages(
            db,
            conversation,
            current_input=state.input,
            current_message_id=state.message_id,
            fallback_messages=cached_short_memory,
        )
    else:
        state.short_term_memory = cached_short_memory or get_recent_db_messages(db, state.conversation_id)
        state.short_term_memory, history_filter = filter_memory_history(
            state.short_term_memory,
            current_input=state.input,
            current_message_id=state.message_id,
        )

    state.profile_memories = list_core_profile_context(db, state.user_id, limit=PROFILE_MEMORY_LIMIT)
    state.long_term_memories = []
    state.memory_context = build_memory_context_for_question(
        db,
        state.user_id,
        state.input,
        conversation_id=state.conversation_id,
        preloaded_short_memory=state.short_term_memory,
        preloaded_long_memories=state.long_term_memories,
        preloaded_profile_memories=state.profile_memories,
        conversation_summary=state.conversation_summary,
    )
    state.core_memory_context = state.memory_context
    add_trace(
        state,
        node="memory_agent",
        action="load_core_context",
        input_data={"conversation_id": state.conversation_id},
        output_data={
            "short_term_memory_count": len(state.short_term_memory),
            "profile_memory_count": len(state.profile_memories),
            "long_term_memory_count": len(state.long_term_memories),
            "has_conversation_summary": bool(state.conversation_summary),
            "memory_context_chars": len(state.memory_context),
            **history_filter,
        },
    )
    return state


def recall_long_term_memory(db: Session, state: AgentRunState, query: str) -> list[dict]:
    if not state.memory_enabled:
        return []
    memories = retrieve_relevant_memories(
        db,
        state.user_id,
        query,
        limit=SEMANTIC_MEMORY_LIMIT,
        conversation_id=state.conversation_id,
        message_id=state.message_id,
        include_profile=False,
    )
    recalled = [memory_to_dict(memory) for memory in memories]
    existing_ids = {memory.get("id") for memory in state.long_term_memories}
    for memory in recalled:
        if memory.get("id") in existing_ids:
            continue
        state.long_term_memories.append(memory)
        existing_ids.add(memory.get("id"))
    state.memory_recalled = True
    state.memory_context = build_memory_context_for_question(
        db,
        state.user_id,
        state.input,
        conversation_id=state.conversation_id,
        preloaded_short_memory=state.short_term_memory,
        preloaded_long_memories=state.long_term_memories,
        preloaded_profile_memories=state.profile_memories,
        conversation_summary=state.conversation_summary,
    )
    return recalled


def filter_memory_history(
    messages: list[dict],
    *,
    current_input: str,
    current_message_id: str | None = None,
) -> tuple[list[dict], dict]:
    return filter_memory_history_messages(
        messages,
        current_input=current_input,
        current_message_id=current_message_id,
    )


def memory_to_dict(memory) -> dict:
    return {
        "id": memory.id,
        "content": memory.content,
        "category": memory.category,
        "kind": memory.kind,
        "status": memory.status,
        "memory_layer": memory.memory_layer,
        "canonical_key": memory.canonical_key,
        "profile_slot": memory.profile_slot,
        "scope_type": memory.scope_type,
        "scope_id": memory.scope_id,
        "pinned": memory.pinned,
        "revision": memory.revision,
        "metadata": {
            **(memory.extra_metadata or {}),
            "canonical_key": memory.canonical_key,
            "memory_layer": memory.memory_layer,
            "profile_slot": memory.profile_slot,
        },
    }


def update_user_memories(db: Session, state: AgentRunState) -> AgentRunState:
    if state.defer_memory_update:
        state.memory_actions = [
            {
                "action": "deferred",
                "memory_id": None,
                "content": "",
                "reason": "memory update deferred until the conversation turn is committed",
            }
        ]
        add_trace(
            state,
            node="memory_agent",
            action="defer_user_memories",
            input_data={"conversation_id": state.conversation_id},
            output_data={"reason": "awaiting committed assistant message"},
        )
        return state
    if state.memory_enabled is None:
        state.memory_enabled = not should_skip_memory_for_turn(state.input)
    if not state.memory_enabled:
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
            respect_no_memory_marker=False,
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


def enqueue_user_memory_update(db: Session, state: AgentRunState) -> AgentRunState:
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
        if claim_memory_update_job_dispatch(db, job_id):
            enqueue_memory_update(job_id)
    except Exception as exc:
        dispatch_error = str(exc)
        try:
            record_memory_update_job_dispatch_failure(db, job_id, exc)
        except Exception as persistence_exc:
            db.rollback()
            dispatch_error = f"{dispatch_error}; failed to persist dispatch error: {persistence_exc}"

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
