from __future__ import annotations

from sqlalchemy.orm import Session

from app.agents.state import AgentGraphState, add_trace
from app.db.models.conversation import Conversation
from app.services.memory_service import (
    build_memory_context_for_question,
    get_recent_db_messages,
    get_short_term_memory,
    process_user_memory,
    retrieve_relevant_memories,
    to_memory_action_dict,
)


def load_memory_context(db: Session, state: AgentGraphState) -> AgentGraphState:
    conversation = db.get(Conversation, state.conversation_id) if state.conversation_id else None
    state.conversation_summary = conversation.summary if conversation else None
    state.short_term_memory = get_short_term_memory(state.user_id, state.conversation_id)
    if not state.short_term_memory:
        state.short_term_memory = get_recent_db_messages(db, state.conversation_id)

    memories = retrieve_relevant_memories(db, state.user_id, state.input)
    state.long_term_memories = [
        {
            "id": memory.id,
            "content": memory.content,
            "category": memory.category,
            "status": memory.status,
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
    try:
        actions = process_user_memory(
            db,
            state.user_id,
            state.input,
            conversation_id=state.conversation_id,
            assistant_text=state.answer,
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
