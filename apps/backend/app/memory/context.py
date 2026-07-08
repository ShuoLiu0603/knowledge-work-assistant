from __future__ import annotations

from dataclasses import dataclass
from typing import Any

EMPTY_VALUE = "无"
MIN_MEMORY_CONTEXT_CHARS = 300
MIN_MEMORY_CONTEXT_TOKENS = 100
MEMORY_CATEGORY_PRIORITY = {
    "language": 100,
    "format": 95,
    "response_detail": 90,
    "role": 70,
    "profile": 65,
    "project": 60,
    "instruction": 55,
}
MEMORY_KIND_PRIORITY = {
    "instruction": 30,
    "profile": 20,
    "project": 15,
    "preference": 10,
}
MEMORY_IMPORTANCE_PRIORITY = {
    "high": 30,
    "medium": 15,
    "low": 0,
}


def format_memory_context(
    long_memories: list[dict],
    short_memory: list[dict],
    conversation_summary: str | None,
    max_long_memories: int = 8,
    max_chars: int = 3000,
    max_tokens: int | None = None,
    model_name: str = "",
) -> str:
    budget = TextBudget.from_limits(max_chars=max_chars, max_tokens=max_tokens, model_name=model_name)
    headers = "长期记忆:\n\n\n会话摘要:\n\n\n最近对话:\n"
    available = max(0, budget.limit - budget.count(headers))
    long_budget = max(budget.minimum_section_limit, int(available * 0.45))
    summary_budget = max(budget.minimum_section_limit, int(available * 0.25))
    recent_budget = max(budget.minimum_section_limit, available - long_budget - summary_budget)

    memories = budgeted_memory_lines(
        prioritize_memories(long_memories)[:max_long_memories],
        limit=long_budget,
        budget=budget,
    )
    summary = budget.truncate(conversation_summary or EMPTY_VALUE, summary_budget)
    recent = budgeted_lines(
        [
            f"{item.get('role')}: {item.get('content')}"
            for item in short_memory[-8:]
            if item.get("content")
        ],
        limit=recent_budget,
        budget=budget,
    )

    context = f"长期记忆:\n{memories}\n\n会话摘要:\n{summary}\n\n最近对话:\n{recent}"
    context = budget.truncate(context, budget.limit)
    return truncate_chars(context, max_chars) if len(context) > max_chars else context


def budgeted_lines(values: list[object], limit: int, budget: "TextBudget") -> str:
    normalized = [normalize_text(str(value)) for value in values if normalize_text(str(value or ""))]
    if not normalized:
        return f"- {EMPTY_VALUE}"

    lines: list[str] = []
    used = 0
    for value in normalized:
        line = f"- {value}"
        line_cost = budget.count(line) + (budget.count("\n") if lines else 0)
        if used + line_cost <= limit:
            lines.append(line)
            used += line_cost
            continue
        if not lines:
            lines.append(budget.truncate(line, limit))
        break
    return "\n".join(lines) or f"- {EMPTY_VALUE}"


def budgeted_memory_lines(memories: list[dict], limit: int, budget: "TextBudget") -> str:
    return budgeted_lines([memory.get("content") for memory in memories], limit=limit, budget=budget)


def prioritize_memories(memories: list[dict]) -> list[dict]:
    indexed = list(enumerate(memories))
    indexed.sort(key=lambda item: (-memory_priority(item[1]), item[0]))
    return [memory for _, memory in indexed]


def memory_priority(memory: dict) -> float:
    category = normalize_key(memory.get("category"))
    kind = normalize_key(memory.get("kind"))
    metadata = memory.get("metadata") if isinstance(memory.get("metadata"), dict) else {}
    importance = normalize_key(metadata.get("importance") or metadata.get("importance_level"))
    confidence = safe_float(metadata.get("confidence"))
    return (
        MEMORY_CATEGORY_PRIORITY.get(category, 0)
        + MEMORY_KIND_PRIORITY.get(kind, 0)
        + MEMORY_IMPORTANCE_PRIORITY.get(importance, 0)
        + confidence * 10
    )


def normalize_key(value: object) -> str:
    return str(value or "").strip().lower()


def safe_float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def normalize_text(text: str) -> str:
    return " ".join(text.strip().split())


@dataclass(frozen=True)
class TextBudget:
    limit: int
    unit: str
    encoding: Any = None

    @property
    def minimum_section_limit(self) -> int:
        return 24 if self.unit == "tokens" else 80

    @classmethod
    def from_limits(cls, max_chars: int, max_tokens: int | None, model_name: str = "") -> "TextBudget":
        if max_tokens is not None:
            return cls(
                limit=max(MIN_MEMORY_CONTEXT_TOKENS, max_tokens),
                unit="tokens",
                encoding=load_tokenizer(model_name),
            )
        return cls(limit=max(MIN_MEMORY_CONTEXT_CHARS, max_chars), unit="chars")

    def count(self, text: str) -> int:
        if self.unit != "tokens":
            return len(text)
        if self.encoding is not None:
            return len(self.encoding.encode(text))
        return estimate_tokens(text)

    def truncate(self, text: str, limit: int) -> str:
        if limit <= 0:
            return ""
        if self.count(text) <= limit:
            return text
        if self.unit != "tokens":
            return truncate_chars(text, limit)
        if self.encoding is None:
            return truncate_chars(text, max(1, limit * 4))
        token_ids = self.encoding.encode(text)
        if limit <= 3:
            return self.encoding.decode(token_ids[:limit])
        keep = min(len(token_ids), limit - 1)
        while keep > 0:
            candidate = self.encoding.decode(token_ids[:keep]).rstrip() + "..."
            if self.count(candidate) <= limit:
                return candidate
            keep -= 1
        return "..."


def load_tokenizer(model_name: str):
    try:
        import tiktoken
    except Exception:
        return None
    try:
        return tiktoken.encoding_for_model(model_name)
    except Exception:
        try:
            return tiktoken.get_encoding("cl100k_base")
        except Exception:
            return None


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def truncate_chars(text: str, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    if max_chars <= 3:
        return text[:max_chars]
    return text[: max_chars - 3].rstrip() + "..."
