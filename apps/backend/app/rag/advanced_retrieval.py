from __future__ import annotations

import re
from dataclasses import dataclass, replace

from rank_bm25 import BM25Okapi
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models.document import Document, DocumentChunk
from app.db.models.knowledge_base import KnowledgeBase
from app.rag.answering import compact_snippet
from app.rag.retrieval import RetrievedChunk, retrieve_dense_chunks

TERM_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+")
ENGLISH_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "been",
        "being",
        "by",
        "for",
        "from",
        "has",
        "have",
        "in",
        "into",
        "is",
        "it",
        "its",
        "no",
        "not",
        "of",
        "on",
        "or",
        "that",
        "the",
        "their",
        "this",
        "to",
        "was",
        "were",
        "with",
        "without",
    }
)

_SETTINGS = get_settings()
DENSE_PREFILTER_MULTIPLIER = _SETTINGS.retrieval_dense_prefilter_multiplier
BM25_PREFILTER_TERMS = _SETTINGS.retrieval_bm25_prefilter_terms
MAX_MATCHED_TERMS = _SETTINGS.retrieval_max_matched_terms


@dataclass(frozen=True)
class RetrievalCandidate:
    chunk: RetrievedChunk
    route: str
    query: str
    rank: int
    score: float
    matched_terms: list[str]


@dataclass(frozen=True)
class FusedCandidate:
    chunk: RetrievedChunk
    routes: list[str]
    rrf_score: float
    best_score: float
    matched_terms: list[str]


@dataclass(frozen=True)
class AdvancedRetrievalResult:
    query: str
    scope_type: str
    searched_knowledge_base_ids: list[str]
    retrieval_routes: list[str]
    candidates: list[dict]
    selected_chunks: list[RetrievedChunk]
    selected_chunk_logs: list[dict]
    rrf_k: int
    reranker_enabled: bool
    compression_chars_saved: int


def retrieve_advanced_chunks(
    db: Session,
    owner_id: str | None,
    kb_ids: str | list[str],
    question: str,
    top_k: int | None = None,
    max_security_level: int = 1,
    scope_type: str = "single",
) -> AdvancedRetrievalResult:
    settings = get_settings()
    limit = top_k or settings.retrieval_top_k
    knowledge_base_ids = normalize_kb_ids(kb_ids)
    query = normalize_query(question)

    route_candidates: dict[str, list[RetrievalCandidate]] = {}
    route_candidates.update(
        retrieve_dense_route(
            db,
            owner_id,
            knowledge_base_ids,
            query,
            settings.retrieval_route_limit,
            max_security_level=max_security_level,
        )
    )
    route_candidates.update(
        retrieve_bm25_route(
            db,
            knowledge_base_ids,
            query,
            settings.retrieval_route_limit,
            max_security_level=max_security_level,
        )
    )

    fused = fuse_candidates(route_candidates, settings.rrf_k)
    selected_fused = fused[:limit]
    selected_chunks = [
        replace(
            candidate.chunk,
            rrf_score=round(candidate.rrf_score, 6),
            retrieval_routes=candidate.routes,
        )
        for candidate in selected_fused
    ]

    return AdvancedRetrievalResult(
        query=query,
        scope_type=scope_type,
        searched_knowledge_base_ids=knowledge_base_ids,
        retrieval_routes=list(route_candidates.keys()),
        candidates=candidates_to_log(route_candidates),
        selected_chunks=selected_chunks,
        selected_chunk_logs=selected_chunks_to_log(selected_fused, selected_chunks),
        rrf_k=settings.rrf_k,
        reranker_enabled=False,
        compression_chars_saved=0,
    )


def normalize_query(value: str) -> str:
    return " ".join(value.strip().split())


def retrieve_dense_route(
    db: Session,
    owner_id: str | None,
    kb_ids: str | list[str],
    query: str,
    route_limit: int,
    max_security_level: int,
) -> dict[str, list[RetrievalCandidate]]:
    knowledge_base_ids = normalize_kb_ids(kb_ids)
    if not knowledge_base_ids or not query:
        return {}

    owner_by_kb_id = load_knowledge_base_owners(db, knowledge_base_ids)
    if owner_id and len(knowledge_base_ids) == 1:
        owner_by_kb_id.setdefault(knowledge_base_ids[0], owner_id)

    raw_chunks: list[RetrievedChunk] = []
    prefilter_limit = max(route_limit * DENSE_PREFILTER_MULTIPLIER, route_limit)
    for kb_id in knowledge_base_ids:
        owner = owner_by_kb_id.get(kb_id)
        if not owner:
            continue
        raw_chunks.extend(
            retrieve_dense_chunks(
                db,
                owner,
                kb_id,
                query,
                top_k=prefilter_limit,
                max_security_level=max_security_level,
            )
        )
    chunks = sorted(
        hydrate_retrieved_chunks(db, raw_chunks, knowledge_base_ids, max_security_level),
        key=lambda chunk: chunk.score,
        reverse=True,
    )[:route_limit]
    if not chunks:
        return {}
    return {
        "dense": [
            RetrievalCandidate(
                chunk=replace(chunk, retrieval_routes=["dense"]),
                route="dense",
                query=query,
                rank=rank,
                score=chunk.score,
                matched_terms=matched_terms(query, chunk.content),
            )
            for rank, chunk in enumerate(chunks, start=1)
        ]
    }


