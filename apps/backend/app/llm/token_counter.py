from __future__ import annotations

from functools import lru_cache

import tiktoken

from app.core.config import get_settings


@lru_cache(maxsize=16)
def tokenizer_for_model(model_name: str):
    try:
        return tiktoken.encoding_for_model(model_name)
    except Exception:
        return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str, model_name: str | None = None) -> int:
    if not text:
        return 0
    name = (model_name or get_settings().llm_model).strip()
    return len(tokenizer_for_model(name).encode(text))
