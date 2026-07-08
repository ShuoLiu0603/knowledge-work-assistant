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
from app.rag.query_rewrite import normalize_whitespace, rewrite_query
from app.rag.retrieval import RetrievedChunk, retrieve_dense_chunks

TERM_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？.!?])\s+|\n+")

MAX_ROUTE_QUERIES = 4
ORIGINAL_QUERY_WEIGHT = 1.2
REWRITE_QUERY_WEIGHT = 1.1
SUB_QUERY_WEIGHT = 1.0
DENSE_PREFILTER_MULTIPLIER = 4
BM25_PREFILTER_TERMS = 12


@dataclass(frozen=True)
class RetrievalQuery:
    text: str
    route_suffix: str
    weight: float


@dataclass(frozen=True)
class RetrievalCandidate:
    chunk: RetrievedChunk
    route: str
    query: str
    query_index: int
    rank: int
    score: float
    weight: float
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
    question: str
    scope_type: str
    searched_knowledge_base_ids: list[str]
    rewritten_query: str
    sub_questions: list[str]
    expanded_queries: list[str]
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
    rewritten_query, sub_queries, planned_queries = plan_retrieval_query_routes(question)
    retrieval_queries = [query.text for query in planned_queries]

    route_candidates: dict[str, list[RetrievalCandidate]] = {}
    route_candidates.update(
        retrieve_dense_routes(
            db,
            owner_id,
            knowledge_base_ids,
            planned_queries,
            settings.retrieval_route_limit,
            max_security_level=max_security_level,
        )
    )
    route_candidates.update(
        retrieve_bm25_routes(
            db,
            knowledge_base_ids,
            planned_queries,
            settings.retrieval_route_limit,
            max_security_level=max_security_level,
        )
    )

    fused = fuse_candidates(route_candidates, settings.rrf_k)
    selected_fused = fused[:limit]
    selected_chunks, saved = compress_selected_chunks(
        selected_fused,
        " ".join(retrieval_queries),
        settings.context_compression_chunk_chars,
    )

    return AdvancedRetrievalResult(
        question=question,
        scope_type=scope_type,
        searched_knowledge_base_ids=knowledge_base_ids,
        rewritten_query=rewritten_query,
        sub_questions=sub_queries,
        expanded_queries=retrieval_queries,
        retrieval_routes=list(route_candidates.keys()),
        candidates=candidates_to_log(route_candidates),
        selected_chunks=selected_chunks,
        selected_chunk_logs=selected_chunks_to_log(selected_fused, selected_chunks),
        rrf_k=settings.rrf_k,
        reranker_enabled=False,
        compression_chars_saved=saved,
    )


def plan_retrieval_queries(question: str) -> tuple[str, list[str], list[str]]:
    rewritten_query, sub_questions, planned_queries = plan_retrieval_query_routes(question)
    return rewritten_query, sub_questions, [query.text for query in planned_queries]


def plan_retrieval_query_routes(question: str) -> tuple[str, list[str], list[RetrievalQuery]]:
    original_query = normalize_whitespace(question)
    rewrite_plan = rewrite_query(original_query)
    rewritten_query = rewrite_plan.rewritten_query
    sub_questions = rewrite_plan.sub_questions

    planned_queries: list[RetrievalQuery] = []
    add_planned_query(planned_queries, original_query, "original", ORIGINAL_QUERY_WEIGHT)
    add_planned_query(planned_queries, rewritten_query, "rewrite", REWRITE_QUERY_WEIGHT)
    for index, sub_question in enumerate(sub_questions, start=1):
        add_planned_query(planned_queries, sub_question, f"subquery_{index}", SUB_QUERY_WEIGHT)

    return rewritten_query, sub_questions, planned_queries[:MAX_ROUTE_QUERIES]


def add_planned_query(planned_queries: list[RetrievalQuery], text: str, route_suffix: str, weight: float) -> None:
    normalized = normalize_whitespace(text)
    if not normalized:
        return
    if any(query.text == normalized for query in planned_queries):
        return
    planned_queries.append(RetrievalQuery(text=normalized, route_suffix=route_suffix, weight=weight))


def normalize_retrieval_queries(queries: list[str | RetrievalQuery]) -> list[RetrievalQuery]:
    normalized: list[RetrievalQuery] = []
    for index, query in enumerate(queries[:MAX_ROUTE_QUERIES]):
        if isinstance(query, RetrievalQuery):
            add_planned_query(normalized, query.text, query.route_suffix, query.weight)
            continue
        route_suffix = legacy_route_suffix(index)
        weight = ORIGINAL_QUERY_WEIGHT if index == 0 else SUB_QUERY_WEIGHT
        add_planned_query(normalized, query, route_suffix, weight)
    return normalized


def legacy_route_suffix(query_index: int) -> str:
    return "original" if query_index == 0 else f"subquery_{query_index}"


