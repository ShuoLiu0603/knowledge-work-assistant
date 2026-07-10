from __future__ import annotations

from sqlalchemy.orm import Session

from app.agents.state import AgentGraphState, add_trace
from app.llm.provider import get_llm_provider
from app.services.llm_log_service import create_llm_call_log
from app.services.qa_service import build_rag_answer


def answer_with_rag(db: Session, state: AgentGraphState) -> AgentGraphState:
    if state.intent == "chat":
        return answer_from_chat(db, state)
    if state.intent == "memory":
        return answer_from_memory(db, state)

    rag_answer = build_rag_answer(
        db,
        state.user_id,
        state.knowledge_base_id,
        state.input,
        top_k=state.top_k,
        conversation_id=state.conversation_id,
        message_id=state.message_id,
        memory_context=state.memory_context,
        agent_name="rag_agent",
        on_token=state.token_callback,
        search_scope=state.search_scope,
        department_id=state.search_department_id,
    )
    state.answer = rag_answer.answer
    state.citations = rag_answer.citations
    state.retrieval_log_id = rag_answer.retrieval_log_id
    state.llm_log_id = rag_answer.llm_log_id
    if rag_answer.llm_log_id:
        state.llm_log_ids.append(rag_answer.llm_log_id)
    add_trace(
        state,
        node="rag_agent",
        action="answer_with_existing_rag_service",
        input_data={"question": state.input, "top_k": state.top_k, "search_scope": state.search_scope},
        output_data={
            "retrieval_log_id": state.retrieval_log_id,
            "llm_log_id": state.llm_log_id,
            "citation_count": len(state.citations),
        },
    )
    return state


def answer_from_chat(db: Session, state: AgentGraphState) -> AgentGraphState:
    completion = get_llm_provider().answer_chat_with_metadata(
        state.input,
        state.memory_context,
        on_token=state.token_callback,
    )
    llm_log = create_llm_call_log(
        db,
        completion,
        user_id=state.user_id,
        conversation_id=state.conversation_id,
        agent_name="chat_agent",
    )
    state.answer = completion.content.strip()
    state.citations = []
    state.llm_log_id = llm_log.id
    state.llm_log_ids.append(llm_log.id)
    add_trace(
        state,
        node="chat_agent",
        action="answer_without_retrieval",
        input_data={"question": state.input},
        output_data={"llm_log_id": state.llm_log_id, "answer_chars": len(state.answer)},
    )
    return state


def answer_from_memory(db: Session, state: AgentGraphState) -> AgentGraphState:
    completion = get_llm_provider().answer_memory_question_with_metadata(
        state.input,
        state.memory_context,
        on_token=state.token_callback,
    )
    llm_log = create_llm_call_log(
        db,
        completion,
        user_id=state.user_id,
        conversation_id=state.conversation_id,
        agent_name="memory_agent",
    )
    state.answer = completion.content.strip()
    state.citations = []
    state.llm_log_id = llm_log.id
    state.llm_log_ids.append(llm_log.id)
    add_trace(
        state,
        node="memory_agent",
        action="answer_from_memory_context",
        input_data={"question": state.input},
        output_data={"llm_log_id": state.llm_log_id, "answer_chars": len(state.answer)},
    )
    return state
