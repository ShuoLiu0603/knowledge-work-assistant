from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

INTENTS = {"rag", "memory", "chat", "summary", "writing"}
MEMORY_ACTIONS = {"create", "update", "supersede", "pending", "ignore"}
MEMORY_KINDS = {"preference", "profile", "project", "instruction"}
IMPORTANCE_LEVELS = {"low", "medium", "high"}
SENSITIVITY_LEVELS = {"low", "medium", "high"}


class IntentOutput(BaseModel):
    intent: Literal["rag", "memory", "chat", "summary", "writing"] = "rag"

    @field_validator("intent", mode="before")
    @classmethod
    def normalize_intent(cls, value: object) -> str:
        text = str(value or "").strip().lower()
        if any(marker in text for marker in ("summary", "summarize", "recap", "总结", "摘要", "概括", "归纳")):
            return "summary"
        if any(marker in text for marker in ("writing", "write", "draft", "compose", "写作", "撰写", "起草")):
            return "writing"
        if any(marker in text for marker in ("memory_answer", "memory", "记忆")):
            return "memory"
        if any(marker in text for marker in ("chat", "small", "聊天", "闲聊", "寒暄", "问候")):
            return "chat"
        return "rag"


class MemoryOperationOutput(BaseModel):
    action: str = "ignore"
    content: str = Field(default="", max_length=2000)
    target_memory_id: str | None = None
    kind: str = "preference"
    category: str = Field(default="general", max_length=80)
    canonical_key: str = Field(default="", max_length=160)
    importance: str = "low"
    sensitivity: str = "high"
    evidence: str = Field(default="", max_length=1000)
    reason: str = Field(default="", max_length=1000)

    @field_validator("action", mode="before")
    @classmethod
    def normalize_action(cls, value: object) -> str:
        return normalize_memory_action(value)

    @field_validator("content", "target_memory_id", "category", "canonical_key", "evidence", "reason", mode="before")
    @classmethod
    def clean_text(cls, value: object) -> str:
        return normalize_whitespace(str(value or ""))

    @field_validator("kind", mode="before")
    @classmethod
    def normalize_kind(cls, value: object) -> str:
        return normalize_allowed(value, MEMORY_KINDS, "preference")

    @field_validator("importance", mode="before")
    @classmethod
    def normalize_importance(cls, value: object) -> str:
        return normalize_allowed(value, IMPORTANCE_LEVELS, "low")

    @field_validator("sensitivity", mode="before")
    @classmethod
    def normalize_sensitivity(cls, value: object) -> str:
        return normalize_allowed(value, SENSITIVITY_LEVELS, "high")


class MemoryOperationsOutput(BaseModel):
    operations: list[MemoryOperationOutput] = Field(default_factory=list)

    @field_validator("operations", mode="before")
    @classmethod
    def limit_operations(cls, value: object) -> list:
        if isinstance(value, dict):
            return [value]
        return value[:5] if isinstance(value, list) else []


def parse_json_value(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", raw)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None


def normalize_allowed(value: object, allowed: set[str], fallback: str) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in allowed else fallback


def normalize_memory_action(value: object) -> str:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return "ignore"
    aliases = {
        "add": "create",
        "save": "create",
        "remember": "create",
        "insert": "create",
        "modify": "update",
        "edit": "update",
        "merge": "update",
        "replace": "supersede",
        "overwrite": "supersede",
        "contradict": "supersede",
        "review": "pending",
        "needs_review": "pending",
        "uncertain": "pending",
        "skip": "ignore",
        "none": "ignore",
        "noop": "ignore",
        "no_op": "ignore",
    }
    return aliases.get(normalized, normalized)


def normalize_whitespace(text: str) -> str:
    return " ".join(text.strip().split())