def retrieve_dense_routes(
    db: Session,
    owner_id: str | None,
    kb_ids: str | list[str],
    queries: list[str | RetrievalQuery],
    route_limit: int,
    max_security_level: int,
) -> dict[str, list[RetrievalCandidate]]:
    knowledge_base_ids = normalize_kb_ids(kb_ids)
    if not knowledge_base_ids:
        return {}

    owner_by_kb_id = load_knowledge_base_owners(db, knowledge_base_ids)
    if owner_id and len(knowledge_base_ids) == 1:
        owner_by_kb_id.setdefault(knowledge_base_ids[0], owner_id)

    routes: dict[str, list[RetrievalCandidate]] = {}
    for index, query_plan in enumerate(normalize_retrieval_queries(queries)):
        raw_chunks: list[RetrievedChunk] = []
        prefilter_limit = max(route_limit * DENSE_PREFILTER_MULTIPLIER, route_limit)
        for kb_id in knowledge_base_ids:
            owner = owner_by_kb_id.get(kb_id)
            if not owner:
                continue
            raw_chunks.extend(
                retrieve_dense_chunks(
                    owner,
                    kb_id,
                    query_plan.text,
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
            continue
        route = route_name("dense", query_plan.route_suffix)
        routes[route] = [
            RetrievalCandidate(
                chunk=replace(chunk, retrieval_routes=[route]),
                route=route,
                query=query_plan.text,
                query_index=index,
                rank=rank,
                score=chunk.score,
                weight=query_plan.weight,
                matched_terms=matched_terms(query_plan.text, chunk.content),
            )
            for rank, chunk in enumerate(chunks, start=1)
        ]
    return routes


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


def retrieve_bm25_routes(
    db: Session,
    kb_ids: str | list[str],
    queries: list[str | RetrievalQuery],
    route_limit: int,
    max_security_level: int,
) -> dict[str, list[RetrievalCandidate]]:
    knowledge_base_ids = normalize_kb_ids(kb_ids)
    if not knowledge_base_ids:
        return {}

    routes: dict[str, list[RetrievalCandidate]] = {}
    for index, query_plan in enumerate(normalize_retrieval_queries(queries)):
        route = route_name("bm25", query_plan.route_suffix)
        candidates = retrieve_bm25_chunks(
            db,
            knowledge_base_ids,
            query_plan.text,
            route=route,
            query_index=index,
            weight=query_plan.weight,
            limit=route_limit,
            max_security_level=max_security_level,
        )
        if candidates:
            routes[route] = candidates
    return routes


def retrieve_bm25_chunks(
    db: Session,
    kb_ids: str | list[str],
    query: str,
    route: str,
    query_index: int,
    weight: float,
    limit: int,
    max_security_level: int,
) -> list[RetrievalCandidate]:
    knowledge_base_ids = normalize_kb_ids(kb_ids)
    query_terms = searchable_terms(query)
    if not knowledge_base_ids or not query_terms:
        return []

    rows = load_bm25_candidate_rows(db, knowledge_base_ids, query_terms, max_security_level)
    ranked_rows = rank_bm25_rows(query_terms, rows)

    return [
        RetrievalCandidate(
            chunk=chunk_to_retrieved(chunk, document, score, [route]),
            route=route,
            query=query,
            query_index=query_index,
            rank=rank,
            score=score,
            weight=weight,
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
        (chunk, document, searchable_terms(keyword_search_text(chunk, document)))
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
        rrf_score = sum(candidate.weight / (rrf_k + candidate.rank) for candidate in candidates)
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


def compress_selected_chunks(
    candidates: list[FusedCandidate],
    query: str,
    max_chars: int,
) -> tuple[list[RetrievedChunk], int]:
    selected: list[RetrievedChunk] = []
    saved = 0
    terms = searchable_terms(query)
    for candidate in candidates:
        original = candidate.chunk.content
        compressed = compress_context(original, terms, max_chars)
        saved += max(0, len(original) - len(compressed))
        selected.append(
            replace(
                candidate.chunk,
                content=compressed,
                rrf_score=round(candidate.rrf_score, 6),
                retrieval_routes=candidate.routes,
            )
        )
    return selected, saved


def compress_context(content: str, terms: list[str], max_chars: int) -> str:
    normalized = " ".join(content.split())
    if len(normalized) <= max_chars:
        return normalized

    sentences = [part.strip() for part in SENTENCE_SPLIT_RE.split(content) if part.strip()]
    matched = [
        sentence
        for sentence in sentences
        if any(term.lower() in sentence.lower() for term in terms)
    ]
    compressed = " ".join(matched or sentences[:2])
    if len(compressed) > max_chars:
        return compact_snippet(compressed, max_chars=max_chars)
    return compressed


def candidates_to_log(route_candidates: dict[str, list[RetrievalCandidate]]) -> list[dict]:
    rows: list[dict] = []
    for route, candidates in route_candidates.items():
        for candidate in candidates:
            rows.append(
                {
                    "route": route,
                    "query": candidate.query,
                    "query_index": candidate.query_index,
                    "rank": candidate.rank,
                    "score": round(candidate.score, 6),
                    "route_weight": candidate.weight,
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
    return dedupe_preserve_order(text_terms(text))[:32]


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
    return [term for term in searchable_terms(query) if term in content_terms]


def has_term_overlap(query_terms: list[str], content_terms: list[str]) -> bool:
    return bool(set(query_terms) & set(content_terms))


def route_name(prefix: str, route_suffix: str) -> str:
    return f"{prefix}_{route_suffix}"


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
