from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.config import get_settings
from app.memory import policy

EMPTY_VALUE = "None"
MEMORY_CATEGORY_PRIORITY = {
    "role": 70,
    "profile": 65,
    "project": 60,
    "instruction": 55,
    "general": 0,
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
    max_long_memories: int | None = None,
    max_chars: int | None = None,
    max_tokens: int | None = None,
    model_name: str = "",
    profile_memories: list[dict] | None = None,
) -> str:
    settings = get_settings()
    if max_long_memories is None:
        max_long_memories = settings.memory_context_max_long_memories
    if max_chars is None:
        max_chars = settings.memory_context_max_chars
    budget = TextBudget.from_limits(max_chars=max_chars, max_tokens=max_tokens, model_name=model_name)
    if budget.limit <= 0 or max_chars <= 0:
        return ""
    if profile_memories is None:
        profile_memories, long_memories = split_profile_memories(long_memories)

    recent_values = [
        f"{item.get('role')}: {item.get('content')}"
        for item in short_memory
        if item.get("content")
    ]
    include_recent = bool(recent_values)
    headers = format_memory_sections("", "", "", "" if include_recent else None)
    available = max(0, budget.limit - budget.count(headers))
    has_content = {
        "profile": bool(profile_memories),
        "long": bool(long_memories),
        "summary": bool(conversation_summary),
    }
    if include_recent:
        has_content["recent"] = bool(recent_values)
    section_budgets = allocate_section_budgets(
        budget,
        available,
        has_content,
    )

    profiles = budgeted_memory_lines(
        prioritize_profile_memories(profile_memories),
        limit=section_budgets["profile"],
        budget=budget,
    )
    memories = budgeted_memory_lines(
        prioritize_memories(long_memories)[:max_long_memories],
        limit=section_budgets["long"],
        budget=budget,
    )
    summary = budget.truncate(conversation_summary or EMPTY_VALUE, section_budgets["summary"])
    recent = (
        budgeted_recent_lines(
            recent_values,
            limit=section_budgets["recent"],
            budget=budget,
        )
        if include_recent
        else None
    )
    context = format_memory_sections(profiles, memories, summary, recent)
    return budget.truncate(context, budget.limit)


def format_memory_sections(profile: str, long_term: str, summary: str, recent: str | None) -> str:
    sections = [
        f"Stable preferences and profile:\n{profile}",
        f"Relevant long-term memories:\n{long_term}",
        f"Conversation summary:\n{summary}",
    ]
    if recent is not None:
        sections.append(f"Recent conversation:\n{recent}")
    return "\n\n".join(sections)


def allocate_section_budgets(budget: "TextBudget", available: int, has_content: dict[str, bool]) -> dict[str, int]:
    settings = get_settings()
    weights = {
        "profile": settings.memory_context_profile_weight,
        "long": settings.memory_context_long_term_weight,
        "summary": settings.memory_context_summary_weight,
        "recent": settings.memory_context_recent_weight,
    }
    weights = {name: weight for name, weight in weights.items() if name in has_content}
    available = max(0, available)
    empty_budget = budget.count(f"- {EMPTY_VALUE}")
    minimums = {
        name: budget.minimum_section_limit if has_content.get(name) else empty_budget
        for name in weights
    }
    minimum_total = sum(minimums.values())
    if minimum_total > available:
        constrained_weights = {
            name: (
                weights[name]
                if has_content.get(name)
                else weights[name] * settings.memory_context_empty_section_weight_factor
            )
            for name in weights
        }
        return distribute_weighted_budget(available, constrained_weights)

    allocations = dict(minimums)
    remaining = available - minimum_total
    active_weights = {name: weight for name, weight in weights.items() if has_content.get(name)}
    for name, extra in distribute_weighted_budget(remaining, active_weights).items():
        allocations[name] += extra
    return allocations


