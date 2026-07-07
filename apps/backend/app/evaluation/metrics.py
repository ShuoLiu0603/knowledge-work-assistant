from __future__ import annotations

from typing import Any


def compute_metrics(cases: list[dict[str, Any]], results: list[dict[str, Any]], top_k: int) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for case, result in zip(cases, results, strict=False):
        citations = result.get("citations") or []
        expected_sources = [str(item).lower() for item in case.get("expected_sources", [])]
        expected_keywords = [str(item).lower() for item in case.get("expected_keywords", [])]
        rank = first_matching_rank(citations, expected_sources, top_k)
        answer = str(result.get("answer", "")).lower()
        keyword_hit = bool(expected_keywords) and any(keyword in answer for keyword in expected_keywords)
        row = {
            "id": case.get("id") or case.get("question"),
            "question": case.get("question"),
            "first_hit_rank": rank,
            "recall_at_k": 1 if rank is not None else 0,
            "reciprocal_rank": round(1 / rank, 6) if rank else 0,
            "citation_hit": 1 if rank is not None else 0,
            "answer_keyword_hit": 1 if keyword_hit else 0,
            "citation_count": len(citations),
        }
        rows.append(row)

    total = len(rows)
    return {
        "total": total,
        "top_k": top_k,
        "recall_at_k": round(mean(row["recall_at_k"] for row in rows), 6),
        "mrr": round(mean(row["reciprocal_rank"] for row in rows), 6),
        "citation_hit_rate": round(mean(row["citation_hit"] for row in rows), 6),
        "answer_keyword_hit_rate": round(mean(row["answer_keyword_hit"] for row in rows), 6),
        "rows": rows,
    }


def first_matching_rank(citations: list[dict[str, Any]], expected_sources: list[str], top_k: int) -> int | None:
    if not expected_sources:
        return None
    for rank, citation in enumerate(citations[:top_k], start=1):
        haystack = " ".join(
            str(citation.get(key, ""))
            for key in ("chunk_id", "document_id", "file_name", "content_preview")
        ).lower()
        if any(source in haystack for source in expected_sources):
            return rank
    return None


def mean(values) -> float:
    numbers = list(values)
    if not numbers:
        return 0.0
    return sum(numbers) / len(numbers)
