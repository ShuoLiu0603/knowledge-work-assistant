from __future__ import annotations

import math
from collections.abc import Iterable


def compute_ir_metrics(
    qrels: dict[str, dict[str, float]],
    rankings: dict[str, list[str]],
    cutoffs: Iterable[int] = (1, 5, 10),
) -> dict[str, float | int]:
    normalized_cutoffs = sorted({int(cutoff) for cutoff in cutoffs if int(cutoff) > 0})
    if not normalized_cutoffs:
        raise ValueError("At least one positive cutoff is required")

    query_ids = [
        query_id
        for query_id, judgments in qrels.items()
        if any(float(relevance) > 0 for relevance in judgments.values())
    ]
    if not query_ids:
        raise ValueError("At least one query with a positive relevance judgment is required")

    totals = {
        f"{metric}@{cutoff}": 0.0
        for cutoff in normalized_cutoffs
        for metric in ("nDCG", "MAP", "Recall", "Precision", "MRR")
    }
    for query_id in query_ids:
        judgments = {
            document_id: float(relevance)
            for document_id, relevance in qrels[query_id].items()
            if float(relevance) > 0
        }
        ranking = dedupe_preserve_order(rankings.get(query_id, []))
        for cutoff in normalized_cutoffs:
            retrieved = ranking[:cutoff]
            relevant_hits = [document_id for document_id in retrieved if document_id in judgments]
            totals[f"Recall@{cutoff}"] += len(relevant_hits) / len(judgments)
            totals[f"Precision@{cutoff}"] += len(relevant_hits) / cutoff
            totals[f"MRR@{cutoff}"] += reciprocal_rank(retrieved, judgments)
            totals[f"MAP@{cutoff}"] += average_precision(retrieved, judgments, cutoff)
            totals[f"nDCG@{cutoff}"] += normalized_discounted_cumulative_gain(
                retrieved,
                judgments,
                cutoff,
            )

    query_count = len(query_ids)
    return {
        "query_count": query_count,
        **{key: round(value / query_count, 6) for key, value in totals.items()},
    }


def reciprocal_rank(ranking: list[str], judgments: dict[str, float]) -> float:
    for rank, document_id in enumerate(ranking, start=1):
        if document_id in judgments:
            return 1 / rank
    return 0.0


def average_precision(ranking: list[str], judgments: dict[str, float], cutoff: int) -> float:
    relevant_hits = 0
    precision_sum = 0.0
    for rank, document_id in enumerate(ranking[:cutoff], start=1):
        if document_id not in judgments:
            continue
        relevant_hits += 1
        precision_sum += relevant_hits / rank
    denominator = min(len(judgments), cutoff)
    return precision_sum / denominator if denominator else 0.0


def normalized_discounted_cumulative_gain(
    ranking: list[str],
    judgments: dict[str, float],
    cutoff: int,
) -> float:
    actual = discounted_cumulative_gain(
        [judgments.get(document_id, 0.0) for document_id in ranking[:cutoff]]
    )
    ideal = discounted_cumulative_gain(sorted(judgments.values(), reverse=True)[:cutoff])
    return actual / ideal if ideal else 0.0


def discounted_cumulative_gain(relevances: Iterable[float]) -> float:
    return sum(
        (2**float(relevance) - 1) / math.log2(rank + 1)
        for rank, relevance in enumerate(relevances, start=1)
    )


def dedupe_preserve_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