def distribute_weighted_budget(total: int, weights: dict[str, float]) -> dict[str, int]:
    allocations = {name: 0 for name in weights}
    if total <= 0 or not weights:
        return allocations
    weight_total = sum(max(0.0, weight) for weight in weights.values())
    if weight_total <= 0:
        return allocations

    exact = {name: total * max(0.0, weight) / weight_total for name, weight in weights.items()}
    allocations = {name: int(value) for name, value in exact.items()}
    remainder = total - sum(allocations.values())
    order = sorted(weights, key=lambda name: (exact[name] - allocations[name], weights[name]), reverse=True)
    for name in order[:remainder]:
        allocations[name] += 1
    return allocations


def split_profile_memories(memories: list[dict]) -> tuple[list[dict], list[dict]]:
    profiles: list[dict] = []
    semantic: list[dict] = []
    for memory in memories:
        if policy.is_profile_memory(memory):
            profiles.append(memory)
        else:
            semantic.append(memory)
    return profiles, semantic


def build_memory_compression_sources(
    long_memories: list[dict],
    short_memory: list[dict],
    conversation_summary: str | None,
    profile_memories: list[dict],
    max_long_memories: int,
) -> list[dict]:
    sources: list[dict] = []
    for index, memory in enumerate(prioritize_profile_memories(profile_memories)):
        content = normalize_text(str(memory.get("content") or ""))
        if content:
            sources.append(
                {
                    "id": str(memory.get("id") or f"profile:{index}"),
                    "section": "profile",
                    "content": content,
                    "protected": True,
                }
            )
    for index, memory in enumerate(prioritize_memories(long_memories)[:max_long_memories]):
        content = normalize_text(str(memory.get("content") or ""))
        if content:
            sources.append(
                {
                    "id": str(memory.get("id") or f"long_term:{index}"),
                    "section": "long_term",
                    "content": content,
                    "protected": bool(memory.get("pinned")) or normalize_key(memory.get("kind")) == "instruction",
                }
            )
    if conversation_summary and conversation_summary.strip():
        sources.append(
            {
                "id": "conversation_summary",
                "section": "summary",
                "content": conversation_summary.strip(),
                "protected": False,
            }
        )
    for index, item in enumerate(short_memory):
        content = normalize_text(str(item.get("content") or ""))
        if content:
            sources.append(
                {
                    "id": f"recent:{index}",
                    "section": "recent",
                    "content": f"{item.get('role')}: {content}",
                    "protected": index >= max(0, len(short_memory) - 2),
                }
            )
    return sources


def render_memory_sources(sources: list[dict]) -> str:
    grouped = {"profile": [], "long_term": [], "summary": [], "recent": []}
    for source in sources:
        grouped[str(source["section"])].append(f"- {source['content']}")
    return format_memory_sections(
        join_lines_or_none(grouped["profile"]),
        join_lines_or_none(grouped["long_term"]),
        join_lines_or_none(grouped["summary"]),
        join_lines_or_none(grouped["recent"]) if grouped["recent"] else None,
    )


def join_lines_or_none(values: list[str]) -> str:
    return "\n".join(values) if values else f"- {EMPTY_VALUE}"


def budgeted_lines(values: list[object], limit: int, budget: "TextBudget") -> str:
    normalized = [normalize_text(str(value or "")) for value in values]
    normalized = [value for value in normalized if value]
    if not normalized:
        return budget.truncate(f"- {EMPTY_VALUE}", limit)

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


def budgeted_recent_lines(values: list[object], limit: int, budget: "TextBudget") -> str:
    normalized = [normalize_text(str(value or "")) for value in values]
    normalized = [value for value in normalized if value]
    if not normalized:
        return budget.truncate(f"- {EMPTY_VALUE}", limit)

    newest_first: list[str] = []
    used = 0
    for value in reversed(normalized):
        line = f"- {value}"
        line_cost = budget.count(line) + (budget.count("\n") if newest_first else 0)
        if used + line_cost <= limit:
            newest_first.append(line)
            used += line_cost
            continue
        if not newest_first:
            newest_first.append(budget.truncate(line, limit))
        break
    return "\n".join(reversed(newest_first))


