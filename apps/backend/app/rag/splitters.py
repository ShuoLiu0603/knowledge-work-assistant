from __future__ import annotations

from dataclasses import dataclass, field

from app.rag.loaders import ParsedBlock, clean_text


@dataclass
class TextChunk:
    content: str
    token_count: int
    title_path: str | None = None
    page_number: int | None = None
    section_name: str | None = None
    metadata: dict = field(default_factory=dict)


def split_blocks(
    blocks: list[ParsedBlock],
    chunk_size: int = 800,
    chunk_overlap: int = 120,
) -> list[TextChunk]:
    chunks: list[TextChunk] = []
    buffer: list[str] = []
    buffer_meta: ParsedBlock | None = None
    current_size = 0

    def flush() -> None:
        nonlocal buffer, buffer_meta, current_size
        content = clean_text("\n\n".join(buffer))
        if content and buffer_meta:
            chunks.append(_chunk_from_content(content, buffer_meta))
        buffer = []
        buffer_meta = None
        current_size = 0

    for block in blocks:
        text = clean_text(block.text)
        if not text:
            continue

        if len(text) > chunk_size:
            flush()
            chunks.extend(_split_long_text(text, block, chunk_size, chunk_overlap))
            continue

        if buffer and buffer_meta and not same_chunk_scope(buffer_meta, block):
            flush()

        next_size = current_size + len(text) + (2 if buffer else 0)
        if buffer and next_size > chunk_size:
            flush()

        buffer.append(text)
        buffer_meta = buffer_meta or block
        current_size += len(text)

    flush()
    return chunks


def same_chunk_scope(left: ParsedBlock, right: ParsedBlock) -> bool:
    return (
        left.page_number == right.page_number
        and left.title_path == right.title_path
        and left.section_name == right.section_name
        and left.block_type == right.block_type
    )


def _split_long_text(
    text: str,
    block: ParsedBlock,
    chunk_size: int,
    chunk_overlap: int,
) -> list[TextChunk]:
    chunks: list[TextChunk] = []
    start = 0
    safe_overlap = min(chunk_overlap, max(0, chunk_size // 2))

    while start < len(text):
        end = min(len(text), start + chunk_size)
        content = clean_text(text[start:end])
        if content:
            chunks.append(_chunk_from_content(content, block))
        if end >= len(text):
            break
        start = max(end - safe_overlap, start + 1)

    return chunks


def _chunk_from_content(content: str, block: ParsedBlock) -> TextChunk:
    return TextChunk(
        content=content,
        token_count=estimate_token_count(content),
        title_path=block.title_path,
        page_number=block.page_number,
        section_name=block.section_name,
        metadata={**block.metadata, "block_type": block.block_type},
    )


def estimate_token_count(text: str) -> int:
    return max(1, len(text) // 4)
