from __future__ import annotations

from sqlalchemy.orm import Session

from app.agents.state import AgentGraphState, add_trace, ensure_agent_run_active
from app.llm.provider import get_llm_provider, normalize_intent_label
from app.memory.policy import is_full_memory_recall_query
from app.services.llm_log_service import create_llm_call_log


def normalize_intent(raw_intent: str, _text: str = "") -> str:
    return normalize_intent_label(raw_intent)


def route_intent(db: Session, state: AgentGraphState) -> AgentGraphState:
    if is_full_memory_recall_query(state.input):
        state.intent = "memory"
        add_trace(
            state,
            node="supervisor",
            action="route_full_memory_recall",
            input_data={"input": state.input},
            output_data={"intent": state.intent, "deterministic": True},
        )
        return state

    provider = get_llm_provider()
    raw_intent = "rag"
    llm_log_id = None
    if hasattr(provider, "classify_intent_with_metadata"):
        classification = provider.classify_intent_with_metadata(state.input)
        ensure_agent_run_active(state)
        raw_intent = classification.intent
        llm_log = create_llm_call_log(
            db,
            classification.completion,
            user_id=state.user_id,
            conversation_id=state.conversation_id,
            agent_name="supervisor",
        )
        llm_log_id = llm_log.id
        state.llm_log_ids.append(llm_log.id)
    else:
        raw_intent = provider.classify_intent(state.input)
        ensure_agent_run_active(state)
    state.intent = normalize_intent(raw_intent, state.input)
    add_trace(
        state,
        node="supervisor",
        action="route_intent_with_llm_provider",
        input_data={"input": state.input},
        output_data={
            "intent": state.intent,
            "raw_intent": raw_intent,
            "provider": provider.provider_name,
            "llm_log_id": llm_log_id,
        },
    )
    return state