def prioritize_profile_memories(memories: list[dict]) -> list[dict]:
    indexed = list(enumerate(memories))
    indexed.sort(key=lambda item: (-policy.profile_memory_priority(item[1]), item[0]))
    return [memory for _, memory in indexed]


def prioritize_memories(memories: list[dict]) -> list[dict]:
    indexed = list(enumerate(memories))
    indexed.sort(key=lambda item: (-memory_priority(item[1]), item[0]))
    return [memory for _, memory in indexed]


def select_memories_by_batches(
    memories: list[dict],
    batches: list[list[str]],
    limit: int,
) -> list[dict]:
    if limit <= 0 or not memories:
        return []
    if not batches:
        return prioritize_memories(memories)[:limit]

    memories_by_id = {
        str(memory["id"]): memory
        for memory in memories
        if memory.get("id") is not None
    }
    batch_memories: list[list[dict]] = []
    assigned_ids: set[str] = set()
    for batch in batches:
        current_batch = []
        for memory_id in batch:
            normalized_id = str(memory_id)
            memory = memories_by_id.get(normalized_id)
            if memory is None or normalized_id in assigned_ids:
                continue
            current_batch.append(memory)
            assigned_ids.add(normalized_id)
        batch_memories.append(current_batch)

    selected: list[dict] = []
    selected_object_ids: set[int] = set()
    max_batch_size = max((len(batch) for batch in batch_memories), default=0)
    for index in range(max_batch_size):
        for batch in batch_memories:
            if index >= len(batch):
                continue
            memory = batch[index]
            selected.append(memory)
            selected_object_ids.add(id(memory))
            if len(selected) == limit:
                return selected

    for memory in prioritize_memories(memories):
        if id(memory) in selected_object_ids:
            continue
        selected.append(memory)
        if len(selected) == limit:
            break
    return selected


def memory_priority(memory: dict) -> float:
    category = normalize_key(memory.get("category"))
    kind = normalize_key(memory.get("kind"))
    metadata = memory.get("metadata") if isinstance(memory.get("metadata"), dict) else {}
    importance = normalize_key(metadata.get("importance") or metadata.get("importance_level"))
    return (
        MEMORY_CATEGORY_PRIORITY.get(category, 0)
        + MEMORY_KIND_PRIORITY.get(kind, 0)
        + MEMORY_IMPORTANCE_PRIORITY.get(importance, 0)
    )


def normalize_key(value: object) -> str:
    return str(value or "").strip().lower()


def normalize_text(text: str) -> str:
    return " ".join(text.strip().split())


@dataclass(frozen=True)
class TextBudget:
    limit: int
    unit: str
    encoding: Any = None

    @property
    def minimum_section_limit(self) -> int:
        settings = get_settings()
        return (
            settings.memory_context_min_section_tokens
            if self.unit == "tokens"
            else settings.memory_context_min_section_chars
        )

    @classmethod
    def from_limits(cls, max_chars: int, max_tokens: int | None, model_name: str = "") -> "TextBudget":
        if max_tokens is not None:
            return cls(
                limit=max(0, max_tokens),
                unit="tokens",
                encoding=load_tokenizer(model_name),
            )
        return cls(limit=max(0, max_chars), unit="chars")

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
            return truncate_estimated_tokens(text, limit)
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
    if not text:
        return 0
    return max(1, (len(text.encode("utf-8")) + 3) // 4)


def truncate_estimated_tokens(text: str, limit: int) -> str:
    if limit <= 0:
        return ""
    if estimate_tokens(text) <= limit:
        return text
    low = 0
    high = len(text)
    best = ""
    while low <= high:
        keep = (low + high) // 2
        candidate = text[:keep].rstrip() + "..."
        if estimate_tokens(candidate) <= limit:
            best = candidate
            low = keep + 1
        else:
            high = keep - 1
    return best or truncate_chars("...", limit)


def truncate_chars(text: str, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    if max_chars <= 3:
        return text[:max_chars]
    return text[: max_chars - 3].rstrip() + "..."
