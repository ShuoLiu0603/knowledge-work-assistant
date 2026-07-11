from __future__ import annotations

from app.core.config import get_settings
from app.llm.token_counter import count_tokens


def validate_question_token_limit(value: object) -> str:
    text = str(value or "").strip()
    limit = get_settings().question_max_tokens
    actual = count_tokens(text)
    if actual > limit:
        raise ValueError(f"Question uses {actual} tokens; the maximum is {limit}")
    return text
