from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, replace

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models.document import Document, DocumentChunk
from app.db.models.knowledge_base import KnowledgeBase
from app.rag.answering import compact_snippet
from app.rag.retrieval import RetrievedChunk, retrieve_dense_chunks

CONNECTOR_RE = re.compile(r"\s+(?:and|or|vs|versus|with)\s+|[，。；;？?、]|以及|并且|同时|分别|对比|比较|和")
POLITE_PREFIX_RE = re.compile(r"^(请问|请帮我|帮我|能否|可以|请|please|could you|can you)\s*", re.IGNORECASE)
SUBQUESTION_PREFIX_RE = re.compile(r"^(并说明|说明|并列出|列出|介绍)\s*")
TERM_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+")

MAX_SUB_QUERIES = 3
MAX_ROUTE_QUERIES = 4
ORIGINAL_QUERY_WEIGHT = 1.2
SUB_QUERY_WEIGHT = 1.0
BM25_K1 = 1.5
BM25_B = 0.75
DENSE_PREFILTER_MULTIPLIER = 4


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
class Bm25Stats:
    total_chunks: int
    avgdl: float
    document_frequency: dict[str, int]


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
    normalized_query, sub_queries, retrieval_queries = plan_retrieval_queries(question)

    route_candidates: dict[str, list[RetrievalCandidate]] = {}
    route_candidates.update(
        retrieve_dense_routes(
            db,
            owner_id,
            knowledge_base_ids,
            retrieval_queries,
            settings.retrieval_route_limit,
            max_security_level=max_security_level,
        )
    )
    route_candidates.update(
        retrieve_bm25_routes(
            db,
            knowledge_base_ids,
            retrieval_queries,
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
        rewritten_query=normalized_query,
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
    original_query = " ".join(question.strip().split())
    normalized_query = normalize_query(original_query)
    sub_queries = decompose_question(normalized_query)
    retrieval_queries = dedupe_preserve_order([original_query, *sub_queries])[:MAX_ROUTE_QUERIES]
    return normalized_query, sub_queries, retrieval_queries


def normalize_query(question: str) -> str:
    normalized = " ".join(question.strip().split())
    normalized = POLITE_PREFIX_RE.sub("", normalized).strip()
    normalized = normalized.rstrip("?.。？")
    return normalized or question.strip()


def decompose_question(query: str) -> list[str]:
    parts = [clean_sub_query(part) for part in CONNECTOR_RE.split(query) if part.strip(" ,，。？?;；")]
    if len(parts) <= 1:
        return []
    return dedupe_preserve_order(part for part in parts if len(part) >= 2 and part != query)[:MAX_SUB_QUERIES]


def clean_sub_query(part: str) -> str:
    cleaned = part.strip(" ,，。？?;；")
    return SUBQUESTION_PREFIX_RE.sub("", cleaned).strip()


def retrieve_dense_routes(
    db: Session,
    owner_id: str | None,
    kb_ids: str | list[str],
    queries: list[str],
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
    for index, query in enumerate(queries[:MAX_ROUTE_QUERIES]):
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
            continue
        route = route_name("dense", index)
        weight = query_weight(index)
        routes[route] = [
            RetrievalCandidate(
                chunk=replace(chunk, retrieval_routes=[route]),
                route=route,
                query=query,
                query_index=index,
                rank=rank,
                score=chunk.score,
                weight=weight,
                matched_terms=matched_terms(query, chunk.content),
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
    queries: list[str],
    route_limit: int,
    max_security_level: int,
) -> dict[str, list[RetrievalCandidate]]:
    knowledge_base_ids = normalize_kb_ids(kb_ids)
    if not knowledge_base_ids:
        return {}

    all_terms = dedupe_preserve_order(term for query in queries for term in searchable_terms(query))
    if not all_terms:
        return {}

    stats = build_bm25_stats(db, knowledge_base_ids, all_terms, max_security_level)
    routes: dict[str, list[RetrievalCandidate]] = {}
    for index, query in enumerate(queries[:MAX_ROUTE_QUERIES]):
        route = route_name("bm25", index)
        candidates = retrieve_bm25_chunks(
            db,
            knowledge_base_ids,
            query,
            route=route,
            query_index=index,
            weight=query_weight(index),
            limit=route_limit,
            max_security_level=max_security_level,
            stats=stats,
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
    stats: Bm25Stats | None = None,
) -> list[RetrievalCandidate]:
    knowledge_base_ids = normalize_kb_ids(kb_ids)
    if not knowledge_base_ids:
        return []

    terms = searchable_terms(query)
    if not terms:
        return []

    stats = stats or build_bm25_stats(db, knowledge_base_ids, terms, max_security_level)
    filters = [contains_term(DocumentChunk.content, term) for term in terms[:12]]
    rows = db.execute(
        select(DocumentChunk, Document)
        .join(Document, Document.id == DocumentChunk.document_id)
        .where(DocumentChunk.knowledge_base_id.in_(knowledge_base_ids))
        .where(DocumentChunk.security_level <= max_security_level)
        .where(Document.status == "indexed")
        .where(or_(*filters))
    ).all()

    candidates: list[tuple[RetrievedChunk, float, list[str]]] = []
    for chunk, document in rows:
        matches = matched_terms(query, chunk.content)
        score = bm25_score(terms, chunk.content, chunk.token_count, stats)
        if score > 0:
            candidates.append((chunk_to_retrieved(chunk, document, score, [route]), score, matches))

    candidates.sort(key=lambda item: item[1], reverse=True)
    return [
        RetrievalCandidate(
            chunk=chunk,
            route=route,
            query=query,
            query_index=query_index,
            rank=rank,
            score=score,
            weight=weight,
            matched_terms=matches,
        )
        for rank, (chunk, score, matches) in enumerate(candidates[:limit], start=1)
    ]


def build_bm25_stats(
    db: Session,
    kb_ids: str | list[str],
    terms: list[str],
    max_security_level: int,
) -> Bm25Stats:
    knowledge_base_ids = normalize_kb_ids(kb_ids)
    if not knowledge_base_ids:
        return Bm25Stats(total_chunks=0, avgdl=1.0, document_frequency={term: 0 for term in terms})

    base_filters = (
        DocumentChunk.knowledge_base_id.in_(knowledge_base_ids),
        DocumentChunk.security_level <= max_security_level,
        Document.status == "indexed",
    )
    total_chunks = int(
        db.scalar(
            select(func.count())
            .select_from(DocumentChunk)
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(*base_filters)
        )
        or 0
    )
    avgdl = float(
        db.scalar(
            select(func.avg(DocumentChunk.token_count))
            .select_from(DocumentChunk)
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(*base_filters)
        )
        or 1
    )
    document_frequency = {
        term: int(
            db.scalar(
                select(func.count())
                .select_from(DocumentChunk)
                .join(Document, Document.id == DocumentChunk.document_id)
                .where(*base_filters)
                .where(contains_term(DocumentChunk.content, term))
            )
            or 0
        )
        for term in terms
    }
    return Bm25Stats(total_chunks=total_chunks, avgdl=max(avgdl, 1.0), document_frequency=document_frequency)


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

    sentences = [part.strip() for part in re.split(r"(?<=[。！？.!?])\s+|\n+", content) if part.strip()]
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


def bm25_score(terms: list[str], content: str, token_count: int, stats: Bm25Stats) -> float:
    if stats.total_chunks <= 0:
        return 0.0

    counts = Counter(text_terms(content))
    dl = max(1, token_count or sum(counts.values()))
    score = 0.0
    for term in terms:
        tf = counts.get(term, 0)
        df = stats.document_frequency.get(term, 0)
        if tf <= 0 or df <= 0:
            continue
        idf = math.log(1 + ((stats.total_chunks - df + 0.5) / (df + 0.5)))
        denominator = tf + BM25_K1 * (1 - BM25_B + BM25_B * dl / stats.avgdl)
        score += idf * ((tf * (BM25_K1 + 1)) / denominator)
    return float(score)


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


def route_name(prefix: str, query_index: int) -> str:
    return f"{prefix}_original" if query_index == 0 else f"{prefix}_subquery_{query_index}"


def query_weight(query_index: int) -> float:
    return ORIGINAL_QUERY_WEIGHT if query_index == 0 else SUB_QUERY_WEIGHT


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
