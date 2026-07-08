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
        if "summary" in text:
            return "summary"
        if "writing" in text:
            return "writing"
        if "memory_answer" in text or "memory" in text:
            return "memory"
        if "chat" in text or "small" in text:
            return "chat"
        return "rag"


class MemoryCandidateOutput(BaseModel):
    content: str = Field(default="", max_length=2000)
    kind: str = "preference"
    category: str = Field(default="general", max_length=80)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    sensitivity: str = "low"

    @field_validator("content", "category", mode="before")
    @classmethod
    def clean_text(cls, value: object) -> str:
        return normalize_whitespace(str(value or ""))

    @field_validator("kind", mode="before")
    @classmethod
    def normalize_kind(cls, value: object) -> str:
        return normalize_allowed(value, MEMORY_KINDS, "preference")

    @field_validator("sensitivity", mode="before")
    @classmethod
    def normalize_sensitivity(cls, value: object) -> str:
        return normalize_allowed(value, SENSITIVITY_LEVELS, "low")


class MemoryCandidatesOutput(BaseModel):
    candidates: list[MemoryCandidateOutput] = Field(default_factory=list)

    @field_validator("candidates", mode="before")
    @classmethod
    def limit_candidates(cls, value: object) -> list:
        return value[:5] if isinstance(value, list) else []


class MemoryOperationOutput(BaseModel):
    action: str = "ignore"
    content: str = Field(default="", max_length=2000)
    target_memory_id: str | None = None
    kind: str = "preference"
    category: str = Field(default="general", max_length=80)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    importance: str = "low"
    sensitivity: str = "low"
    evidence: str = Field(default="", max_length=1000)
    reason: str = Field(default="", max_length=1000)

    @field_validator("action", mode="before")
    @classmethod
    def normalize_action(cls, value: object) -> str:
        return normalize_allowed(value, MEMORY_ACTIONS, "ignore")

    @field_validator("content", "target_memory_id", "category", "evidence", "reason", mode="before")
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
        return normalize_allowed(value, SENSITIVITY_LEVELS, "low")


class MemoryOperationsOutput(BaseModel):
    operations: list[MemoryOperationOutput] = Field(default_factory=list)

    @field_validator("operations", mode="before")
    @classmethod
    def limit_operations(cls, value: object) -> list:
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


def normalize_whitespace(text: str) -> str:
    return " ".join(text.strip().split())