def hydrate_retrieved_chunks(
    db: Session,
    chunks: list[RetrievedChunk],
    kb_ids: str | list[str],
    max_security_level: int,
) -> list[RetrievedChunk]:
    knowledge_base_ids = normalize_kb_ids(kb_ids)
    scores: dict[str, float] = {}
    for chunk in chunks:
        if chunk.chunk_id and chunk.chunk_id not in scores:
            scores[chunk.chunk_id] = chunk.score
    if not scores:
        return []

    rows = db.execute(
        select(DocumentChunk, Document)
        .join(Document, Document.id == DocumentChunk.document_id)
        .where(DocumentChunk.id.in_(scores))
        .where(DocumentChunk.knowledge_base_id.in_(knowledge_base_ids))
        .where(DocumentChunk.security_level <= max_security_level)
        .where(Document.status == "indexed")
    ).all()
    by_id = {
        chunk.id: chunk_to_retrieved(chunk, document, scores[chunk.id], ["dense"])
        for chunk, document in rows
    }
    return [by_id[chunk_id] for chunk_id in scores if chunk_id in by_id]


def retrieve_bm25_route(
    db: Session,
    kb_ids: str | list[str],
    query: str,
    route_limit: int,
    max_security_level: int,
) -> dict[str, list[RetrievalCandidate]]:
    knowledge_base_ids = normalize_kb_ids(kb_ids)
    if not knowledge_base_ids or not query:
        return {}

    candidates = retrieve_bm25_chunks(
        db,
        knowledge_base_ids,
        query,
        route="bm25",
        limit=route_limit,
        max_security_level=max_security_level,
    )
    return {"bm25": candidates} if candidates else {}


def retrieve_bm25_chunks(
    db: Session,
    kb_ids: str | list[str],
    query: str,
    route: str,
    limit: int,
    max_security_level: int,
) -> list[RetrievalCandidate]:
    knowledge_base_ids = normalize_kb_ids(kb_ids)
    query_terms = bm25_query_terms(query)
    if not knowledge_base_ids or not query_terms:
        return []

    rows = load_bm25_candidate_rows(db, knowledge_base_ids, query_terms, max_security_level)
    ranked_rows = rank_bm25_rows(query_terms, rows)

    return [
        RetrievalCandidate(
            chunk=chunk_to_retrieved(chunk, document, score, [route]),
            route=route,
            query=query,
            rank=rank,
            score=score,
            matched_terms=matched_terms(query, keyword_search_text(chunk, document)),
        )
        for rank, (chunk, document, score) in enumerate(ranked_rows[:limit], start=1)
    ]


def load_bm25_candidate_rows(
    db: Session,
    kb_ids: list[str],
    query_terms: list[str],
    max_security_level: int,
) -> list[tuple[DocumentChunk, Document]]:
    filters = [contains_term_in_search_fields(term) for term in query_terms[:BM25_PREFILTER_TERMS]]
    if not filters:
        return []
    return list(
        db.execute(
            select(DocumentChunk, Document)
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(DocumentChunk.knowledge_base_id.in_(kb_ids))
            .where(DocumentChunk.security_level <= max_security_level)
            .where(Document.status == "indexed")
            .where(or_(*filters))
        ).all()
    )


def rank_bm25_rows(
    query_terms: list[str],
    rows: list[tuple[DocumentChunk, Document]],
) -> list[tuple[DocumentChunk, Document, float]]:
    tokenized_rows = [
        (chunk, document, bm25_document_terms(keyword_search_text(chunk, document)))
        for chunk, document in rows
    ]
    tokenized_rows = [row for row in tokenized_rows if row[2]]
    if not tokenized_rows:
        return []

    bm25 = BM25Okapi([tokens for _, _, tokens in tokenized_rows])
    scores = bm25.get_scores(query_terms)
    ranked = [
        (chunk, document, float(score))
        for (chunk, document, tokens), score in zip(tokenized_rows, scores, strict=True)
        if has_term_overlap(query_terms, tokens)
    ]
    return sorted(ranked, key=lambda item: item[2], reverse=True)


def fuse_candidates(route_candidates: dict[str, list[RetrievalCandidate]], rrf_k: int) -> list[FusedCandidate]:
    grouped: dict[str, list[RetrievalCandidate]] = {}
    for candidates in route_candidates.values():
        for candidate in candidates:
            grouped.setdefault(candidate.chunk.chunk_id, []).append(candidate)

    fused: list[FusedCandidate] = []
    for candidates in grouped.values():
        rrf_score = sum(1 / (rrf_k + candidate.rank) for candidate in candidates)
        representative = max(candidates, key=lambda item: item.score)
        routes = dedupe_preserve_order(candidate.route for candidate in candidates)
        matches = dedupe_preserve_order(term for candidate in candidates for term in candidate.matched_terms)
        fused.append(
            FusedCandidate(
                chunk=representative.chunk,
                routes=routes,
                rrf_score=rrf_score,
                best_score=representative.score,
                matched_terms=matches,
            )
        )
    return sorted(fused, key=lambda item: item.rrf_score, reverse=True)


