from __future__ import annotations

from dataclasses import dataclass

from app.core.config import get_settings
from app.rag.vector_store import search_knowledge_base_chunks


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: str
    document_id: str
    knowledge_base_id: str
    chunk_index: int
    content: str
    score: float
    file_name: str
    title_path: str | None
    page_number: int | None
    section_name: str | None
    metadata: dict
    security_level: int = 1
    rrf_score: float | None = None
    retrieval_routes: list[str] | None = None


def retrieve_dense_chunks(
    owner_id: str,
    kb_id: str,
    question: str,
    top_k: int | None = None,
    max_security_level: int = 1,
) -> list[RetrievedChunk]:
    settings = get_settings()
    limit = top_k or settings.retrieval_top_k
    hits = search_knowledge_base_chunks(owner_id, kb_id, question, limit=limit, max_security_level=max_security_level)
    return [to_retrieved_chunk(hit.payload, hit.score) for hit in hits if hit.payload.get("content")]


def to_retrieved_chunk(payload: dict, score: float) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=str(payload.get("chunk_id", "")),
        document_id=str(payload.get("document_id", "")),
        knowledge_base_id=str(payload.get("knowledge_base_id", "")),
        chunk_index=int(payload.get("chunk_index", 0)),
        content=str(payload.get("content", "")),
        score=score,
        file_name=str(payload.get("file_name", "")),
        title_path=payload.get("title_path"),
        page_number=payload.get("page_number"),
        section_name=payload.get("section_name"),
        metadata=payload.get("metadata") or {},
        security_level=int(payload.get("security_level", 1)),
        retrieval_routes=["dense"],
    )
