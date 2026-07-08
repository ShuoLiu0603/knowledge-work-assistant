from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.memory.policy import (
    FULL_MEMORY_RECALL_LIMIT,
    STICKY_MEMORY_CATEGORIES,
    is_full_memory_recall_query,
    retrieval_similarity_threshold,
)

EmbedFn = Callable[[str], list[float]]


@dataclass(frozen=True)
class MemoryRecallCandidate:
    memory: Any
    route: str
    score: float | None
    selected: bool


@dataclass(frozen=True)
class MemoryRecallResult:
    selected: list[Any]
    candidates: list[MemoryRecallCandidate]
    recall_mode: str
    requested_limit: int
    recall_limit: int
    active_count: int
    threshold: float | None = None
    embedding_error: str | None = None


def rank_editor_context(memories: list[Any], query: str, embed: EmbedFn) -> list[Any]:
    if not memories:
        return []
    sticky = [memory for memory in memories if memory.category in STICKY_MEMORY_CATEGORIES]
    if not query.strip():
        return dedupe_memories([*sticky, *memories])

    try:
        query_embedding = embed(query)
    except Exception:
        return dedupe_memories([*sticky, *memories])
    scored = [
        (memory, cosine_similarity(query_embedding, memory.embedding or []))
        for memory in memories
        if memory.category not in STICKY_MEMORY_CATEGORIES
    ]
    scored.sort(key=lambda item: (item[1], item[0].last_touched_at), reverse=True)
    semantic = [memory for memory, score in scored if score > 0]
    return dedupe_memories([*sticky, *semantic, *memories])


def retrieve_relevant_memories(active: list[Any], query: str, limit: int, embed: EmbedFn) -> list[Any]:
    return retrieve_relevant_memories_with_metadata(active, query, limit, embed).selected


def retrieve_relevant_memories_with_metadata(
    active: list[Any],
    query: str,
    limit: int,
    embed: EmbedFn,
) -> MemoryRecallResult:
    recall_limit = max(limit, FULL_MEMORY_RECALL_LIMIT) if is_full_memory_recall_query(query) else limit
    if not active:
        return MemoryRecallResult(
            selected=[],
            candidates=[],
            recall_mode="empty",
            requested_limit=limit,
            recall_limit=recall_limit,
            active_count=0,
        )
    if is_full_memory_recall_query(query):
        selected = active[:recall_limit]
        return MemoryRecallResult(
            selected=selected,
            candidates=build_candidates(active, selected, route="full_recall"),
            recall_mode="full_recall",
            requested_limit=limit,
            recall_limit=recall_limit,
            active_count=len(active),
        )

    sticky = [memory for memory in active if memory.category in STICKY_MEMORY_CATEGORIES]
    non_sticky = [memory for memory in active if memory.category not in STICKY_MEMORY_CATEGORIES]
    if not non_sticky:
        selected = dedupe_memories(sticky)[:recall_limit]
        return MemoryRecallResult(
            selected=selected,
            candidates=build_candidates(sticky, selected, route="sticky"),
            recall_mode="sticky_only",
            requested_limit=limit,
            recall_limit=recall_limit,
            active_count=len(active),
        )

    try:
        query_embedding = embed(query)
    except Exception as exc:
        selected = dedupe_memories([*sticky, *active])[:recall_limit]
        return MemoryRecallResult(
            selected=selected,
            candidates=build_candidates(active, selected, route="fallback_no_embedding"),
            recall_mode="fallback_no_embedding",
            requested_limit=limit,
            recall_limit=recall_limit,
            active_count=len(active),
            embedding_error=str(exc),
        )
    scored = [
        (memory, cosine_similarity(query_embedding, memory.embedding or []))
        for memory in non_sticky
    ]
    scored.sort(key=lambda item: (item[1], item[0].last_touched_at), reverse=True)
    threshold = retrieval_similarity_threshold()
    semantic = [memory for memory, score in scored if score >= threshold]
    selected = dedupe_memories([*sticky, *semantic])[:recall_limit]
    selected_ids = {memory.id for memory in selected}
    candidates = [
        MemoryRecallCandidate(memory=memory, route="sticky", score=None, selected=memory.id in selected_ids)
        for memory in sticky
    ]
    candidates.extend(
        MemoryRecallCandidate(
            memory=memory,
            route="semantic" if score >= threshold else "below_threshold",
            score=score,
            selected=memory.id in selected_ids,
        )
        for memory, score in scored
    )
    return MemoryRecallResult(
        selected=selected,
        candidates=candidates,
        recall_mode="semantic",
        requested_limit=limit,
        recall_limit=recall_limit,
        active_count=len(active),
        threshold=threshold,
    )


