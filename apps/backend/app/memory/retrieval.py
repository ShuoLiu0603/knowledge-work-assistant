from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.memory import policy

EmbedFn = Callable[[str], list[float]]
LEXICAL_WORD_PATTERN = re.compile(r"[a-z0-9_]+|[\u3400-\u9fff]+", re.IGNORECASE)
LEXICAL_STOP_WORDS = {
    "about",
    "and",
    "are",
    "for",
    "from",
    "have",
    "memory",
    "that",
    "the",
    "this",
    "user",
    "what",
    "with",
    "you",
    "your",
}


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
    sticky = [memory for memory in memories if policy.is_profile_memory(memory)]
    if not query.strip():
        return dedupe_memories([*sticky, *memories])

    try:
        query_embedding = embed(query)
        if not query_embedding:
            raise ValueError("embedding provider returned an empty vector")
    except Exception:
        lexical = [memory for memory, _score in rank_lexical_memories(memories, query)]
        return dedupe_memories([*sticky, *lexical])
    scored = [
        (memory, cosine_similarity(query_embedding, memory.embedding or []))
        for memory in memories
        if not policy.is_profile_memory(memory)
    ]
    scored.sort(key=lambda item: (item[1], item[0].last_touched_at), reverse=True)
    semantic = [memory for memory, _score in scored]
    return dedupe_memories([*sticky, *semantic, *memories])


def retrieve_relevant_memories(active: list[Any], query: str, limit: int, embed: EmbedFn) -> list[Any]:
    return retrieve_relevant_memories_with_metadata(active, query, limit, embed).selected


