from __future__ import annotations

from app.core.config import get_settings
from app.llm.token_counter import count_tokens
from app.rag.retrieval import RetrievedChunk


def select_answer_context_chunks(chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
    selected: list[RetrievedChunk] = []
    used_tokens = 0
    max_tokens = get_settings().rag_context_max_tokens
    for chunk in chunks:
        row = format_answer_context_row(chunk, citation_index=len(selected) + 1)
        row_tokens = count_tokens(row)
        if used_tokens + row_tokens > max_tokens:
            continue
        selected.append(chunk)
        used_tokens += row_tokens
    return selected


def format_answer_context(chunks: list[RetrievedChunk]) -> str:
    return "\n\n".join(
        format_answer_context_row(chunk, citation_index=index)
        for index, chunk in enumerate(chunks, start=1)
    )


def format_answer_context_row(chunk: RetrievedChunk, citation_index: int) -> str:
    return f"[{citation_index}] 来源：{chunk.file_name}，chunk #{chunk.chunk_index}\n{chunk.content}"


def compact_snippet(text: str, max_chars: int) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 1].rstrip() + "..."
