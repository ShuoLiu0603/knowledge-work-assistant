from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MemoryAction:
    action: str
    memory_id: str | None
    content: str
    reason: str


@dataclass(frozen=True)
class MemorySource:
    text: str
    conversation_id: str | None = None
    message_id: str | None = None


@dataclass(frozen=True)
class MemoryEmbedding:
    vector: list[float]
    model: str
    dimension: int