def retrieve_relevant_memories_with_metadata(
    active: list[Any],
    query: str,
    limit: int,
    embed: EmbedFn,
    active_count: int | None = None,
) -> MemoryRecallResult:
    recall_limit = max(limit, policy.FULL_MEMORY_RECALL_LIMIT) if policy.is_full_memory_recall_query(query) else limit
    reported_active_count = len(active) if active_count is None else active_count
    if not active:
        return MemoryRecallResult(
            selected=[],
            candidates=[],
            recall_mode="empty",
            requested_limit=limit,
            recall_limit=recall_limit,
            active_count=reported_active_count,
        )
    if policy.is_full_memory_recall_query(query):
        sticky = [memory for memory in active if policy.is_profile_memory(memory)]
        non_sticky = [memory for memory in active if not policy.is_profile_memory(memory)]
        selected = dedupe_memories([*sticky, *non_sticky])[:recall_limit]
        return MemoryRecallResult(
            selected=selected,
            candidates=build_candidates(active, selected, route="full_recall"),
            recall_mode="full_recall",
            requested_limit=limit,
            recall_limit=recall_limit,
            active_count=reported_active_count,
        )

    sticky = [memory for memory in active if policy.is_profile_memory(memory)]
    non_sticky = [memory for memory in active if not policy.is_profile_memory(memory)]
    if not non_sticky:
        selected = dedupe_memories(sticky)[:recall_limit]
        return MemoryRecallResult(
            selected=selected,
            candidates=build_candidates(sticky, selected, route="sticky"),
            recall_mode="sticky_only",
            requested_limit=limit,
            recall_limit=recall_limit,
            active_count=reported_active_count,
        )

    try:
        query_embedding = embed(query)
        if not query_embedding:
            raise ValueError("embedding provider returned an empty vector")
    except Exception as exc:
        lexical_scores = rank_lexical_memories(non_sticky, query)
        lexical = [memory for memory, _score in lexical_scores]
        selected = dedupe_memories([*sticky, *lexical])[:recall_limit]
        selected_ids = {memory.id for memory in selected}
        candidates = [
            MemoryRecallCandidate(memory=memory, route="sticky", score=None, selected=memory.id in selected_ids)
            for memory in sticky
        ]
        candidates.extend(
            MemoryRecallCandidate(
                memory=memory,
                route="lexical_ranked",
                score=score,
                selected=memory.id in selected_ids,
            )
            for memory, score in lexical_scores
        )
        return MemoryRecallResult(
            selected=selected,
            candidates=candidates,
            recall_mode="fallback_no_embedding",
            requested_limit=limit,
            recall_limit=recall_limit,
            active_count=reported_active_count,
            embedding_error=str(exc),
        )
    scored = [
        (memory, cosine_similarity(query_embedding, memory.embedding or []))
        for memory in non_sticky
    ]
    scored.sort(key=lambda item: (item[1], item[0].last_touched_at), reverse=True)
    semantic = [memory for memory, _score in scored]
    selected = dedupe_memories([*sticky, *semantic])[:recall_limit]
    selected_ids = {memory.id for memory in selected}
    candidates = [
        MemoryRecallCandidate(memory=memory, route="sticky", score=None, selected=memory.id in selected_ids)
        for memory in sticky
    ]
    candidates.extend(
        MemoryRecallCandidate(
            memory=memory,
            route="semantic_ranked",
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
        active_count=reported_active_count,
        threshold=None,
    )


def retrieve_relevant_memories_with_vector_hits(
    active: list[Any],
    query: str,
    limit: int,
    hits: list[Any],
    active_count: int | None = None,
) -> MemoryRecallResult:
    recall_limit = max(limit, policy.FULL_MEMORY_RECALL_LIMIT) if policy.is_full_memory_recall_query(query) else limit
    reported_active_count = len(active) if active_count is None else active_count
    active_by_id = {memory.id: memory for memory in active}
    sticky = [memory for memory in active if policy.is_profile_memory(memory)]
    vector_memories = [
        active_by_id[hit.memory_id]
        for hit in hits
        if (
            hit.memory_id in active_by_id
            and not policy.is_profile_memory(active_by_id[hit.memory_id])
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
            route="vector_ranked",
            score=hit.score,
            selected=hit.memory_id in selected_ids,
        )
        for hit in hits
        if hit.memory_id in active_by_id and not policy.is_profile_memory(active_by_id[hit.memory_id])
    )
    return MemoryRecallResult(
        selected=selected,
        candidates=candidates,
        recall_mode="vector",
        requested_limit=limit,
        recall_limit=recall_limit,
        active_count=reported_active_count,
        threshold=None,
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
        "canonical_key": memory.canonical_key,
        "memory_layer": memory.memory_layer,
        "profile_slot": memory.profile_slot,
        "scope_type": memory.scope_type,
        "scope_id": memory.scope_id,
        "pinned": memory.pinned,
        "status": memory.status,
        "route": candidate.route,
        "score": round(candidate.score, 6) if candidate.score is not None else None,
        "selected": candidate.selected,
        "last_touched_at": memory.last_touched_at.isoformat() if memory.last_touched_at else None,
    }


def rank_lexical_memories(memories: list[Any], query: str) -> list[tuple[Any, float]]:
    scored = [(memory, lexical_similarity(query, str(getattr(memory, "content", "") or ""))) for memory in memories]
    scored.sort(key=lambda item: (item[1], memory_recency(item[0])), reverse=True)
    return scored


def lexical_similarity(query: str, content: str) -> float:
    query_tokens = lexical_tokens(query)
    content_tokens = lexical_tokens(content)
    if not query_tokens or not content_tokens:
        return 0.0
    overlap = query_tokens & content_tokens
    if not overlap:
        return 0.0
    return len(overlap) / math.sqrt(len(query_tokens) * len(content_tokens))


def lexical_tokens(text: str) -> set[str]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    tokens: set[str] = set()
    for value in LEXICAL_WORD_PATTERN.findall(normalized):
        if value and all("\u3400" <= character <= "\u9fff" for character in value):
            if len(value) == 1:
                tokens.add(value)
            else:
                tokens.update(value[index : index + 2] for index in range(len(value) - 1))
            continue
        if len(value) >= 2 and value not in LEXICAL_STOP_WORDS:
            tokens.add(value)
    return tokens


def memory_recency(memory: Any) -> float:
    value = getattr(memory, "last_touched_at", None)
    if isinstance(value, datetime):
        return value.timestamp()
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0


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
