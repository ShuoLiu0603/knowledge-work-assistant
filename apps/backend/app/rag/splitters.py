from __future__ import annotations

import os

import tiktoken
from langchain_core.documents import Document
from langchain_text_splitters.character import RecursiveCharacterTextSplitter

os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")

CHUNK_SEPARATORS = [
    "\n\n",
    "\n",
    "。",
    "！",
    "？",
    "；",
    ";",
    ".",
    "!",
    "?",
    "，",
    ",",
    "、",
    " ",
    "",
]

TOKEN_ENCODING = tiktoken.get_encoding("cl100k_base")


def split_documents(
    documents: list[Document],
    chunk_size: int = 800,
    chunk_overlap: int = 120,
) -> list[Document]:
    chunks = build_text_splitter(chunk_size, chunk_overlap).split_documents(documents)
    return [with_token_count(chunk) for chunk in chunks if chunk.page_content.strip()]


def build_text_splitter(chunk_size: int, chunk_overlap: int) -> RecursiveCharacterTextSplitter:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    safe_overlap = max(0, min(chunk_overlap, chunk_size - 1))
    return RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=safe_overlap,
        separators=CHUNK_SEPARATORS,
        keep_separator="end",
        length_function=len,
        strip_whitespace=True,
    )


def with_token_count(document: Document) -> Document:
    return Document(
        page_content=document.page_content.strip(),
        metadata={**document.metadata, "token_count": count_tokens(document.page_content)},
    )


def count_tokens(text: str) -> int:
    return max(1, len(TOKEN_ENCODING.encode(text)))
