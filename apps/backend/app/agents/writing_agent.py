from __future__ import annotations

from sqlalchemy.orm import Session

from app.agents.rag_agent import answer_with_rag
from app.agents.state import AgentGraphState, add_trace
from app.agents.summary_agent import memory_context_prompt
from app.llm.provider import get_llm_provider
from app.services.llm_log_service import create_llm_call_log


def draft_with_rag(db: Session, state: AgentGraphState) -> AgentGraphState:
    token_callback = state.token_callback
    state.token_callback = None
    try:
        answer_with_rag(db, state)
    finally:
        state.token_callback = token_callback
    grounding = state.answer
    completion = get_llm_provider().draft_with_metadata(state.input, memory_context_prompt(state, grounding))
    llm_log = create_llm_call_log(
        db,
        completion,
        user_id=state.user_id,
        conversation_id=state.conversation_id,
        agent_name="writing_agent",
    )
    state.answer = completion.content.strip()
    state.llm_log_id = llm_log.id
    state.llm_log_ids.append(llm_log.id)
    add_trace(
        state,
        node="writing_agent",
        action="draft_with_rag_grounding",
        input_data={"writing_request": state.input},
        output_data={"draft_chars": len(state.answer), "llm_log_id": llm_log.id},
    )
    return state