def retrieve_relevant_memories_with_vector_hits(
    active: list[Any],
    query: str,
    limit: int,
    hits: list[Any],
    threshold: float | None = None,
) -> MemoryRecallResult:
    recall_limit = max(limit, FULL_MEMORY_RECALL_LIMIT) if is_full_memory_recall_query(query) else limit
    threshold = retrieval_similarity_threshold() if threshold is None else threshold
    active_by_id = {memory.id: memory for memory in active}
    sticky = [memory for memory in active if memory.category in STICKY_MEMORY_CATEGORIES]
    vector_memories = [
        active_by_id[hit.memory_id]
        for hit in hits
        if (
            hit.memory_id in active_by_id
            and active_by_id[hit.memory_id].category not in STICKY_MEMORY_CATEGORIES
            and hit.score >= threshold
        )
    ]
    selected = dedupe_memories([*sticky, *vector_memories])[:recall_limit]
    selected_ids = {memory.id for memory in selected}

    candidates = [
        MemoryRecallCandidate(memory=memory, route="sticky", score=None, selected=memory.id in selected_ids)
        for memory in sticky
    ]
    candidates.extend(
        MemoryRecallCandidate(
            memory=active_by_id[hit.memory_id],
            route="vector" if hit.score >= threshold else "below_threshold",
            score=hit.score,
            selected=hit.memory_id in selected_ids,
        )
        for hit in hits
        if hit.memory_id in active_by_id and active_by_id[hit.memory_id].category not in STICKY_MEMORY_CATEGORIES
    )
    return MemoryRecallResult(
        selected=selected,
        candidates=candidates,
        recall_mode="vector",
        requested_limit=limit,
        recall_limit=recall_limit,
        active_count=len(active),
        threshold=threshold,
    )


def build_candidates(memories: list[Any], selected: list[Any], route: str) -> list[MemoryRecallCandidate]:
    selected_ids = {memory.id for memory in selected}
    return [
        MemoryRecallCandidate(
            memory=memory,
            route=route,
            score=None,
            selected=memory.id in selected_ids,
        )
        for memory in memories
    ]


def recall_candidate_to_dict(candidate: MemoryRecallCandidate) -> dict:
    memory = candidate.memory
    return {
        "memory_id": memory.id,
        "category": memory.category,
        "kind": memory.kind,
        "status": memory.status,
        "route": candidate.route,
        "score": round(candidate.score, 6) if candidate.score is not None else None,
        "selected": candidate.selected,
        "last_touched_at": memory.last_touched_at.isoformat() if memory.last_touched_at else None,
    }


def find_similar_memory(memories: list[Any], embedding: list[float], threshold: float) -> Any | None:
    best_memory = None
    best_score = 0.0
    for memory in memories:
        score = cosine_similarity(embedding, memory.embedding or [])
        if score > best_score:
            best_memory = memory
            best_score = score
    if best_memory is not None and best_score >= threshold:
        return best_memory
    return None


def dedupe_memories(memories: list[Any]) -> list[Any]:
    seen = set()
    result = []
    for memory in memories:
        if memory.id in seen:
            continue
        seen.add(memory.id)
        result.append(memory)
    return result


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)
