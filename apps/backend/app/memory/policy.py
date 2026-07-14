from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any

from fastapi import HTTPException, status

from app.core.config import get_settings

ALLOWED_MEMORY_STATUSES = {"active", "pending", "superseded", "ignored", "deleted"}
MEMORY_LAYERS = {"profile", "semantic", "episodic", "procedural"}
_SETTINGS = get_settings()
MAX_MEMORY_OPERATIONS = _SETTINGS.memory_max_operations
MEMORY_EDITOR_CONTEXT_LIMIT = _SETTINGS.memory_editor_context_limit
MEMORY_EDITOR_CANDIDATE_LIMIT = _SETTINGS.memory_editor_candidate_limit
MEMORY_RECALL_CANDIDATE_LIMIT = _SETTINGS.memory_recall_candidate_limit
MEMORY_SOURCE_MAX_CHARS = _SETTINGS.memory_source_max_chars
SUMMARY_DELTA_MAX_CHARS = _SETTINGS.memory_summary_delta_max_chars
FULL_MEMORY_RECALL_LIMIT = _SETTINGS.memory_full_recall_limit
CANONICAL_KEY_MAX_LENGTH = 160

CORE_PROFILE_SINGLETON_CATEGORIES = {
    "response_detail",
    "language",
    "format",
    "name",
    "preferred_address",
    "current_role",
    "tone",
    "accessibility",
}
CORE_PROFILE_CATEGORIES = CORE_PROFILE_SINGLETON_CATEGORIES | {"global_instruction"}
ON_DEMAND_SINGLETON_CATEGORIES = {
    "company",
    "team",
    "current_project",
    "current_stack",
    "backend_framework",
    "frontend_framework",
}
EPISODIC_MEMORY_CATEGORIES = {"decision", "event", "task"}
PROCEDURAL_MEMORY_CATEGORIES = {"workflow", "task_instruction", "domain_rule"}
GLOBAL_INSTRUCTION_MARKERS = (
    "所有回复",
    "每次回复",
    "以后都",
    "所有对话",
    "每次对话",
    "任何对话",
    "全局",
    "始终使用",
    "all responses",
    "every response",
    "all conversations",
    "every conversation",
    "from now on always",
    "globally",
)

# Kept as a public alias for callers that display memory governance metrics.
# "Sticky" now means core profile only; pinning and importance never change layers.
STICKY_MEMORY_CATEGORIES = CORE_PROFILE_CATEGORIES

MEMORY_RECALL_MARKERS = (
    "\u4f60\u8bb0\u5f97",
    "\u8fd8\u8bb0\u5f97",
    "\u8bb0\u4f4f\u4e86\u4ec0\u4e48",
    "\u957f\u671f\u8bb0\u5fc6",
    "\u6211\u7684\u504f\u597d",
    "\u6211\u7684\u9879\u76ee",
    "\u6211\u7684\u89d2\u8272",
    "\u6211\u7684\u540d\u5b57",
    "\u6211\u53eb\u4ec0\u4e48",
    "\u4f60\u77e5\u9053\u6211",
    "\u5173\u4e8e\u6211",
    "remember about me",
    "what do you remember",
    "what do you know about me",
    "what have you saved",
    "my preference",
    "my preferences",
    "my project",
    "my role",
    "my name",
    "my job",
    "about me",
)
FULL_MEMORY_RECALL_MARKERS = (
    "\u4f60\u8bb0\u5f97\u6211\u4ec0\u4e48",
    "\u4f60\u8fd8\u8bb0\u5f97\u6211\u4ec0\u4e48",
    "\u4f60\u90fd\u8bb0\u5f97\u6211\u4ec0\u4e48",
    "\u4f60\u77e5\u9053\u6211\u4ec0\u4e48",
    "\u5173\u4e8e\u6211\u4f60\u8bb0\u5f97\u4ec0\u4e48",
    "what do you remember",
    "what do you know about me",
    "what have you saved",
)
DO_NOT_REMEMBER_MARKERS = (
    "\u4e0d\u8981\u8bb0\u4f4f",
    "\u522b\u8bb0\u4f4f",
    "\u65e0\u9700\u8bb0\u4f4f",
    "\u4e0d\u8981\u4fdd\u5b58",
    "\u522b\u4fdd\u5b58",
    "do not remember",
    "don't remember",
    "dont remember",
    "do not save",
    "don't save",
    "dont save",
)
NO_MEMORY_TURN_MARKERS = (
    "\u4e0d\u8981\u4f7f\u7528\u8bb0\u5fc6",
    "\u522b\u4f7f\u7528\u8bb0\u5fc6",
    "\u4e0d\u8981\u8bfb\u53d6\u8bb0\u5fc6",
    "\u522b\u8bfb\u53d6\u8bb0\u5fc6",
    "\u4e0d\u7528\u8bb0\u5fc6",
    "\u4e34\u65f6\u6a21\u5f0f",
    "\u4e34\u65f6\u5bf9\u8bdd",
    "no memory",
    "without memory",
    "temporary chat",
    "temporary mode",
    "do not use memory",
    "don't use memory",
    "dont use memory",
)

