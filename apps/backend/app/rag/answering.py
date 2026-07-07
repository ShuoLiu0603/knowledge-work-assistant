from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.core.config import get_settings
from app.llm.provider import LlmCompletion
from app.llm.provider import get_llm_provider
from app.rag.retrieval import RetrievedChunk


@dataclass(frozen=True)
class GeneratedAnswer:
    answer: str
    completion: LlmCompletion
    used_chunks: list[RetrievedChunk]


def select_answer_context_chunks(chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
    settings = get_settings()
    selected: list[RetrievedChunk] = []
    used_chars = 0
    for chunk in chunks:
        snippet = compact_snippet(chunk.content, max_chars=700)
        if used_chars + len(snippet) > settings.answer_context_max_chars:
            break
        used_chars += len(snippet)
        selected.append(
            RetrievedChunk(
                chunk_id=chunk.chunk_id,
                document_id=chunk.document_id,
                knowledge_base_id=chunk.knowledge_base_id,
                chunk_index=chunk.chunk_index,
                content=snippet,
                score=chunk.score,
                file_name=chunk.file_name,
                title_path=chunk.title_path,
                page_number=chunk.page_number,
                section_name=chunk.section_name,
                metadata=chunk.metadata,
                security_level=chunk.security_level,
                rrf_score=chunk.rrf_score,
                retrieval_routes=chunk.retrieval_routes,
            )
        )
    return selected


def generate_grounded_answer(
    question: str,
    chunks: list[RetrievedChunk],
    memory_context: str = "",
    on_token: Callable[[str], None] | None = None,
) -> GeneratedAnswer:
    used_chunks = select_answer_context_chunks(chunks)
    completion = get_llm_provider().answer_question_with_metadata(
        question,
        format_answer_context(used_chunks),
        memory_context=memory_context,
        on_token=on_token,
    )
    answer = completion.content.strip()
    if not answer:
        raise RuntimeError("LLM provider returned an empty answer.")
    return GeneratedAnswer(answer, completion, used_chunks)


def format_answer_context(chunks: list[RetrievedChunk]) -> str:
    rows: list[str] = []
    for index, chunk in enumerate(chunks, start=1):
        rows.append(f"[{index}] 来源：{chunk.file_name}，chunk #{chunk.chunk_index}\n{chunk.content}")
    return "\n\n".join(rows)


def compact_snippet(text: str, max_chars: int) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 1].rstrip() + "..."