def candidates_to_log(route_candidates: dict[str, list[RetrievalCandidate]]) -> list[dict]:
    rows: list[dict] = []
    for route, candidates in route_candidates.items():
        for candidate in candidates:
            rows.append(
                {
                    "route": route,
                    "query": candidate.query,
                    "rank": candidate.rank,
                    "score": round(candidate.score, 6),
                    "chunk_id": candidate.chunk.chunk_id,
                    "document_id": candidate.chunk.document_id,
                    "knowledge_base_id": candidate.chunk.knowledge_base_id,
                    "file_name": candidate.chunk.file_name,
                    "chunk_index": candidate.chunk.chunk_index,
                    "security_level": candidate.chunk.security_level,
                    "matched_terms": candidate.matched_terms,
                    "content_preview": compact_snippet(candidate.chunk.content, max_chars=180),
                }
            )
    return rows


def selected_chunks_to_log(candidates: list[FusedCandidate], chunks: list[RetrievedChunk]) -> list[dict]:
    rows: list[dict] = []
    for candidate, chunk in zip(candidates, chunks, strict=False):
        rows.append(
            {
                "chunk_id": chunk.chunk_id,
                "document_id": chunk.document_id,
                "knowledge_base_id": chunk.knowledge_base_id,
                "file_name": chunk.file_name,
                "chunk_index": chunk.chunk_index,
                "security_level": chunk.security_level,
                "retrieval_routes": candidate.routes,
                "rrf_score": round(candidate.rrf_score, 6),
                "matched_terms": candidate.matched_terms,
                "content_preview": compact_snippet(chunk.content, max_chars=220),
            }
        )
    return rows


def chunk_to_retrieved(
    chunk: DocumentChunk,
    document: Document,
    score: float,
    routes: list[str],
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk.id,
        document_id=chunk.document_id,
        knowledge_base_id=chunk.knowledge_base_id,
        chunk_index=chunk.chunk_index,
        content=chunk.content,
        score=score,
        file_name=document.file_name,
        title_path=chunk.title_path,
        page_number=chunk.page_number,
        section_name=chunk.section_name,
        metadata=chunk.extra_metadata,
        security_level=chunk.security_level,
        retrieval_routes=routes,
    )


def keyword_search_text(chunk: DocumentChunk, document: Document) -> str:
    parts = [
        document.file_name,
        chunk.title_path,
        chunk.section_name,
        chunk.content,
    ]
    return "\n".join(str(part) for part in parts if part)


def searchable_terms(text: str) -> list[str]:
    return dedupe_preserve_order(text_terms(text))[:MAX_MATCHED_TERMS]


def bm25_query_terms(text: str) -> list[str]:
    return dedupe_preserve_order(bm25_document_terms(text))[:MAX_MATCHED_TERMS]


def bm25_document_terms(text: str) -> list[str]:
    return [term for term in text_terms(text) if is_cjk(term) or term not in ENGLISH_STOP_WORDS]


def text_terms(text: str) -> list[str]:
    terms: list[str] = []
    for token in TERM_RE.findall(text.lower()):
        if is_cjk(token):
            terms.extend(cjk_bigrams(token))
        elif len(token) >= 2:
            terms.append(token)
    return terms


def cjk_bigrams(value: str) -> list[str]:
    if len(value) <= 1:
        return [value]
    return [value[index : index + 2] for index in range(len(value) - 1)]


def is_cjk(value: str) -> bool:
    return all("\u4e00" <= char <= "\u9fff" for char in value)


def matched_terms(query: str, text: str) -> list[str]:
    content_terms = set(text_terms(text))
    return [term for term in bm25_query_terms(query) if term in content_terms]


def has_term_overlap(query_terms: list[str], content_terms: list[str]) -> bool:
    return bool(set(query_terms) & set(content_terms))


def contains_term_in_search_fields(term: str):
    return or_(
        contains_term(DocumentChunk.content, term),
        contains_term(DocumentChunk.title_path, term),
        contains_term(DocumentChunk.section_name, term),
        contains_term(Document.file_name, term),
    )


def contains_term(column, term: str):
    return column.ilike(f"%{escape_like(term)}%", escape="\\")


def escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def dedupe_preserve_order(values) -> list:
    seen = set()
    result = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def normalize_kb_ids(kb_ids: str | list[str]) -> list[str]:
    if isinstance(kb_ids, str):
        values = [kb_ids]
    else:
        values = kb_ids
    return [value for value in dedupe_preserve_order(values) if value]


def load_knowledge_base_owners(db: Session, kb_ids: list[str]) -> dict[str, str]:
    rows = db.execute(select(KnowledgeBase.id, KnowledgeBase.owner_id).where(KnowledgeBase.id.in_(kb_ids))).all()
    return {kb_id: owner_id for kb_id, owner_id in rows}
