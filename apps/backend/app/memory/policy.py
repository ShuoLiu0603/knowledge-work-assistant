from __future__ import annotations

import hashlib
from typing import Any

from fastapi import HTTPException, status

from app.core.config import get_settings

ALLOWED_MEMORY_STATUSES = {"active", "pending", "superseded", "ignored", "deleted"}
AUTO_MEMORY_CONFIDENCE = 0.75
PENDING_MEMORY_CONFIDENCE = 0.55
AUTO_OPERATION_CONFIDENCE = 0.8
SUPERSEDE_OPERATION_CONFIDENCE = 0.85
PENDING_OPERATION_CONFIDENCE = 0.6
MAX_MEMORY_OPERATIONS = 3
MEMORY_EDITOR_CONTEXT_LIMIT = 30
MEMORY_EDITOR_CANDIDATE_LIMIT = 80
MEMORY_SOURCE_MAX_CHARS = 700
SUMMARY_DELTA_MAX_CHARS = 12000
FULL_MEMORY_RECALL_LIMIT = 20
STICKY_MEMORY_CATEGORIES = {"response_detail", "language", "format"}
MEMORY_RECALL_MARKERS = (
    "你记得",
    "还记得",
    "记住了什么",
    "记了什么",
    "长期记忆",
    "我的偏好",
    "我的项目",
    "我的角色",
    "我的名字",
    "我叫什么",
    "我的工作",
    "我偏好",
    "我喜欢什么",
    "你知道我",
    "关于我",
    "remember about me",
    "what do you remember",
    "my preference",
    "my preferences",
    "my project",
    "my role",
    "my name",
    "my job",
    "about me",
)
FULL_MEMORY_RECALL_MARKERS = (
    "你记得我什么",
    "你还记得我什么",
    "你都记得我什么",
    "你知道我什么",
    "关于我你记得什么",
    "what do you remember",
    "what do you know about me",
    "what have you saved",
)
DO_NOT_REMEMBER_MARKERS = ("不要记住", "别记住", "无需记住", "不要保存", "别保存")
DO_NOT_REMEMBER_EN_MARKERS = ("do not remember", "don't remember", "dont remember")
NO_MEMORY_TURN_MARKERS = (
    "不要使用记忆",
    "别使用记忆",
    "不要读取记忆",
    "别读取记忆",
    "不用记忆",
    "不要保存也不要使用记忆",
    "临时模式",
    "临时对话",
)
NO_MEMORY_TURN_EN_MARKERS = (
    "no memory",
    "without memory",
    "temporary chat",
    "temporary mode",
    "do not use memory",
    "don't use memory",
    "dont use memory",
)


def should_ignore_memory_request(text: str) -> bool:
    lowered = text.lower()
    return any(marker in text for marker in DO_NOT_REMEMBER_MARKERS) or any(
        marker in lowered for marker in DO_NOT_REMEMBER_EN_MARKERS
    )


def should_skip_memory_for_turn(text: str) -> bool:
    lowered = text.lower()
    return any(marker in text for marker in NO_MEMORY_TURN_MARKERS) or any(
        marker in lowered for marker in NO_MEMORY_TURN_EN_MARKERS
    )


def validate_memory_status(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in ALLOWED_MEMORY_STATUSES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid memory status")
    return normalized


def normalize_memory_content(content: str) -> str:
    return " ".join(content.strip().lower().split())


def hash_content(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def infer_memory_category(content: str) -> str:
    if any(marker in content for marker in ("简洁", "详细", "concise", "detailed", "short", "brief")):
        return "response_detail"
    if any(marker in content for marker in ("中文", "英文", "chinese", "english")):
        return "language"
    if any(marker in content for marker in ("列表", "表格", "markdown", "格式")):
        return "format"
    return "general"


def is_memory_recall_query(query: str) -> bool:
    normalized = " ".join(query.lower().split())
    return any(marker in normalized for marker in (*MEMORY_RECALL_MARKERS, *FULL_MEMORY_RECALL_MARKERS))


def is_full_memory_recall_query(query: str) -> bool:
    normalized = " ".join(query.lower().split())
    return any(marker in normalized for marker in FULL_MEMORY_RECALL_MARKERS)


def can_auto_create(operation: Any) -> bool:
    return is_safe_memory_operation(operation, AUTO_OPERATION_CONFIDENCE) and operation.importance == "high"


def can_auto_update(operation: Any) -> bool:
    return is_safe_memory_operation(operation, AUTO_OPERATION_CONFIDENCE) and operation.importance in {"medium", "high"}


def can_auto_supersede(operation: Any, target_status: str) -> bool:
    return (
        target_status == "active"
        and is_safe_memory_operation(operation, SUPERSEDE_OPERATION_CONFIDENCE)
        and operation.importance == "high"
    )


def is_safe_memory_operation(operation: Any, confidence_threshold: float) -> bool:
    return (
        operation.confidence >= confidence_threshold
        and operation.sensitivity == "low"
        and bool(operation.evidence.strip())
    )


def resolve_operation_category(operation: Any) -> str:
    category = (operation.category or "").strip().lower()
    if category and category != "general":
        return category[:80]
    return infer_memory_category(normalize_memory_content(operation.content))


def resolve_memory_category(candidate: Any) -> str:
    category = (candidate.category or "").strip().lower()
    if category and category != "general":
        return category[:80]
    return infer_memory_category(normalize_memory_content(candidate.content))


def memory_operation_metadata(operation: Any, decision: str) -> dict:
    return {
        "confidence": operation.confidence,
        "importance": operation.importance,
        "sensitivity": operation.sensitivity,
        "evidence": operation.evidence,
        "reason": operation.reason,
        "decision": decision,
        "proposed_action": operation.action,
        "target_memory_id": operation.target_memory_id,
    }


def metadata_confidence(memory: Any) -> float:
    try:
        return float((memory.extra_metadata or {}).get("confidence", 0))
    except (TypeError, ValueError):
        return 0.0


def retrieval_similarity_threshold() -> float:
    return max(0.2, min(0.45, get_settings().memory_semantic_threshold * 0.35))


def semantic_similarity_threshold() -> float:
    return get_settings().memory_semantic_threshold
