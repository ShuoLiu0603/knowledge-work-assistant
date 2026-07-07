from __future__ import annotations

from sqlalchemy.orm import Session

from app.agents.rag_agent import answer_with_rag
from app.agents.state import AgentGraphState, add_trace
from app.llm.provider import get_llm_provider
from app.services.llm_log_service import create_llm_call_log


def summarize_with_rag(db: Session, state: AgentGraphState) -> AgentGraphState:
    token_callback = state.token_callback
    state.token_callback = None
    try:
        answer_with_rag(db, state)
    finally:
        state.token_callback = token_callback
    source_answer = state.answer
    completion = get_llm_provider().summarize_with_metadata(memory_context_prompt(state, source_answer))
    llm_log = create_llm_call_log(
        db,
        completion,
        user_id=state.user_id,
        conversation_id=state.conversation_id,
        agent_name="summary_agent",
    )
    state.answer = completion.content.strip()
    state.llm_log_id = llm_log.id
    state.llm_log_ids.append(llm_log.id)
    add_trace(
        state,
        node="summary_agent",
        action="summarize_rag_answer",
        input_data={"source_answer_chars": len(source_answer)},
        output_data={"summary_chars": len(state.answer), "llm_log_id": llm_log.id},
    )
    return state


def memory_context_prompt(state: AgentGraphState, answer: str) -> str:
    return f"{state.memory_context or '无'}\n\nRAG 依据:\n{answer}"
