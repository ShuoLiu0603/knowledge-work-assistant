from __future__ import annotations

from sqlalchemy.orm import Session

from app.agents.state import AgentGraphState, add_trace, ensure_agent_run_active
from app.llm.provider import get_llm_provider
from app.rag.answering import format_answer_context
from app.services.llm_log_service import create_llm_call_log
from app.services.qa_service import retrieve_rag_evidence


def draft_with_rag(db: Session, state: AgentGraphState) -> AgentGraphState:
    evidence = retrieve_rag_evidence(
        db,
        state.user_id,
        state.knowledge_base_id,
        state.input,
        top_k=state.top_k,
        conversation_id=state.conversation_id,
        message_id=state.message_id,
        search_scope=state.search_scope,
        department_id=state.search_department_id,
    )
    ensure_agent_run_active(state)
    grounding = format_answer_context(evidence.chunks)
    state.citations = evidence.citations
    state.retrieval_log_id = evidence.retrieval_log_id
    state.searched_knowledge_base_ids = evidence.searched_knowledge_base_ids
    completion = get_llm_provider().draft_with_metadata(
        state.input,
        grounding,
        style_context=state.memory_context,
    )
    ensure_agent_run_active(state)
    state.answer = completion.content.strip()
    if state.token_callback and state.answer:
        state.token_callback(state.answer)
    llm_log = create_llm_call_log(
        db,
        completion,
        user_id=state.user_id,
        conversation_id=state.conversation_id,
        agent_name="writing_agent",
    )
    state.llm_log_id = llm_log.id
    state.llm_log_ids.append(llm_log.id)
    add_trace(
        state,
        node="writing_agent",
        action="draft_with_rag_evidence",
        input_data={"writing_request": state.input, "evidence_chars": len(grounding), "chunk_count": len(evidence.chunks)},
        output_data={
            "draft_chars": len(state.answer),
            "citation_count": len(state.citations),
            "retrieval_log_id": state.retrieval_log_id,
            "llm_log_id": llm_log.id,
        },
    )
    return state