RESPONSE_BRIEF_MARKERS = ("\u7b80\u6d01", "\u7b80\u77ed", "\u77ed\u4e00\u70b9", "concise", "brief", "short")
RESPONSE_DETAILED_MARKERS = ("\u8be6\u7ec6", "\u5b8c\u6574", "\u5c55\u5f00", "detailed", "complete", "full")
LANGUAGE_MARKERS = ("\u4e2d\u6587", "\u82f1\u6587", "\u82f1\u8bed", "\u6c49\u8bed", "chinese", "english", "mandarin")
FORMAT_MARKERS = ("\u5217\u8868", "\u8868\u683c", "\u683c\u5f0f", "markdown", "table", "bullet", "json")
SENSITIVE_MEMORY_MARKERS = (
    "api key",
    "apikey",
    "access token",
    "auth token",
    "bearer token",
    "password",
    "passcode",
    "credential",
    "secret",
    "private key",
    "ssh key",
    "passport",
    "social security",
    "ssn",
    "credit card",
    "bank account",
    "account balance",
    "bank statement",
    "routing number",
    "swift code",
    "tax id",
    "tax number",
    "transaction history",
    "phone number",
    "mobile number",
    "telephone number",
    "home address",
    "residential address",
    "mailing address",
    "street address",
    "medical record",
    "medical history",
    "health record",
    "mental health",
    "blood type",
    "diagnosed with",
    "prescription",
    "salary",
    "\u5bc6\u7801",
    "\u53e3\u4ee4",
    "\u4ee4\u724c",
    "\u5bc6\u94a5",
    "\u79c1\u94a5",
    "\u51ed\u8bc1",
    "\u62a4\u7167",
    "\u8eab\u4efd\u8bc1",
    "\u94f6\u884c\u5361",
    "\u4fe1\u7528\u5361",
    "\u624b\u673a\u53f7",
    "\u624b\u673a\u53f7\u7801",
    "\u8054\u7cfb\u7535\u8bdd",
    "\u7535\u8bdd\u53f7\u7801",
    "\u5bb6\u5ead\u5730\u5740",
    "\u5bb6\u5ead\u4f4f\u5740",
    "\u5c45\u4f4f\u5730\u5740",
    "\u6536\u8d27\u5730\u5740",
    "\u901a\u8baf\u5730\u5740",
    "\u8be6\u7ec6\u5730\u5740",
    "\u75c5\u5386",
    "\u75c5\u53f2",
    "\u8bca\u65ad",
    "\u5904\u65b9",
    "\u7528\u836f",
    "\u8840\u578b",
    "\u5fc3\u7406\u5065\u5eb7",
    "\u7cbe\u795e\u5065\u5eb7",
    "\u6b8b\u75be",
    "\u5de5\u8d44",
    "\u85aa\u8d44",
    "\u6536\u5165",
    "\u7a0e\u53f7",
    "\u7eb3\u7a0e\u4eba\u8bc6\u522b\u53f7",
    "\u8d26\u6237\u4f59\u989d",
    "\u94f6\u884c\u6d41\u6c34",
    "\u4ea4\u6613\u8bb0\u5f55",
)
SENSITIVE_MEMORY_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9_]{16,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    # ASCII lookarounds deliberately allow CJK text immediately beside the email address.
    re.compile(r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![A-Za-z0-9.-])"),
    re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d[- ]?\d{4}[- ]?\d{4}(?!\d)"),
    re.compile(r"(?<!\d)0\d{2,3}[- ]?\d{7,8}(?!\d)"),
    re.compile(r"\b\+\d{1,3}(?:[- (]\d{1,4}){2,5}\)?\b"),
    re.compile(
        r"\b\d{1,6}\s+[A-Za-z0-9][A-Za-z0-9 .'-]{1,60}\s"
        r"(?:Street|St|Road|Rd|Avenue|Ave|Boulevard|Blvd|Lane|Ln|Drive|Dr|Way|Court|Ct)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:[\u4e00-\u9fff]{2,}(?:\u7701|\u81ea\u6cbb\u533a|\u5e02))?"
        r"[\u4e00-\u9fff]{2,}(?:\u5e02|\u533a|\u53bf)"
        r"[\u4e00-\u9fffA-Za-z0-9]{2,}(?:\u8857\u9053|\u9547|\u4e61|\u8def|\u8857|\u5df7)"
        r"[\u4e00-\u9fffA-Za-z0-9-]{0,30}(?:\u53f7|\u5ba4)"
    ),
    re.compile(
        r"(?:\bIBAN\s*:?[ ]*[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b|"
        r"\bSWIFT(?:[ ]+code)?\s*:?[ ]*[A-Z0-9]{8,11}\b|"
        r"\b(?:routing|account)\s*(?:number|no\.?|#|:)\s*[A-Z0-9 -]{6,34}\b)",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:diagnosed|diagnosis|medication|prescription)\s*(?:with|is|:)?\s+[^\n,.;]{2,80}", re.IGNORECASE),
    re.compile(r"(?<!\d)\d{6}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)"),
)
SENSITIVITY_LEVELS = {"low", "medium", "high"}
GROUNDING_STOP_WORDS = {
    "a",
    "am",
    "an",
    "and",
    "are",
    "from",
    "i",
    "is",
    "me",
    "my",
    "of",
    "please",
    "the",
    "to",
    "user",
}


