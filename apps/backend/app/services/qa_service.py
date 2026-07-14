from dataclasses import dataclass

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.rag.answering import compact_snippet, select_answer_context_chunks
from app.rag.answering import format_answer_context
from app.rag.advanced_retrieval import retrieve_advanced_chunks
from app.rag.retrieval import RetrievedChunk
from app.llm.context_compression import compress_rag_evidence
from app.llm.token_counter import count_tokens
from app.core.config import get_settings
from app.db.models.retrieval_log import RetrievalLog
from app.schemas.qa import AskKnowledgeBaseRequest, AskKnowledgeBaseResponse, CitationRead
from app.services.audit_service import record_audit_event
from app.services.knowledge_base_service import ensure_kb_access, resolve_search_scope
from app.services.llm_log_service import create_llm_call_log
from app.services.retrieval_log_service import create_retrieval_log, to_retrieval_log_read


@dataclass(frozen=True)
class RagEvidence:
    question: str
    chunks: list[RetrievedChunk]
    citations: list[CitationRead]
    retrieval_log_id: str
    retrieval_log: object
    searched_knowledge_base_ids: list[str]


def ask_knowledge_base(
    db: Session,
    user_id: str,
    kb_id: str,
    payload: AskKnowledgeBaseRequest,
) -> AskKnowledgeBaseResponse:
    ensure_kb_access(db, user_id, kb_id, required_role="viewer")
    question = payload.question.strip()
    if (payload.search_scope or "single").strip().lower() != "single" or payload.department_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Direct knowledge-base ask only supports single knowledge-base scope",
        )
    from app.services.agent_service import run_agent

    run = run_agent(
        db,
        user_id,
        kb_id,
        question,
        top_k=payload.top_k,
        search_scope="single",
        department_id=None,
        memory_enabled=False,
    )
    retrieval_log_ids = list((run.state or {}).get("retrieval_log_ids") or [])
    if run.retrieval_log_id:
        retrieval_log_ids.append(run.retrieval_log_id)
    retrieval_logs = [
        log
        for log_id in dict.fromkeys(retrieval_log_ids)
        if (log := db.get(RetrievalLog, log_id)) is not None
    ]
    return AskKnowledgeBaseResponse(
        question=question,
        answer=run.answer,
        citations=[CitationRead(**citation) for citation in run.citations],
        retrieval_logs=[to_retrieval_log_read(log) for log in retrieval_logs],
    )


def retrieve_rag_evidence(
    db: Session,
    user_id: str,
    kb_id: str | None,
    question: str,
    top_k: int | None = None,
    conversation_id: str | None = None,
    message_id: str | None = None,
    search_scope: str = "single",
    department_id: str | None = None,
) -> RagEvidence:
    scope = resolve_search_scope(db, user_id, kb_id, scope_type=search_scope, department_id=department_id)
    retrieval = retrieve_advanced_chunks(
        db,
        None,
        scope.knowledge_base_ids,
        question,
        top_k=top_k,
        max_security_level=scope.max_security_level,
        scope_type=scope.scope_type,
    )
    settings = get_settings()
    raw_context = format_answer_context(retrieval.selected_chunks)
    compression = None
    if count_tokens(raw_context) > settings.rag_context_max_tokens:
        compression = compress_rag_evidence(
            question,
            retrieval.selected_chunks,
            settings.rag_context_max_tokens,
        )
        for completion in compression.completions:
            create_llm_call_log(
                db,
                completion,
                user_id=user_id,
                conversation_id=conversation_id,
                agent_name="rag_evidence_compression",
            )
    log = create_retrieval_log(
        db,
        user_id,
        scope.primary_knowledge_base_id,
        retrieval,
        conversation_id=conversation_id,
        message_id=message_id,
    )
    retrieval_log = to_retrieval_log_read(log)
    evidence_chunks = (
        compression.chunks
        if compression is not None and compression.chunks is not None
        else select_answer_context_chunks(retrieval.selected_chunks)
    )
    record_audit_event(
        db,
        actor_user_id=user_id,
        action="rag.retrieve",
        resource_type="knowledge_base",
        resource_id=scope.primary_knowledge_base_id,
        security_level=scope.max_security_level,
        metadata={
            "scope_type": scope.scope_type,
            "searched_knowledge_base_ids": scope.knowledge_base_ids,
            "conversation_id": conversation_id,
            "message_id": message_id,
            "retrieval_log_id": log.id,
            "question_preview": question[:160],
            "candidate_count": len(retrieval.candidates),
            "selected_count": len(retrieval.selected_chunks),
            "context_compressed": bool(compression and compression.chunks is not None),
            "context_compression_fallback": bool(compression and compression.fallback_used),
            "max_returned_security_level": max((chunk.security_level for chunk in retrieval.selected_chunks), default=None),
        },
    )
    return RagEvidence(
        question=question,
        chunks=evidence_chunks,
        citations=[to_citation(chunk) for chunk in evidence_chunks],
        retrieval_log_id=log.id,
        retrieval_log=retrieval_log,
        searched_knowledge_base_ids=scope.knowledge_base_ids,
    )


def to_citation(chunk: RetrievedChunk) -> CitationRead:
    return CitationRead(
        chunk_id=chunk.chunk_id,
        document_id=chunk.document_id,
        knowledge_base_id=chunk.knowledge_base_id,
        file_name=chunk.file_name,
        chunk_index=chunk.chunk_index,
        score=round(chunk.score, 6),
        content_preview=compact_snippet(chunk.content, max_chars=260),
        title_path=chunk.title_path,
        page_number=chunk.page_number,
        section_name=chunk.section_name,
        rrf_score=chunk.rrf_score,
        retrieval_routes=chunk.retrieval_routes or [],
        security_level=chunk.security_level,
    )
