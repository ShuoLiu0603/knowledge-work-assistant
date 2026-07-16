from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

MEMORY_ACTIONS = {"create", "update", "supersede", "pending", "ignore"}
MEMORY_KINDS = {"preference", "profile", "instruction"}
MEMORY_CATEGORIES = {
    "general",
    "name",
    "preferred_address",
    "current_role",
    "language",
    "response_detail",
    "format",
    "tone",
    "accessibility",
    "global_instruction",
    "company",
    "team",
    "role",
    "profile",
    "background",
    "current_project",
    "current_stack",
    "backend_framework",
    "frontend_framework",
    "architecture",
    "tooling",
    "decision",
    "event",
    "task",
    "workflow",
    "task_instruction",
    "domain_rule",
}
IMPORTANCE_LEVELS = {"low", "medium", "high"}
SENSITIVITY_LEVELS = {"low", "medium", "high"}
MEMORY_RELATIONS = {"independent", "equivalent", "refinement", "replacement", "uncertain", "discard"}


class MemoryClassificationOutput(BaseModel):
    kind: Literal["preference", "profile", "instruction"] = "preference"
    category: str = Field(default="general", max_length=80)

    @field_validator("kind", mode="before")
    @classmethod
    def normalize_kind(cls, value: object) -> str:
        return normalize_allowed(value, MEMORY_KINDS, "preference")

    @field_validator("category", mode="before")
    @classmethod
    def normalize_category(cls, value: object) -> str:
        normalized = normalize_whitespace(str(value or "")).lower()
        return normalized if normalized in MEMORY_CATEGORIES else "general"


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
        if str(value or "").strip().lower() == "project":
            return "profile"
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


class MemoryCandidateOutput(BaseModel):
    content: str = Field(default="", max_length=2000)
    kind: str = "preference"
    category: str = Field(default="general", max_length=80)
    canonical_key: str = Field(default="", max_length=160)
    importance: str = "low"
    sensitivity: str = "high"
    evidence: str = Field(min_length=1, max_length=1000)
    reason: str = Field(default="", max_length=1000)

    @field_validator("content", "category", "canonical_key", "evidence", "reason", mode="before")
    @classmethod
    def clean_text(cls, value: object) -> str:
        return normalize_whitespace(str(value or ""))

    @field_validator("kind", mode="before")
    @classmethod
    def normalize_kind(cls, value: object) -> str:
        if str(value or "").strip().lower() == "project":
            return "profile"
        return normalize_allowed(value, MEMORY_KINDS, "preference")

    @field_validator("importance", mode="before")
    @classmethod
    def normalize_importance(cls, value: object) -> str:
        return normalize_allowed(value, IMPORTANCE_LEVELS, "low")

    @field_validator("sensitivity", mode="before")
    @classmethod
    def normalize_sensitivity(cls, value: object) -> str:
        return normalize_allowed(value, SENSITIVITY_LEVELS, "high")


class MemoryCandidatesOutput(BaseModel):
    candidates: list[MemoryCandidateOutput] = Field(default_factory=list)

    @field_validator("candidates", mode="before")
    @classmethod
    def limit_candidates(cls, value: object) -> list:
        if isinstance(value, dict):
            return [value]
        return value[:5] if isinstance(value, list) else []


class MemoryJudgeDecisionOutput(BaseModel):
    relation: str = "discard"
    target_memory_id: str | None = None
    content: str = Field(default="", max_length=2000)
    reason: str = Field(default="", max_length=1000)

    @field_validator("relation", mode="before")
    @classmethod
    def normalize_relation(cls, value: object) -> str:
        return normalize_allowed(value, MEMORY_RELATIONS, "discard")

    @field_validator("content", "target_memory_id", "reason", mode="before")
    @classmethod
    def clean_text(cls, value: object) -> str:
        return normalize_whitespace(str(value or ""))


class CompressedMemoryItemOutput(BaseModel):
    content: str = Field(min_length=1)
    source_ids: list[str] = Field(min_length=1)
    section: Literal["profile", "long_term", "summary", "recent"]


class MemoryContextCompressionOutput(BaseModel):
    items: list[CompressedMemoryItemOutput] = Field(default_factory=list)


class ExtractedEvidenceOutput(BaseModel):
    chunk_id: str = Field(min_length=1)
    quotes: list[str] = Field(min_length=1)


class RagEvidenceCompressionOutput(BaseModel):
    evidence: list[ExtractedEvidenceOutput] = Field(default_factory=list)


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