def should_ignore_memory_request(text: str) -> bool:
    normalized = normalize_query_text(text)
    return any(marker in normalized for marker in DO_NOT_REMEMBER_MARKERS)


def is_explicit_global_instruction(text: str) -> bool:
    normalized = normalize_query_text(text)
    return any(marker in normalized for marker in GLOBAL_INSTRUCTION_MARKERS)


def should_skip_memory_for_turn(text: str) -> bool:
    normalized = normalize_query_text(text)
    return any(marker in normalized for marker in (*NO_MEMORY_TURN_MARKERS, *DO_NOT_REMEMBER_MARKERS))


def validate_memory_status(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in ALLOWED_MEMORY_STATUSES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid memory status")
    return normalized


def validate_memory_layer(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in MEMORY_LAYERS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid memory layer")
    return normalized


def normalize_memory_content(content: str) -> str:
    return " ".join(content.strip().lower().split())


def normalize_query_text(text: str) -> str:
    return " ".join(text.strip().lower().split())


def normalize_canonical_key(value: object) -> str:
    text = normalize_query_text(str(value or ""))
    if not text:
        return ""
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"[^a-z0-9_.:/-]+", "", text)
    text = re.sub(r"_+", "_", text).strip("._:-/")
    return text[:CANONICAL_KEY_MAX_LENGTH]


def hash_content(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def infer_memory_category(content: str) -> str:
    normalized = normalize_query_text(content)
    if any(marker in normalized for marker in (*RESPONSE_BRIEF_MARKERS, *RESPONSE_DETAILED_MARKERS)):
        return "response_detail"
    if any(marker in normalized for marker in LANGUAGE_MARKERS):
        return "language"
    if any(marker in normalized for marker in FORMAT_MARKERS):
        return "format"
    return "general"


def is_memory_recall_query(query: str) -> bool:
    normalized = normalize_query_text(query)
    return any(marker in normalized for marker in (*MEMORY_RECALL_MARKERS, *FULL_MEMORY_RECALL_MARKERS))


def is_full_memory_recall_query(query: str) -> bool:
    normalized = normalize_query_text(query)
    return any(marker in normalized for marker in FULL_MEMORY_RECALL_MARKERS)


def can_auto_create(operation: Any, user_message: str | None = None) -> bool:
    return is_safe_memory_operation(operation, user_message=user_message)


def can_auto_update(operation: Any, user_message: str | None = None) -> bool:
    return is_safe_memory_operation(operation, user_message=user_message)


def can_auto_supersede(operation: Any, target_status: str, user_message: str | None = None) -> bool:
    return target_status == "active" and is_safe_memory_operation(operation, user_message=user_message)


def is_safe_memory_operation(operation: Any, user_message: str | None = None) -> bool:
    return (
        normalize_sensitivity_level(getattr(operation, "sensitivity", None)) == "low"
        and is_evidence_grounded(getattr(operation, "evidence", ""), user_message)
        and is_content_grounded_in_evidence(
            getattr(operation, "content", ""),
            getattr(operation, "evidence", ""),
        )
        and not has_sensitive_memory_content(operation.content, operation.evidence)
    )


def normalize_sensitivity_level(value: object) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in SENSITIVITY_LEVELS else "high"


def is_evidence_grounded(evidence: object, user_message: object) -> bool:
    normalized_evidence = normalize_evidence_text(evidence)
    normalized_message = normalize_evidence_text(user_message)
    return bool(normalized_evidence and normalized_message and normalized_evidence in normalized_message)


def normalize_evidence_text(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = " ".join(text.strip().split())
    return text.strip(" \"'`\u2018\u2019\u201c\u201d")


def is_content_grounded_in_evidence(content: object, evidence: object) -> bool:
    normalized_content = normalize_evidence_text(content)
    normalized_evidence = normalize_evidence_text(evidence)
    if not normalized_content or not normalized_evidence:
        return False
    if normalized_content in normalized_evidence or normalized_evidence in normalized_content:
        return True

    content_terms = memory_grounding_terms(normalized_content)
    evidence_terms = memory_grounding_terms(normalized_evidence)
    if not content_terms or not evidence_terms:
        return False
    shared_terms = content_terms & evidence_terms
    return bool(shared_terms) and (
        len(shared_terms) / min(len(content_terms), len(evidence_terms))
        >= get_settings().memory_grounding_overlap_threshold
    )


def memory_grounding_terms(value: str) -> set[str]:
    ascii_terms = {
        normalize_grounding_word(term)
        for term in re.findall(r"[a-z0-9_]+", value)
        if term not in GROUNDING_STOP_WORDS
    }
    ascii_terms.discard("")

    cjk_terms: set[str] = set()
    for sequence in re.findall(r"[\u3400-\u9fff]+", value):
        if len(sequence) == 1:
            cjk_terms.add(sequence)
            continue
        cjk_terms.update(sequence[index : index + 2] for index in range(len(sequence) - 1))
    return ascii_terms | cjk_terms


def normalize_grounding_word(value: str) -> str:
    if len(value) > 4 and value.endswith("ies"):
        return f"{value[:-3]}y"
    for suffix in ("ing", "ed", "es", "s"):
        if len(value) > len(suffix) + 2 and value.endswith(suffix):
            return value[: -len(suffix)]
    return value


def has_sensitive_memory_content(*values: object) -> bool:
    raw = "\n".join(str(value or "") for value in values)
    normalized = normalize_query_text(raw)
    if any(marker in normalized for marker in SENSITIVE_MEMORY_MARKERS):
        return True
    return any(pattern.search(raw) for pattern in SENSITIVE_MEMORY_PATTERNS)


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


def is_profile_memory(memory: Any) -> bool:
    memory_layer = normalize_key(get_memory_field(memory, "memory_layer"))
    category = get_memory_field(memory, "category")
    if memory_layer in MEMORY_LAYERS:
        return memory_layer == "profile" or normalize_key(category) in CORE_PROFILE_CATEGORIES
    metadata = get_memory_metadata(memory)
    return is_profile_memory_payload(
        category,
        get_memory_field(memory, "kind"),
        metadata,
    )


def is_profile_memory_payload(category: object, kind: object, metadata: dict | None = None) -> bool:
    metadata = metadata or {}
    layer = normalize_key(metadata.get("memory_layer") or metadata.get("layer"))
    if layer == "profile":
        return True
    return normalize_key(category) in CORE_PROFILE_CATEGORIES


def is_profile_singleton_category(category: object) -> bool:
    return normalize_key(category) in CORE_PROFILE_SINGLETON_CATEGORIES


def is_profile_singleton_slot(profile_slot: object) -> bool:
    return normalize_key(profile_slot) in CORE_PROFILE_SINGLETON_CATEGORIES


def memory_layer_for_fields(kind: object, category: object, metadata: dict | None = None) -> str:
    normalized_category = normalize_key(category)
    if is_profile_memory_payload(category, kind, metadata):
        return "profile"
    if normalized_category in EPISODIC_MEMORY_CATEGORIES:
        return "episodic"
    if normalize_key(kind) == "instruction" or normalized_category in PROCEDURAL_MEMORY_CATEGORIES:
        return "procedural"
    return "semantic"


def profile_slot_for_fields(kind: object, category: object) -> str:
    normalized_category = normalize_key(category)
    if normalized_category in CORE_PROFILE_SINGLETON_CATEGORIES:
        return normalized_category
    return ""


def pinned_for_layer(memory_layer: str) -> bool:
    return normalize_key(memory_layer) == "profile"


def canonical_key_for_operation(operation: Any, category: str, normalized_content: str) -> str:
    return canonical_key_for_fields(
        kind=operation.kind,
        category=category,
        normalized_content=normalized_content,
        explicit_key=getattr(operation, "canonical_key", ""),
    )


def canonical_key_for_candidate(candidate: Any, category: str, normalized_content: str) -> str:
    return canonical_key_for_fields(
        kind=candidate.kind,
        category=category,
        normalized_content=normalized_content,
        explicit_key=getattr(candidate, "canonical_key", ""),
    )


def canonical_key_for_fields(
    *,
    kind: object,
    category: object,
    normalized_content: str = "",
    explicit_key: object = "",
) -> str:
    explicit = normalize_canonical_key(explicit_key)
    if explicit:
        return explicit
    normalized_category = normalize_key(category)
    if normalized_category in CORE_PROFILE_SINGLETON_CATEGORIES:
        return f"profile:{normalized_category}"
    if normalized_category in ON_DEMAND_SINGLETON_CATEGORIES:
        prefix = "profile" if normalized_category in {"company", "team"} else "project"
        return f"{prefix}:{normalized_category}"
    return ""


def canonical_key_for_profile_slot(profile_slot: object) -> str:
    slot = normalize_key(profile_slot)
    if slot in CORE_PROFILE_SINGLETON_CATEGORIES:
        return f"profile:{slot}"
    return ""


def memory_scope_id(user_id: str, scope_id: str | None = None) -> str:
    return (scope_id or user_id).strip()


def profile_memory_priority(memory: Any) -> float:
    category = normalize_key(get_memory_field(memory, "category"))
    kind = normalize_key(get_memory_field(memory, "kind"))
    metadata = get_memory_metadata(memory)
    importance = normalize_key(metadata.get("importance") or metadata.get("importance_level"))
    category_priority = {
        "language": 100,
        "format": 95,
        "response_detail": 90,
        "name": 88,
        "preferred_address": 86,
        "current_role": 80,
        "tone": 78,
        "accessibility": 76,
        "global_instruction": 70,
    }
    kind_priority = {
        "instruction": 30,
        "profile": 20,
        "preference": 10,
    }
    importance_priority = {
        "high": 30,
        "medium": 15,
        "low": 0,
    }
    return (
        category_priority.get(category, 0)
        + kind_priority.get(kind, 0)
        + importance_priority.get(importance, 0)
    )


def memory_operation_metadata(operation: Any, decision: str) -> dict:
    category = resolve_operation_category(operation)
    memory_layer = memory_layer_for_fields(operation.kind, category)
    canonical_key = canonical_key_for_operation(operation, category, normalize_memory_content(operation.content))
    return {
        "importance": operation.importance,
        "sensitivity": normalize_sensitivity_level(operation.sensitivity),
        "evidence": operation.evidence,
        "reason": operation.reason,
        "decision": decision,
        "proposed_action": operation.action,
        "target_memory_id": operation.target_memory_id,
        "expected_revision": getattr(operation, "expected_revision", None),
        "canonical_key": canonical_key,
        "memory_layer": memory_layer,
        "profile_slot": profile_slot_for_fields(operation.kind, category),
    }


def get_memory_field(memory: Any, field_name: str) -> object:
    if isinstance(memory, dict):
        return memory.get(field_name)
    return getattr(memory, field_name, None)


def get_memory_metadata(memory: Any) -> dict:
    if isinstance(memory, dict):
        metadata = memory.get("metadata") or memory.get("extra_metadata")
    else:
        metadata = getattr(memory, "extra_metadata", None)
    return metadata if isinstance(metadata, dict) else {}


def normalize_key(value: object) -> str:
    return str(value or "").strip().lower()


def retrieval_similarity_threshold() -> float:
    settings = get_settings()
    return max(
        settings.memory_recall_threshold_min,
        min(
            settings.memory_recall_threshold_max,
            settings.memory_semantic_threshold * settings.memory_recall_threshold_factor,
        ),
    )


def semantic_similarity_threshold() -> float:
    return get_settings().memory_semantic_threshold
