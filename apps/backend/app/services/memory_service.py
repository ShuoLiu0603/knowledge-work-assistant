from __future__ import annotations

from collections import Counter
from dataclasses import replace
import inspect
import re
import tiktoken
from datetime import datetime, timezone

from fastapi import HTTPException, status as http_status
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models.agent_run import AgentRun
from app.db.models.conversation import Conversation, Message
from app.db.models.user import User
from app.db.models.user_memory import UserMemory, UserMemoryEvent, UserMemoryRecallLog, UserMemoryUpdateJob
from app.llm.provider import MemoryCandidate, MemoryOperation, get_llm_provider
from app.llm.context_compression import compress_memory_context
from app.llm.token_counter import count_tokens
from app.memory import commands as memory_commands
from app.memory import context as memory_contexts
from app.memory import editor as memory_editor
from app.memory import embedding as memory_embedding
from app.memory import events as memory_events
from app.memory import jobs as memory_jobs
from app.memory import policy as memory_policy
from app.memory import reconcile as memory_reconcile
from app.memory import repository as memory_repository
from app.memory import retrieval as memory_retrieval
from app.memory import short_term
from app.memory import vector_index as memory_vector_index
from app.memory.types import MemoryAction, MemoryEmbedding, MemorySource

_SUMMARY_TOKEN_ENCODING = tiktoken.get_encoding("cl100k_base")
from app.services.audit_service import record_audit_event
from app.services.llm_log_service import create_llm_call_log

ALLOWED_MEMORY_STATUSES = memory_policy.ALLOWED_MEMORY_STATUSES
MAX_MEMORY_OPERATIONS = memory_policy.MAX_MEMORY_OPERATIONS
MEMORY_EDITOR_CONTEXT_LIMIT = memory_policy.MEMORY_EDITOR_CONTEXT_LIMIT
MEMORY_EDITOR_CANDIDATE_LIMIT = memory_policy.MEMORY_EDITOR_CANDIDATE_LIMIT
MEMORY_RECALL_CANDIDATE_LIMIT = memory_policy.MEMORY_RECALL_CANDIDATE_LIMIT
MEMORY_SOURCE_MAX_CHARS = memory_policy.MEMORY_SOURCE_MAX_CHARS
SUMMARY_DELTA_MAX_CHARS = memory_policy.SUMMARY_DELTA_MAX_CHARS
FULL_MEMORY_RECALL_LIMIT = memory_policy.FULL_MEMORY_RECALL_LIMIT
STICKY_MEMORY_CATEGORIES = memory_policy.STICKY_MEMORY_CATEGORIES
PROFILE_MEMORY_LIMIT = get_settings().memory_profile_limit
PENDING_MEMORY_LIMIT = get_settings().memory_pending_limit
ALLOWED_MEMORY_UPDATE_JOB_STATUSES = {"queued", "processing", "completed", "failed"}
PURGED_MEMORY_REDACTION_TEXT = "[redacted after memory purge]"
SENSITIVE_MEMORY_CONFIRMATION_REQUIRED = "Sensitive memory content requires explicit confirmation"
CONVERSATION_SUMMARY_SECTION_ORDER = (
    "CURRENT GOAL",
    "ACTIVE CONSTRAINTS AND DECISIONS",
    "ESTABLISHED FACTS AND COMPLETED WORK",
    "IMPORTANT ARTIFACTS",
    "OPEN QUESTIONS OR BLOCKERS",
    "NEXT STEP",
)
CONVERSATION_SUMMARY_SECTION_PRIORITY = (
    "ACTIVE CONSTRAINTS AND DECISIONS",
    "CURRENT GOAL",
    "OPEN QUESTIONS OR BLOCKERS",
    "NEXT STEP",
    "ESTABLISHED FACTS AND COMPLETED WORK",
    "IMPORTANT ARTIFACTS",
)


def get_redis_client():
    return short_term.get_redis_client()


def append_short_term_memory(user_id: str, conversation_id: str | None, role: str, content: str) -> None:
    short_term.append_short_term_memory(user_id, conversation_id, role, content)


def get_short_term_memory(user_id: str, conversation_id: str | None) -> list[dict]:
    return short_term.get_short_term_memory(user_id, conversation_id)


def clear_short_term_memory(user_id: str, conversation_id: str | None) -> bool:
    return short_term.clear_short_term_memory(user_id, conversation_id)


def get_recent_db_messages(db: Session, conversation_id: str | None, limit: int | None = None) -> list[dict]:
    return short_term.get_recent_db_messages(db, conversation_id, limit=limit)


def get_conversation_memory_context_messages(
    db: Session,
    conversation: Conversation,
    *,
    current_input: str,
    current_message_id: str | None = None,
    fallback_messages: list[dict] | None = None,
) -> tuple[list[dict], dict]:
    total = db.scalar(
        select(func.count(Message.id)).where(Message.conversation_id == conversation.id)
    ) or 0
    if total <= 0:
        return filter_memory_history_messages(
            fallback_messages or [],
            current_input=current_input,
            current_message_id=current_message_id,
        )

    processed = min(max(conversation.summary_message_count or 0, 0), total)
    recent_start = max(0, total - get_settings().short_memory_max_messages)
    start = min(processed, recent_start)
    previous_message = None
    if start > 0:
        previous_message = db.scalar(
            select(Message)
            .where(Message.conversation_id == conversation.id)
            .order_by(Message.created_at.asc(), Message.id.asc())
            .offset(start - 1)
            .limit(1)
        )
    rows = db.scalars(
        select(Message)
        .where(Message.conversation_id == conversation.id)
        .order_by(Message.created_at.asc(), Message.id.asc())
        .offset(start)
    ).all()
    messages = [
        {
            "id": message.id,
            "role": message.role,
            "content": message.content,
            "memory_enabled": message.memory_enabled,
            "created_at": message.created_at.isoformat(),
        }
        for message in rows
        if message.content and message.content.strip()
    ]
    return filter_memory_history_messages(
        messages,
        current_input=current_input,
        current_message_id=current_message_id,
        previous_message=previous_message,
    )


def filter_memory_history_messages(
    messages: list[dict],
    *,
    current_input: str,
    current_message_id: str | None = None,
    previous_message: Message | None = None,
) -> tuple[list[dict], dict]:
    filtered: list[dict] = []
    skip_private_assistant = bool(
        previous_message is not None
        and previous_message.role == "user"
        and (
            not previous_message.memory_enabled
            or memory_policy.should_skip_memory_for_turn(previous_message.content)
        )
    )
    private_turn_message_count = 0

    for message in messages:
        role = str(message.get("role") or "")
        content = str(message.get("content") or "")
        memory_enabled = message.get("memory_enabled") is not False
        if role == "user" and (not memory_enabled or should_skip_memory_for_turn(content)):
            skip_private_assistant = True
            private_turn_message_count += 1
            continue
        if role == "assistant" and not memory_enabled:
            skip_private_assistant = False
            private_turn_message_count += 1
            continue
        if skip_private_assistant and role == "assistant":
            skip_private_assistant = False
            private_turn_message_count += 1
            continue
        if role == "user":
            skip_private_assistant = False
        filtered.append(message)

    current_turn_index = None
    if current_message_id:
        current_turn_index = next(
            (
                index
                for index in range(len(filtered) - 1, -1, -1)
                if filtered[index].get("id") == current_message_id
            ),
            None,
        )
    if current_message_id and current_turn_index is None:
        normalized_input = current_input.strip()
        current_turn_index = next(
            (
                index
                for index in range(len(filtered) - 1, -1, -1)
                if filtered[index].get("role") == "user"
                and str(filtered[index].get("content") or "").strip() == normalized_input
            ),
            None,
        )
    if current_turn_index is not None:
        filtered.pop(current_turn_index)

    return filtered, {
        "private_turn_message_count": private_turn_message_count,
        "current_turn_removed": current_turn_index is not None,
    }


def process_user_memory(
    db: Session,
    user_id: str,
    text: str,
    conversation_id: str | None = None,
    assistant_text: str = "",
    message_id: str | None = None,
    *,
    autocommit: bool = True,
    respect_no_memory_marker: bool = True,
) -> list[MemoryAction]:
    if memory_policy.should_ignore_memory_request(text):
        return [MemoryAction("ignore", None, "", "user asked not to remember")]
    if respect_no_memory_marker and memory_policy.should_skip_memory_for_turn(text):
        return [MemoryAction("ignore", None, "", "user requested no memory for this turn")]

    provider = get_llm_provider()
    if not hasattr(provider, "review_memory_operations"):
        return [MemoryAction("ignore", None, "", "memory review is not supported by the configured provider")]

    proposals = extract_memory_candidates_with_logging(db, provider, user_id, text, assistant_text, conversation_id)
    actions: list[MemoryAction] = []
    judge_was_called = False
    try:
        for proposal in proposals[:MAX_MEMORY_OPERATIONS]:
            if proposal.action == "ignore" or not proposal.content.strip():
                actions.append(
                    MemoryAction(
                        "ignore",
                        None,
                        proposal.content,
                        proposal.reason or "memory candidate extractor ignored the turn",
                    )
                )
                continue

            candidate = replace(proposal, action="create", target_memory_id=None, expected_revision=None)
            related_memories = load_related_memories_for_candidate(db, user_id, candidate)
            decision = judge_memory_candidate_with_logging(
                db,
                provider,
                user_id,
                candidate,
                related_memories,
                text,
                assistant_text,
                conversation_id,
            )
            judge_was_called = True
            if decision is None:
                actions.append(
                    MemoryAction(
                        "ignore",
                        None,
                        candidate.content,
                        "mandatory memory judge rejected the candidate or was unavailable",
                    )
                )
                continue

            actions.append(
                process_memory_operation(
                    db,
                    user_id,
                    decision,
                    source=memory_source_from_turn(
                        db,
                        conversation_id,
                        text,
                        evidence=(
                            decision.evidence
                            if memory_policy.is_evidence_grounded(decision.evidence, text)
                            else text
                        ),
                        message_id=message_id,
                    ),
                    conflict_reviewer=None,
                    user_message=text,
                    autocommit=False,
                )
            )

        if autocommit:
            if judge_was_called:
                db.commit()
        else:
            db.flush()
    except Exception:
        db.rollback()
        raise
    return actions or [MemoryAction("ignore", None, "", "no durable memory candidate")]


def extract_memory_candidates_with_logging(
    db: Session,
    provider,
    user_id: str,
    user_message: str,
    assistant_message: str,
    conversation_id: str | None,
) -> list[MemoryOperation]:
    retry_reason = ""
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            review = call_memory_review(
                provider,
                user_message,
                assistant_message,
                empty_memory_editor_context(),
                retry_reason=retry_reason,
            )
        except Exception as exc:
            last_error = exc
            retry_reason = (
                "The previous extractor output violated the candidate schema. Return a candidates array with "
                "verbatim non-empty evidence for every candidate."
            )
            continue
        create_llm_call_log(
            db,
            review.completion,
            user_id=user_id,
            conversation_id=conversation_id,
            agent_name="memory_candidate_extractor" if attempt == 0 else "memory_candidate_extractor_retry",
        )
        return review.operations
    if last_error is not None:
        raise last_error
    return []


def empty_memory_editor_context() -> dict[str, list[dict]]:
    return {
        "existing_memories": [],
        "profile_memories": [],
        "candidate_memories": [],
        "pending_memories": [],
    }


def call_memory_review(
    provider,
    user_message: str,
    assistant_message: str,
    editor_context: dict,
    retry_reason: str = "",
):
    method = provider.review_memory_operations
    parameters = inspect.signature(method).parameters
    accepts_kwargs = any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values())
    kwargs = {
        "user_message": user_message,
        "assistant_message": assistant_message,
    }
    optional_payload = {
        "existing_memories": editor_context["existing_memories"],
        "profile_memories": editor_context["profile_memories"],
        "candidate_memories": editor_context["candidate_memories"],
        "pending_memories": editor_context["pending_memories"],
        "retry_reason": retry_reason,
    }
    for name, value in optional_payload.items():
        if accepts_kwargs or name in parameters:
            kwargs[name] = value
    return method(**kwargs)


def load_related_memories_for_candidate(
    db: Session,
    user_id: str,
    candidate: MemoryOperation,
) -> list[UserMemory]:
    """Build a bounded judge context using deterministic keys and pgvector recall."""
    normalized = memory_policy.normalize_memory_content(candidate.content)
    if not normalized:
        return []

    category = memory_policy.resolve_operation_category(candidate)
    canonical_key = memory_policy.canonical_key_for_operation(candidate, category, normalized)
    exact = memory_repository.find_exact_memory(
        db,
        user_id,
        memory_policy.hash_content(normalized),
        statuses={"active", "pending"},
    )
    conflict_candidates = memory_editor.list_conflict_candidates(db, user_id, category, canonical_key)
    canonical_matches = [
        memory
        for memory in conflict_candidates
        if canonical_key and memory.canonical_key == canonical_key
    ]

    recent_candidates = memory_repository.list_memory_editor_candidates(
        db,
        user_id,
        MEMORY_EDITOR_CANDIDATE_LIMIT,
    )
    recent_candidates = [
        memory
        for memory in recent_candidates
        if (
            not memory_policy.is_profile_memory(memory)
            or memory.category == category
            or (canonical_key and memory.canonical_key == canonical_key)
        )
    ]

    query_vector: list[float] | None = None
    vector_memories: list[UserMemory] = []
    try:
        query_vector = memory_embedding.embed_memory_text(candidate.content).vector
        vector_hits = memory_vector_index.search_active_memories(
            db,
            user_id,
            query_vector,
            limit=MEMORY_EDITOR_CONTEXT_LIMIT,
        )
        vector_memories = memory_repository.list_active_memories_by_ids(
            db,
            user_id,
            [hit.memory_id for hit in vector_hits],
            include_profile=True,
        )
    except Exception:
        query_vector = None
        vector_memories = []

    combined = memory_retrieval.dedupe_memories(
        [
            *([exact] if exact is not None else []),
            *conflict_candidates,
            *vector_memories,
            *recent_candidates,
        ]
    )
    ranked = memory_retrieval.rank_editor_context(
        combined,
        candidate.content,
        embed=(lambda _text: query_vector or []),
    )
    return memory_retrieval.dedupe_memories(
        [
            *([exact] if exact is not None else []),
            *canonical_matches,
            *vector_memories,
            *ranked,
        ]
    )[:MEMORY_EDITOR_CONTEXT_LIMIT]


def judge_memory_candidate_with_logging(
    db: Session,
    provider,
    user_id: str,
    candidate: MemoryOperation,
    related_memories: list[UserMemory],
    user_message: str,
    assistant_message: str,
    conversation_id: str | None,
) -> MemoryOperation | None:
    if not hasattr(provider, "review_memory_conflict_candidates"):
        return None
    retry_reason = ""
    for attempt in range(2):
        try:
            review = call_memory_judge(
                provider,
                candidate,
                related_memories,
                user_message,
                assistant_message,
                retry_reason=retry_reason,
            )
        except Exception:
            retry_reason = "The previous judge call failed. Return one valid structured decision."
            continue

        create_llm_call_log(
            db,
            review.completion,
            user_id=user_id,
            conversation_id=conversation_id,
            agent_name="memory_judge" if attempt == 0 else "memory_judge_retry",
            autocommit=False,
        )
        decision = select_memory_judge_decision(review.operations, candidate, related_memories)
        if decision is not None:
            return decision
        retry_reason = (
            "The previous decision was missing or violated the relation and target-id contract. "
            "Return one valid structured decision using only supplied memory IDs."
        )
    return None


def call_memory_judge(
    provider,
    candidate: MemoryOperation,
    related_memories: list[UserMemory],
    user_message: str,
    assistant_message: str,
    retry_reason: str = "",
):
    method = provider.review_memory_conflict_candidates
    parameters = inspect.signature(method).parameters
    accepts_kwargs = any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values())
    kwargs = {
        "operation": memory_operation_to_context_dict(candidate),
        "conflict_memories": [
            memory_to_context_dict(memory, section="related")
            for memory in related_memories
        ],
    }
    optional_payload = {
        "user_message": user_message,
        "assistant_message": assistant_message,
        "retry_reason": retry_reason,
    }
    for name, value in optional_payload.items():
        if accepts_kwargs or name in parameters:
            kwargs[name] = value
    return method(**kwargs)


def select_memory_judge_decision(
    operations: list[MemoryOperation],
    candidate: MemoryOperation,
    related_memories: list[UserMemory],
) -> MemoryOperation | None:
    allowed_actions = {"create", "update", "supersede", "pending", "ignore"}
    candidate_ids = {memory.id for memory in related_memories}
    candidate_content = memory_policy.normalize_memory_content(candidate.content)
    matching = [
        operation
        for operation in operations[:MAX_MEMORY_OPERATIONS]
        if memory_policy.normalize_memory_content(operation.content) == candidate_content
    ]
    ordered = [*matching, *(operation for operation in operations[:MAX_MEMORY_OPERATIONS] if operation not in matching)]
    for operation in ordered:
        if operation.action not in allowed_actions:
            continue
        if operation.relation in {"equivalent", "refinement", "replacement"} and not operation.target_memory_id:
            continue
        if operation.relation in {"independent", "discard"} and operation.target_memory_id:
            continue
        if operation.action != "ignore" and operation.sensitivity != "low":
            continue
        if operation.target_memory_id and operation.target_memory_id not in candidate_ids:
            continue
        if operation.action in {"update", "supersede"} and operation.target_memory_id not in candidate_ids:
            continue
        if operation.action == "create":
            operation = replace(operation, target_memory_id=None)
        if operation.target_memory_id:
            target_revision = next(
                (
                    memory.revision
                    for memory in related_memories
                    if memory.id == operation.target_memory_id
                ),
                None,
            )
            operation = replace(operation, expected_revision=target_revision)
        return operation
    return None


def process_memory_operation(
    db: Session,
    user_id: str,
    operation: MemoryOperation,
    source: MemorySource,
    conflict_reviewer: memory_editor.ConflictReviewer | None = None,
    user_message: str = "",
    autocommit: bool = True,
) -> MemoryAction:
    return memory_editor.process_memory_operation(
        db,
        user_id,
        operation,
        source,
        conflict_reviewer=conflict_reviewer,
        user_message=user_message,
        autocommit=autocommit,
    )


def memory_operation_to_context_dict(operation: MemoryOperation) -> dict:
    return {
        "action": operation.action,
        "content": operation.content,
        "target_memory_id": operation.target_memory_id,
        "kind": operation.kind,
        "category": operation.category,
        "canonical_key": operation.canonical_key,
        "importance": operation.importance,
        "sensitivity": operation.sensitivity,
        "evidence": operation.evidence,
        "reason": operation.reason,
        "expected_revision": operation.expected_revision,
        "relation": operation.relation,
    }


def create_memory_from_operation(
    db: Session,
    user_id: str,
    operation: MemoryOperation,
    normalized: str,
    source: MemorySource,
    status: str,
) -> UserMemory:
    return memory_editor.create_memory_from_operation(db, user_id, operation, normalized, source, status)


def create_pending_memory_from_operation(
    db: Session,
    user_id: str,
    operation: MemoryOperation,
    normalized: str,
    source: MemorySource,
) -> MemoryAction:
    return memory_editor.create_pending_memory_from_operation(db, user_id, operation, normalized, source)


def can_auto_create(operation: MemoryOperation, user_message: str | None = None) -> bool:
    return memory_policy.can_auto_create(operation, user_message=user_message)


def can_auto_update(operation: MemoryOperation, user_message: str | None = None) -> bool:
    return memory_policy.can_auto_update(operation, user_message=user_message)


def can_auto_supersede(
    operation: MemoryOperation,
    target: UserMemory,
    user_message: str | None = None,
) -> bool:
    return memory_policy.can_auto_supersede(operation, target.status, user_message=user_message)


def is_safe_memory_operation(operation: MemoryOperation, user_message: str | None = None) -> bool:
    return memory_policy.is_safe_memory_operation(operation, user_message=user_message)


def get_operation_target(db: Session, user_id: str, memory_id: str | None) -> UserMemory | None:
    return memory_editor.get_operation_target(db, user_id, memory_id)


def candidate_from_operation(operation: MemoryOperation) -> MemoryCandidate:
    return memory_editor.candidate_from_operation(operation)


def resolve_operation_category(operation: MemoryOperation) -> str:
    return memory_policy.resolve_operation_category(operation)


def memory_operation_metadata(operation: MemoryOperation, decision: str) -> dict:
    return memory_policy.memory_operation_metadata(operation, decision)


def build_memory_editor_context(db: Session, user_id: str, query: str = "") -> dict:
    profile_memories = memory_repository.list_active_profile_memories(db, user_id, limit=PROFILE_MEMORY_LIMIT)
    memories = memory_retrieval.dedupe_memories(
        [
            *profile_memories,
            *memory_repository.list_memory_editor_candidates(db, user_id, MEMORY_EDITOR_CANDIDATE_LIMIT),
        ]
    )
    ranked = rank_memory_editor_context(list(memories), query)
    profile_ids = {memory.id for memory in profile_memories}
    ranked_profiles = [
        memory
        for memory in ranked
        if memory.id in profile_ids or (memory.status == "active" and memory_policy.is_profile_memory(memory))
    ]
    pending_memories = [memory for memory in ranked if memory.status == "pending"]
    candidate_memories = [
        memory
        for memory in ranked
        if not memory_policy.is_profile_memory(memory) and memory.status != "pending"
    ]
    profile_memories = ranked_profiles[:PROFILE_MEMORY_LIMIT]
    pending_memories = pending_memories[:PENDING_MEMORY_LIMIT]
    candidate_memories = candidate_memories[:MEMORY_EDITOR_CONTEXT_LIMIT]
    candidate_limit = max(0, MEMORY_EDITOR_CONTEXT_LIMIT - len(profile_memories) - len(pending_memories))
    existing_memories = memory_retrieval.dedupe_memories(
        [*profile_memories, *pending_memories, *candidate_memories[:candidate_limit]]
    )[:MEMORY_EDITOR_CONTEXT_LIMIT]
    return {
        "profile_memories": [memory_to_context_dict(memory, section="profile") for memory in profile_memories],
        "candidate_memories": [memory_to_context_dict(memory, section="candidate") for memory in candidate_memories],
        "pending_memories": [memory_to_context_dict(memory, section="pending") for memory in pending_memories],
        "existing_memories": [memory_to_context_dict(memory, section="existing") for memory in existing_memories],
    }


def list_memory_editor_context(db: Session, user_id: str, query: str = "") -> list[dict]:
    return build_memory_editor_context(db, user_id, query)["existing_memories"]


def memory_to_context_dict(memory: UserMemory, section: str = "") -> dict:
    return {
        "id": memory.id,
        "status": memory.status,
        "kind": memory.kind,
        "category": memory.category,
        "canonical_key": memory.canonical_key,
        "memory_layer": memory.memory_layer,
        "profile_slot": memory.profile_slot,
        "scope_type": memory.scope_type,
        "scope_id": memory.scope_id,
        "pinned": memory.pinned,
        "revision": memory.revision,
        "section": section,
        "content": memory.content,
    }


def rank_memory_editor_context(memories: list[UserMemory], query: str) -> list[UserMemory]:
    return memory_retrieval.rank_editor_context(
        memories,
        query,
        embed=lambda text: embed_memory_text(text).vector,
    )


def memory_source_from_turn(
    db: Session,
    conversation_id: str | None,
    user_message: str,
    evidence: str = "",
    message_id: str | None = None,
) -> MemorySource:
    source_text = sanitize_memory_source(evidence or user_message)
    return MemorySource(
        text=source_text,
        conversation_id=conversation_id,
        message_id=message_id or latest_user_message_id(db, conversation_id, user_message),
    )


def latest_user_message_id(db: Session, conversation_id: str | None, user_message: str) -> str | None:
    if not conversation_id:
        return None
    message = db.scalar(
        select(Message)
        .where(
            Message.conversation_id == conversation_id,
            Message.role == "user",
            Message.content == user_message,
        )
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(1)
    )
    return message.id if message else None


def sanitize_memory_source(text: str) -> str:
    normalized = " ".join(text.strip().split())
    if len(normalized) <= MEMORY_SOURCE_MAX_CHARS:
        return normalized
    return normalized[: MEMORY_SOURCE_MAX_CHARS - 3].rstrip() + "..."


def process_memory_candidate(
    db: Session,
    user_id: str,
    candidate: MemoryCandidate,
    source: MemorySource,
) -> MemoryAction:
    return memory_editor.process_memory_candidate(db, user_id, candidate, source)


def upsert_memory_candidate(db: Session, user_id: str, content: str | MemoryCandidate, source: MemorySource) -> MemoryAction:
    return memory_editor.upsert_memory_candidate(db, user_id, content, source)


def retrieve_relevant_memories(
    db: Session,
    user_id: str,
    query: str,
    limit: int | None = None,
    conversation_id: str | None = None,
    message_id: str | None = None,
    include_profile: bool = True,
) -> list[UserMemory]:
    if limit is None:
        limit = get_settings().memory_semantic_limit
    active_count = memory_repository.count_active_memories(db, user_id, include_profile=include_profile)
    profile_memories = list_profile_memories(db, user_id) if include_profile else []
    recall_limit = max(limit, FULL_MEMORY_RECALL_LIMIT) if is_full_memory_recall_query(query) else limit
    recent_candidates = memory_repository.list_recent_active_memories(
        db,
        user_id,
        limit=max(limit, FULL_MEMORY_RECALL_LIMIT, MEMORY_RECALL_CANDIDATE_LIMIT),
        include_profile=False,
    )
    result = None

    if is_full_memory_recall_query(query):
        active = memory_retrieval.dedupe_memories(
            [
                *profile_memories,
                *memory_repository.list_recent_active_memories(
                    db,
                    user_id,
                    limit=recall_limit,
                    include_profile=False,
                ),
            ]
        )
        result = memory_retrieval.retrieve_relevant_memories_with_metadata(
            active,
            query,
            limit,
            embed=lambda text: embed_memory_text(text).vector,
            active_count=active_count,
        )
    elif active_count:
        try:
            query_vector = embed_memory_text(query).vector
            vector_hits = memory_vector_index.search_active_memories(
                db,
                user_id,
                query_vector,
                limit=max(limit, FULL_MEMORY_RECALL_LIMIT),
            )
            if vector_hits:
                hit_memories = memory_repository.list_active_memories_by_ids(
                    db,
                    user_id,
                    [hit.memory_id for hit in vector_hits],
                    include_profile=include_profile,
                )
                vector_result = memory_retrieval.retrieve_relevant_memories_with_vector_hits(
                    memory_retrieval.dedupe_memories([*profile_memories, *hit_memories]),
                    query,
                    limit,
                    vector_hits,
                    active_count=active_count,
                )
                semantic_result = memory_retrieval.retrieve_relevant_memories_with_metadata(
                    memory_retrieval.dedupe_memories([*profile_memories, *hit_memories, *recent_candidates]),
                    query,
                    limit,
                    embed=lambda _text: query_vector,
                    active_count=active_count,
                )
                result = merge_memory_recall_results(vector_result, semantic_result)
            else:
                result = memory_retrieval.retrieve_relevant_memories_with_metadata(
                    memory_retrieval.dedupe_memories([*profile_memories, *recent_candidates]),
                    query,
                    limit,
                    embed=lambda _text: query_vector,
                    active_count=active_count,
                )
        except Exception:
            result = None
    if result is None:
        active = memory_retrieval.dedupe_memories(
            [
                *profile_memories,
                *recent_candidates,
            ]
        )
        result = memory_retrieval.retrieve_relevant_memories_with_metadata(
            active,
            query,
            limit,
            embed=lambda text: embed_memory_text(text).vector,
            active_count=active_count,
        )
    try:
        memory_repository.create_recall_log(
            db,
            user_id=user_id,
            conversation_id=conversation_id,
            message_id=message_id,
            query=query,
            recall_mode=result.recall_mode,
            requested_limit=result.requested_limit,
            recall_limit=result.recall_limit,
            active_count=result.active_count,
            selected_count=len(result.selected),
            threshold=result.threshold,
            candidates=[memory_retrieval.recall_candidate_to_dict(candidate) for candidate in result.candidates],
            selected_memory_ids=[memory.id for memory in result.selected],
        )
    except Exception:
        db.rollback()
    return result.selected


def merge_memory_recall_results(
    vector_result: memory_retrieval.MemoryRecallResult,
    semantic_result: memory_retrieval.MemoryRecallResult,
) -> memory_retrieval.MemoryRecallResult:
    recall_limit = max(vector_result.recall_limit, semantic_result.recall_limit)
    selected = memory_retrieval.dedupe_memories([*vector_result.selected, *semantic_result.selected])[:recall_limit]
    selected_ids = {memory.id for memory in selected}
    candidates_by_id: dict[str, memory_retrieval.MemoryRecallCandidate] = {}
    for candidate in [*vector_result.candidates, *semantic_result.candidates]:
        memory_id = candidate.memory.id
        if memory_id not in candidates_by_id or candidate.route == "vector":
            candidates_by_id[memory_id] = candidate
    candidates = [
        memory_retrieval.MemoryRecallCandidate(
            memory=candidate.memory,
            route=candidate.route,
            score=candidate.score,
            selected=candidate.memory.id in selected_ids,
        )
        for candidate in candidates_by_id.values()
    ]
    return memory_retrieval.MemoryRecallResult(
        selected=selected,
        candidates=candidates,
        recall_mode="hybrid",
        requested_limit=semantic_result.requested_limit,
        recall_limit=recall_limit,
        active_count=max(vector_result.active_count, semantic_result.active_count),
        threshold=None,
        embedding_error=semantic_result.embedding_error or vector_result.embedding_error,
    )


def list_profile_memories(db: Session, user_id: str, limit: int = PROFILE_MEMORY_LIMIT) -> list[UserMemory]:
    return memory_repository.list_active_profile_memories(db, user_id, limit=limit)


def list_core_profile_context(db: Session, user_id: str, limit: int = PROFILE_MEMORY_LIMIT) -> list[dict]:
    profiles = [
        memory_to_context_dict(memory, section="profile")
        for memory in list_profile_memories(db, user_id, limit=limit)
    ]
    if any(memory.get("category") == "name" for memory in profiles):
        return profiles
    user = db.get(User, user_id)
    if user is None or not user.username.strip():
        return profiles
    return [*profiles, account_name_memory(user)]


def account_name_memory(user: User) -> dict:
    return {
        "id": "account:username",
        "content": f"User account name: {user.username.strip()}",
        "category": "name",
        "kind": "profile",
        "status": "active",
        "canonical_key": "profile:name",
        "memory_layer": "profile",
        "profile_slot": "name",
        "scope_type": "user",
        "scope_id": user.id,
        "pinned": True,
        "revision": 1,
        "section": "profile",
        "metadata": {"source": "user_account"},
    }


def is_memory_recall_query(query: str) -> bool:
    return memory_policy.is_memory_recall_query(query)


def is_full_memory_recall_query(query: str) -> bool:
    return memory_policy.is_full_memory_recall_query(query)


def should_skip_memory_for_turn(text: str) -> bool:
    return memory_policy.should_skip_memory_for_turn(text)


def build_memory_context_for_question(
    db: Session,
    user_id: str,
    query: str,
    conversation_id: str | None = None,
    preloaded_short_memory: list[dict] | None = None,
    preloaded_long_memories: list[dict] | None = None,
    preloaded_memory_batches: list[list[str]] | None = None,
    preloaded_profile_memories: list[dict] | None = None,
    conversation_summary: str | None = None,
) -> str:
    if should_skip_memory_for_turn(query):
        return format_memory_context([], [], None, profile_memories=[])

    conversation = db.get(Conversation, conversation_id) if conversation_id else None
    short_memory = preloaded_short_memory
    if short_memory is None and conversation is not None:
        short_memory, _metadata = get_conversation_memory_context_messages(
            db,
            conversation,
            current_input=query,
        )
    elif short_memory is None:
        short_memory = get_short_term_memory(user_id, conversation_id)
        if not short_memory and conversation_id:
            short_memory = get_recent_db_messages(db, conversation_id)

    profile_memories = preloaded_profile_memories
    if preloaded_long_memories is not None and profile_memories is None:
        profile_memories, preloaded_long_memories = memory_contexts.split_profile_memories(preloaded_long_memories)

    if profile_memories is None:
        profile_memories = list_core_profile_context(db, user_id)

    long_memories = preloaded_long_memories or []

    summary = conversation_summary
    if summary is None and conversation:
        summary = conversation.summary

    max_long_memories = (
        FULL_MEMORY_RECALL_LIMIT
        if is_full_memory_recall_query(query)
        else get_settings().memory_context_max_long_memories
    )
    if preloaded_memory_batches:
        long_memories = memory_contexts.select_memories_by_batches(
            long_memories,
            preloaded_memory_batches,
            max_long_memories,
        )
    settings = get_settings()
    sources = memory_contexts.build_memory_compression_sources(
        long_memories,
        short_memory,
        summary,
        profile_memories,
        max_long_memories,
    )
    raw_context = memory_contexts.render_memory_sources(sources)
    if count_tokens(raw_context) <= settings.memory_context_max_tokens:
        return raw_context

    compression = compress_memory_context(
        query,
        sources,
        settings.memory_context_max_tokens,
    )
    for completion in compression.completions:
        create_llm_call_log(
            db,
            completion,
            user_id=user_id,
            conversation_id=conversation_id,
            agent_name="memory_context_compression",
        )
    if compression.content:
        return compression.content

    return format_memory_context(
        long_memories,
        short_memory,
        summary,
        max_long_memories=max_long_memories,
        profile_memories=profile_memories,
    )


def format_memory_context(
    long_memories: list[dict],
    short_memory: list[dict],
    conversation_summary: str | None,
    max_long_memories: int | None = None,
    max_chars: int | None = None,
    max_tokens: int | None = None,
    profile_memories: list[dict] | None = None,
) -> str:
    settings = get_settings()
    if max_long_memories is None:
        max_long_memories = settings.memory_context_max_long_memories
    effective_max_tokens = max_tokens
    if effective_max_tokens is None and max_chars is None:
        effective_max_tokens = settings.memory_context_max_tokens
    return memory_contexts.format_memory_context(
        long_memories,
        short_memory,
        conversation_summary,
        max_long_memories=max_long_memories,
        max_chars=max_chars or settings.memory_context_max_chars,
        max_tokens=effective_max_tokens,
        model_name=settings.llm_model,
        profile_memories=profile_memories,
    )


def update_conversation_summary(
    db: Session,
    conversation: Conversation,
    user_message: str,
    assistant_message: str,
    user_id: str | None = None,
) -> str:
    initial_cursor = max(conversation.summary_message_count or 0, 0)
    messages, message_count, previous_message, deferred_trailing_user = get_unprocessed_summary_messages(
        db,
        conversation,
    )
    if deferred_trailing_user and not messages:
        return conversation.summary or ""
    summary_messages = exclude_memory_opt_out_turns(messages, previous_message=previous_message)
    if messages and not summary_messages:
        return commit_conversation_summary(
            db,
            conversation,
            summary=conversation.summary or "",
            message_count=message_count,
            expected_cursor=initial_cursor,
        )
    batches = build_conversation_summary_delta_batches(
        summary_messages,
        fallback_user_message=user_message,
        fallback_assistant_message=assistant_message,
    )
    provider = get_llm_provider()
    summary = conversation.summary or ""
    for delta in batches:
        if hasattr(provider, "summarize_with_metadata"):
            completion = call_conversation_summary_update(provider, summary, delta)
            create_llm_call_log(
                db,
                completion,
                user_id=user_id,
                conversation_id=conversation.id,
                agent_name="conversation_summary",
                autocommit=False,
            )
            summary = completion.content.strip()
        else:
            text = build_conversation_summary_prompt_from_delta(summary, delta)
            summary = provider.summarize(text).strip()
        if not summary:
            raise RuntimeError("Conversation summary provider returned an empty summary")
        if hasattr(provider, "summarize_with_metadata"):
            for _retry in range(get_settings().context_compression_retry_limit):
                actual_tokens = count_tokens(summary)
                if actual_tokens <= get_settings().conversation_summary_max_tokens:
                    break
                completion = call_conversation_summary_compaction(provider, summary)
                create_llm_call_log(
                    db,
                    completion,
                    user_id=user_id,
                    conversation_id=conversation.id,
                    agent_name="conversation_summary_compression_retry",
                    autocommit=False,
                )
                summary = completion.content.strip()
                if not summary:
                    raise RuntimeError("Conversation summary compression returned an empty summary")
        summary = trim_conversation_summary_tokens(summary, get_settings().conversation_summary_max_tokens)

    return commit_conversation_summary(
        db,
        conversation,
        summary=summary,
        message_count=message_count,
        expected_cursor=initial_cursor,
    )


def commit_conversation_summary(
    db: Session,
    conversation: Conversation,
    *,
    summary: str,
    message_count: int,
    expected_cursor: int,
) -> str:
    result = db.execute(
        update(Conversation)
        .where(
            Conversation.id == conversation.id,
            Conversation.summary_message_count == expected_cursor,
        )
        .values(
            summary=trim_conversation_summary_tokens(summary, get_settings().conversation_summary_max_tokens),
            summary_message_count=message_count,
            updated_at=datetime.now(timezone.utc),
        )
        .execution_options(synchronize_session=False)
    )
    db.commit()
    db.expire_all()
    current = db.get(Conversation, conversation.id)
    if current is None:
        return ""
    return current.summary or ""


def call_conversation_summary_update(provider, existing_summary: str, new_messages: str):
    method = getattr(provider, "update_conversation_summary_with_metadata", None)
    if callable(method):
        return method(existing_summary, new_messages)
    return provider.summarize_with_metadata(
        build_conversation_summary_prompt_from_delta(existing_summary, new_messages)
    )


def call_conversation_summary_compaction(provider, summary: str):
    method = getattr(provider, "compact_conversation_summary_with_metadata", None)
    if callable(method):
        return method(summary)
    return provider.summarize_with_metadata(build_conversation_summary_compaction_prompt(summary))


def trim_conversation_summary_tokens(summary: str, max_tokens: int) -> str:
    text = summary.strip()
    if max_tokens <= 0:
        return ""
    if count_tokens(text) <= max_tokens:
        return text

    sections = parse_conversation_summary_sections(text)
    if sections:
        selected = {section: [] for section in CONVERSATION_SUMMARY_SECTION_ORDER}
        section_units = {
            section: split_conversation_summary_units("\n".join(sections.get(section, [])))
            for section in CONVERSATION_SUMMARY_SECTION_ORDER
        }

        # Preserve one complete item from every section before spending budget on extra detail.
        for section in CONVERSATION_SUMMARY_SECTION_PRIORITY:
            if section_units[section]:
                try_add_conversation_summary_unit(selected, section, section_units[section][0], max_tokens)

        for section in CONVERSATION_SUMMARY_SECTION_PRIORITY:
            for unit in section_units[section][1:]:
                try_add_conversation_summary_unit(selected, section, unit, max_tokens)

        rendered = render_conversation_summary_sections(selected)
        if rendered:
            return rendered

    units = split_conversation_summary_units(text)
    selected_units: list[str] = []
    for unit in units:
        candidate = " ".join([*selected_units, unit])
        if count_tokens(candidate) > max_tokens:
            if selected_units:
                break
            continue
        selected_units.append(unit)
    if selected_units:
        return " ".join(selected_units)
    return "None" if count_tokens("None") <= max_tokens else ""


def parse_conversation_summary_sections(summary: str) -> dict[str, list[str]]:
    sections = {section: [] for section in CONVERSATION_SUMMARY_SECTION_ORDER}
    current_section: str | None = None
    found_known_section = False
    for line in summary.splitlines():
        heading = re.match(r"^##\s+(.+?)\s*$", line.strip())
        if heading:
            normalized = re.sub(r"\s+", " ", heading.group(1)).strip().upper()
            current_section = normalized if normalized in sections else None
            found_known_section = found_known_section or current_section is not None
            continue
        if current_section is not None and line.strip():
            sections[current_section].append(line.strip())
    return sections if found_known_section else {}


def split_conversation_summary_units(text: str) -> list[str]:
    units: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.casefold() == "none":
            continue
        if re.match(r"^(?:[-*+]\s+|\d+[.)]\s+)", stripped):
            units.append(stripped)
            continue
        units.extend(
            part.strip()
            for part in re.split(r"(?<=[。！？])\s*|(?<=[.!?])\s+", stripped)
            if part.strip()
        )
    return units


def try_add_conversation_summary_unit(
    selected: dict[str, list[str]],
    section: str,
    unit: str,
    max_tokens: int,
) -> bool:
    selected[section].append(unit)
    if count_tokens(render_conversation_summary_sections(selected)) <= max_tokens:
        return True
    selected[section].pop()
    return False


def render_conversation_summary_sections(sections: dict[str, list[str]]) -> str:
    rendered: list[str] = []
    for section in CONVERSATION_SUMMARY_SECTION_ORDER:
        units = sections.get(section) or []
        if units:
            rendered.append(f"## {section}\n" + "\n".join(units))
    return "\n\n".join(rendered)


def should_update_conversation_summary(db: Session, conversation_id: str) -> bool:
    """Return True when unprocessed messages should trigger a summary update.

    Triggers when any of these conditions are met:
      1. Unprocessed tokens reach conversation_summary_trigger_tokens.
      2. Unprocessed tokens and message count both reach their minimum thresholds.
      3. Unprocessed message count reaches conversation_summary_max_unprocessed.
    """
    conversation = db.get(Conversation, conversation_id)
    if conversation is None:
        return False

    settings = get_settings()
    total = db.scalar(
        select(func.count(Message.id)).where(Message.conversation_id == conversation_id)
    ) or 0
    processed = max(conversation.summary_message_count or 0, 0)
    remaining = max(0, total - processed)

    if remaining == 0:
        return False

    # Safety valve: too many unprocessed messages regardless of token count
    if remaining >= settings.conversation_summary_max_unprocessed:
        return True

    contents = db.scalars(
        select(Message.content)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc(), Message.id.asc())
        .offset(processed)
        .limit(remaining)
    ).all()

    token_count = sum(
        len(_SUMMARY_TOKEN_ENCODING.encode(content))
        for content in contents
        if content and content.strip()
    )

    if token_count >= settings.conversation_summary_trigger_tokens:
        return True

    if (
        token_count >= settings.conversation_summary_min_tokens
        and remaining >= settings.conversation_summary_min_messages
    ):
        return True

    return False


def get_unprocessed_summary_messages(
    db: Session,
    conversation: Conversation,
) -> tuple[list[Message], int, Message | None, bool]:
    message_count = db.scalar(select(func.count(Message.id)).where(Message.conversation_id == conversation.id)) or 0
    processed_count = min(max(conversation.summary_message_count or 0, 0), message_count)
    previous_message = None
    if processed_count > 0:
        previous_message = db.scalar(
            select(Message)
            .where(Message.conversation_id == conversation.id)
            .order_by(Message.created_at.asc(), Message.id.asc())
            .offset(processed_count - 1)
            .limit(1)
        )
    messages = db.scalars(
        select(Message)
        .where(Message.conversation_id == conversation.id)
        .order_by(Message.created_at.asc(), Message.id.asc())
        .offset(processed_count)
    ).all()
    messages = list(messages)
    deferred_trailing_user = bool(messages and messages[-1].role == "user")
    if deferred_trailing_user:
        messages = messages[:-1]
    processable_count = processed_count + len(messages)
    return messages, processable_count, previous_message, deferred_trailing_user


def exclude_memory_opt_out_turns(
    messages: list[Message],
    *,
    previous_message: Message | None = None,
) -> list[Message]:
    included: list[Message] = []
    skip_assistant = bool(
        previous_message is not None
        and previous_message.role == "user"
        and (
            not previous_message.memory_enabled
            or memory_policy.should_skip_memory_for_turn(previous_message.content)
        )
    )
    for message in messages:
        if not message.memory_enabled:
            if message.role == "user":
                skip_assistant = True
            elif message.role == "assistant":
                skip_assistant = False
            continue
        if message.role == "user":
            if memory_policy.should_skip_memory_for_turn(message.content):
                skip_assistant = True
                continue
            skip_assistant = False
            included.append(message)
            continue
        if message.role == "assistant" and skip_assistant:
            skip_assistant = False
            continue
        included.append(message)
    return included


def build_conversation_summary_prompt(
    previous_summary: str,
    messages: list[Message],
    fallback_user_message: str,
    fallback_assistant_message: str,
) -> str:
    batches = build_conversation_summary_delta_batches(
        messages,
        fallback_user_message=fallback_user_message,
        fallback_assistant_message=fallback_assistant_message,
    )
    delta = batches[0]
    return build_conversation_summary_prompt_from_delta(previous_summary, delta)


def build_conversation_summary_prompt_from_delta(previous_summary: str, delta: str) -> str:
    return f"Existing summary:\n{previous_summary or 'None'}\n\nNew messages since previous summary:\n{delta}"


def build_conversation_summary_compaction_prompt(summary: str) -> str:
    return (
        "Rewrite this working-state summary more compactly. Preserve active user constraints, the current goal, "
        "corrections, open questions, blockers, the next step, completed work, and important artifacts. Remove "
        "repetition, background explanation, obsolete details, and abandoned alternatives. Keep every retained item "
        f"complete.\n\nSummary to compact:\n{summary}"
    )


def build_conversation_summary_delta_batches(
    messages: list[Message],
    *,
    fallback_user_message: str,
    fallback_assistant_message: str,
    max_chars: int | None = None,
) -> list[str]:
    rows = (
        [(message.role, message.content) for message in messages]
        if messages
        else [("user", fallback_user_message), ("assistant", fallback_assistant_message)]
    )
    max_chars = max(1, max_chars or SUMMARY_DELTA_MAX_CHARS)
    batches: list[str] = []
    current: list[str] = []
    current_chars = 0

    for role, content in rows:
        prefix = f"{role}: "
        continuation_prefix = f"{role} (continued): "
        remaining = content or ""
        first_chunk = True
        while remaining or first_chunk:
            row_prefix = prefix if first_chunk else continuation_prefix
            chunk_limit = max(1, max_chars - len(row_prefix))
            chunk = remaining[:chunk_limit]
            remaining = remaining[chunk_limit:]
            row = f"{row_prefix}{chunk}"
            separator_chars = 1 if current else 0
            if current and current_chars + separator_chars + len(row) > max_chars:
                batches.append("\n".join(current))
                current = []
                current_chars = 0
                separator_chars = 0
            current.append(row)
            current_chars += separator_chars + len(row)
            first_chunk = False

    if current:
        batches.append("\n".join(current))
    return batches or ["user: \nassistant: "]


def list_user_memories(db: Session, user_id: str, status: str | None = None) -> list[UserMemory]:
    if status:
        status = validate_memory_status(status)
    return memory_repository.list_user_memories(db, user_id, status=status)


def export_user_memory_data(db: Session, user_id: str) -> dict:
    return {
        "user_id": user_id,
        "exported_at": datetime.now(timezone.utc),
        "memories": db.scalars(
            select(UserMemory)
            .where(UserMemory.user_id == user_id)
            .order_by(UserMemory.created_at.asc(), UserMemory.id.asc())
        ).all(),
        "events": db.scalars(
            select(UserMemoryEvent)
            .where(UserMemoryEvent.user_id == user_id)
            .order_by(UserMemoryEvent.created_at.asc(), UserMemoryEvent.id.asc())
        ).all(),
        "recall_logs": db.scalars(
            select(UserMemoryRecallLog)
            .where(UserMemoryRecallLog.user_id == user_id)
            .order_by(UserMemoryRecallLog.created_at.asc(), UserMemoryRecallLog.id.asc())
        ).all(),
        "update_jobs": db.scalars(
            select(UserMemoryUpdateJob)
            .where(UserMemoryUpdateJob.user_id == user_id)
            .order_by(UserMemoryUpdateJob.created_at.asc(), UserMemoryUpdateJob.id.asc())
        ).all(),
    }


def get_user_memory_recall_metrics(db: Session, user_id: str) -> dict:
    logs = db.scalars(
        select(UserMemoryRecallLog)
        .where(UserMemoryRecallLog.user_id == user_id)
        .order_by(UserMemoryRecallLog.created_at.asc(), UserMemoryRecallLog.id.asc())
    ).all()
    recall_mode_counts: Counter[str] = Counter()
    route_counts: Counter[str] = Counter()
    route_selected_counts: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()
    memory_layer_counts: Counter[str] = Counter()
    profile_slot_counts: Counter[str] = Counter()
    selected_memory_counts: Counter[str] = Counter()
    selected_total = 0
    active_total = 0
    empty_result_count = 0
    fallback_count = 0
    vector_count = 0
    below_threshold_candidate_count = 0
    top_scores: list[float] = []

    for log in logs:
        recall_mode_counts[log.recall_mode] += 1
        selected_total += log.selected_count or 0
        active_total += log.active_count or 0
        if not log.selected_count:
            empty_result_count += 1
        if log.recall_mode == "fallback_no_embedding":
            fallback_count += 1
        if log.recall_mode == "vector":
            vector_count += 1
        selected_memory_counts.update(log.selected_memory_ids or [])

        scored_candidates: list[float] = []
        for candidate in log.candidates or []:
            route = str(candidate.get("route") or "unknown")
            route_counts[route] += 1
            if candidate.get("selected"):
                route_selected_counts[route] += 1
            category = str(candidate.get("category") or "unknown")
            category_counts[category] += 1
            memory_layer = str(candidate.get("memory_layer") or "unknown")
            memory_layer_counts[memory_layer] += 1
            profile_slot = str(candidate.get("profile_slot") or "")
            if profile_slot:
                profile_slot_counts[profile_slot] += 1
            if route == "below_threshold":
                below_threshold_candidate_count += 1
            score = candidate.get("score")
            if isinstance(score, int | float):
                scored_candidates.append(float(score))
        if scored_candidates:
            top_scores.append(max(scored_candidates))

    total_logs = len(logs)
    return {
        "user_id": user_id,
        "total_logs": total_logs,
        "recall_mode_counts": dict(sorted(recall_mode_counts.items())),
        "route_counts": dict(sorted(route_counts.items())),
        "route_selected_counts": dict(sorted(route_selected_counts.items())),
        "category_counts": dict(sorted(category_counts.items())),
        "memory_layer_counts": dict(sorted(memory_layer_counts.items())),
        "profile_slot_counts": dict(sorted(profile_slot_counts.items())),
        "empty_result_count": empty_result_count,
        "empty_result_rate": ratio(empty_result_count, total_logs),
        "fallback_count": fallback_count,
        "vector_count": vector_count,
        "below_threshold_candidate_count": below_threshold_candidate_count,
        "average_selected_count": ratio(selected_total, total_logs),
        "average_active_count": ratio(active_total, total_logs),
        "average_top_score": round(sum(top_scores) / len(top_scores), 6) if top_scores else None,
        "unique_selected_memory_count": len(selected_memory_counts),
        "top_selected_memories": [
            {"memory_id": memory_id, "count": count}
            for memory_id, count in selected_memory_counts.most_common(10)
        ],
    }


def reconcile_user_memories(db: Session, user_id: str, apply: bool = False, llm_review: bool = False) -> dict:
    report = memory_reconcile.reconcile_user_memories(
        db,
        user_id,
        apply=apply,
        include_semantic_candidates=llm_review,
    )
    findings = [reconcile_finding_to_dict(finding) for finding in report.findings]
    llm_findings = review_reconcile_findings_with_llm(db, user_id, report.findings, apply=apply) if llm_review else []
    findings.extend(llm_findings)
    return {
        "user_id": report.user_id,
        "apply": report.apply,
        "scanned_count": report.scanned_count,
        "applied_count": report.applied_count + sum(1 for finding in llm_findings if finding["applied"]),
        "findings": findings,
    }


def reconcile_finding_to_dict(finding: memory_reconcile.MemoryReconcileFinding) -> dict:
    return {
        "finding_type": finding.finding_type,
        "severity": finding.severity,
        "memory_id": finding.memory_id,
        "related_memory_id": finding.related_memory_id,
        "proposed_action": finding.proposed_action,
        "reason": finding.reason,
        "applied": finding.applied,
        "metadata": finding.metadata,
    }


def review_reconcile_findings_with_llm(
    db: Session,
    user_id: str,
    findings: list[memory_reconcile.MemoryReconcileFinding],
    *,
    apply: bool,
) -> list[dict]:
    reviewable = [
        finding
        for finding in findings
        if finding.finding_type == "semantic_relation_candidate"
    ]
    if not reviewable:
        return []
    provider = get_llm_provider()
    if not hasattr(provider, "review_memory_reconcile_findings"):
        return []

    memory_rows = list_reconcile_finding_memories(db, user_id, reviewable)
    review = provider.review_memory_reconcile_findings(
        findings=[reconcile_finding_to_dict(finding) for finding in reviewable],
        memories=[memory_to_context_dict(memory, section="reconcile") for memory in memory_rows],
    )
    create_llm_call_log(
        db,
        review.completion,
        user_id=user_id,
        conversation_id=None,
        agent_name="memory_reconcile",
    )

    output: list[dict] = []
    fallback_memory_id = reviewable[0].memory_id
    for operation in review.operations[:MAX_MEMORY_OPERATIONS]:
        if operation.action != "pending" or operation.sensitivity != "low":
            continue
        normalized = normalize_memory_content(operation.content)
        if not normalized:
            continue
        existing = find_exact_memory(db, user_id, hash_content(normalized), statuses={"active", "pending"})
        if existing is not None:
            output.append(
                {
                    "finding_type": "llm_reconcile_suggestion",
                    "severity": "low",
                    "memory_id": existing.id,
                    "related_memory_id": operation.target_memory_id,
                    "proposed_action": "pending",
                    "reason": operation.reason or "LLM reconcile suggestion already exists",
                    "applied": False,
                    "metadata": {
                        "content": operation.content,
                        "canonical_key": operation.canonical_key,
                        "existing_status": existing.status,
                    },
                }
            )
            continue
        applied = False
        memory_id = operation.target_memory_id or fallback_memory_id
        if apply:
            action = create_pending_memory_from_operation(
                db,
                user_id,
                operation,
                normalized,
                MemorySource(text=sanitize_memory_source(operation.evidence or operation.reason or "memory reconcile review")),
            )
            applied = action.memory_id is not None and action.action == "pending"
            memory_id = action.memory_id or memory_id
        output.append(
            {
                "finding_type": "llm_reconcile_suggestion",
                "severity": "low",
                "memory_id": memory_id or fallback_memory_id,
                "related_memory_id": operation.target_memory_id,
                "proposed_action": "pending",
                "reason": operation.reason or "LLM suggested a pending memory repair",
                "applied": applied,
                "metadata": {
                    "content": operation.content,
                    "canonical_key": operation.canonical_key,
                    "kind": operation.kind,
                    "category": operation.category,
                },
            }
        )
    return output


def list_reconcile_finding_memories(
    db: Session,
    user_id: str,
    findings: list[memory_reconcile.MemoryReconcileFinding],
) -> list[UserMemory]:
    memory_ids: list[str] = []
    for finding in findings:
        memory_ids.append(finding.memory_id)
        if finding.related_memory_id:
            memory_ids.append(finding.related_memory_id)
    memories = [
        memory
        for memory_id in dict.fromkeys(memory_ids)
        if (memory := memory_repository.get_user_memory(db, user_id, memory_id)) is not None
    ]
    return memories


def ratio(numerator: int | float, denominator: int | float) -> float:
    if not denominator:
        return 0.0
    return round(float(numerator) / float(denominator), 6)


def list_user_memory_update_jobs(
    db: Session,
    user_id: str,
    status: str | None = None,
    *,
    limit: int = 50,
    offset: int = 0,
) -> list[UserMemoryUpdateJob]:
    if status is not None:
        status = validate_memory_update_job_status(status)
    query = select(UserMemoryUpdateJob).where(UserMemoryUpdateJob.user_id == user_id)
    if status:
        query = query.where(UserMemoryUpdateJob.status == status)
    return db.scalars(
        query.order_by(UserMemoryUpdateJob.created_at.desc(), UserMemoryUpdateJob.id.desc())
        .offset(max(offset, 0))
        .limit(max(1, min(limit, 200)))
    ).all()


def retry_user_memory_update_job(db: Session, user_id: str, job_id: str) -> UserMemoryUpdateJob:
    job = get_user_memory_update_job_or_404(db, user_id, job_id)
    now = datetime.now(timezone.utc)
    newer_job_id = db.scalar(
        select(UserMemoryUpdateJob.id)
        .where(
            UserMemoryUpdateJob.user_id == user_id,
            or_(
                UserMemoryUpdateJob.created_at > job.created_at,
                and_(
                    UserMemoryUpdateJob.created_at == job.created_at,
                    UserMemoryUpdateJob.id > job.id,
                ),
            ),
        )
        .order_by(UserMemoryUpdateJob.created_at.asc(), UserMemoryUpdateJob.id.asc())
        .limit(1)
    )
    if newer_job_id:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail="Memory update job is older than a newer user job and cannot be replayed safely",
        )
    retryable = or_(
        UserMemoryUpdateJob.status == "failed",
        and_(
            UserMemoryUpdateJob.status == "queued",
            or_(
                UserMemoryUpdateJob.dispatched_at.is_(None),
                UserMemoryUpdateJob.error_message.like("worker dispatch failed:%"),
            ),
        ),
        and_(
            UserMemoryUpdateJob.status == "processing",
            or_(
                UserMemoryUpdateJob.lease_expires_at.is_(None),
                UserMemoryUpdateJob.lease_expires_at <= now,
            ),
        ),
    )
    result = db.execute(
        update(UserMemoryUpdateJob)
        .where(
            UserMemoryUpdateJob.id == job_id,
            UserMemoryUpdateJob.user_id == user_id,
            retryable,
        )
        .values(
            status="queued",
            error_message="",
            lease_token="",
            lease_expires_at=None,
            dispatched_at=None,
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    db.commit()
    if result.rowcount != 1:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail="Memory update job is already dispatched, completed, or still processing",
        )
    db.expire_all()
    job = get_user_memory_update_job_or_404(db, user_id, job_id)
    try:
        if memory_jobs.claim_memory_update_job_dispatch(db, job.id):
            memory_jobs.dispatch_memory_update_job(job.id)
    except Exception as exc:
        memory_jobs.record_memory_update_job_dispatch_failure(db, job.id, exc)
    return db.get(UserMemoryUpdateJob, job.id) or job


def get_user_memory_update_job_or_404(db: Session, user_id: str, job_id: str) -> UserMemoryUpdateJob:
    job = db.get(UserMemoryUpdateJob, job_id)
    if job is None or job.user_id != user_id:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Memory update job not found")
    return job


def validate_memory_update_job_status(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in ALLOWED_MEMORY_UPDATE_JOB_STATUSES:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail="Invalid memory update job status")
    return normalized


def create_manual_memory(
    db: Session,
    user_id: str,
    content: str,
    category: str = "general",
    kind: str = "preference",
    canonical_key: str | None = None,
    memory_layer: str | None = None,
    profile_slot: str | None = None,
    pinned: bool | None = None,
    allow_sensitive: bool = False,
    auto_classify: bool = False,
) -> UserMemory:
    normalized = normalize_memory_content(content)
    if not normalized:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail="Memory content cannot be empty")
    validate_manual_memory_content(content, allow_sensitive=allow_sensitive)
    if auto_classify:
        kind, category = classify_manual_memory(db, user_id, content)
    action = upsert_memory_candidate(
        db,
        user_id,
        MemoryCandidate(content=content, kind=kind, category=category, canonical_key=canonical_key or ""),
        source=MemorySource(text="manual"),
    )
    memory = db.get(UserMemory, action.memory_id) if action.memory_id else None
    if memory is None:
        resolved_category = category if category and category != "general" else infer_memory_category(normalized)
        memory = create_memory_row(
            db,
            user_id,
            content,
            normalized,
            hash_content(normalized),
            resolved_category,
            MemorySource(text="manual"),
            embed_memory_text(normalized),
            kind=kind,
            canonical_key=canonical_key,
            event_reason="manual memory create",
        )
    previous_status = memory.status
    previous_governance = (
        memory.category,
        memory.kind,
        memory.canonical_key,
        memory.memory_layer,
        memory.profile_slot,
        memory.pinned,
        memory.scope_type,
        memory.scope_id,
    )
    if category and category != "general":
        memory.category = category
    memory.kind = kind or memory.kind
    apply_memory_governance(
        memory,
        memory_layer=memory_layer,
        profile_slot=profile_slot,
        pinned=pinned,
        canonical_key=canonical_key,
    )
    current_governance = (
        memory.category,
        memory.kind,
        memory.canonical_key,
        memory.memory_layer,
        memory.profile_slot,
        memory.pinned,
        memory.scope_type,
        memory.scope_id,
    )
    if current_governance != previous_governance:
        memory.revision += 1
    activation_conflicts: list[UserMemory] = []
    if memory.status == "active":
        activation_conflicts = resolve_active_memory_conflicts(
            db,
            memory,
            actor_user_id=user_id,
            reason="manual memory metadata update superseded conflicting active memory",
        )
    db.add(memory)
    db.flush()
    memory_events.record_memory_event(
        db,
        memory,
        "manual_update",
        actor_type="user",
        actor_user_id=user_id,
        reason="manual memory metadata update",
        previous_status=previous_status,
        new_status=memory.status,
        payload={"superseded_conflict_ids": [conflict.id for conflict in activation_conflicts]},
    )
    db.commit()
    db.refresh(memory)
    record_memory_governance_audit(
        db,
        user_id,
        "memory.create",
        memory,
        previous_status=previous_status,
        metadata={
            "upsert_action": action.action,
            "superseded_conflict_ids": [conflict.id for conflict in activation_conflicts],
        },
    )
    return memory


def update_user_memory(
    db: Session,
    user_id: str,
    memory_id: str,
    expected_revision: int | None = None,
    content: str | None = None,
    status: str | None = None,
    category: str | None = None,
    kind: str | None = None,
    canonical_key: str | None = None,
    memory_layer: str | None = None,
    profile_slot: str | None = None,
    pinned: bool | None = None,
    allow_sensitive: bool = False,
    auto_classify: bool = False,
) -> UserMemory:
    memory = get_user_memory_or_404(db, user_id, memory_id)
    if expected_revision is not None and memory.revision != expected_revision:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail="Memory changed since it was loaded; refresh and retry",
        )
    previous_status = memory.status
    previous_content_hash = memory.content_hash
    activation_conflicts: list[UserMemory] = []
    requested_status = validate_memory_status(status) if status is not None else None
    if content is not None:
        normalized = normalize_memory_content(content)
        if not normalized:
            raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail="Memory content cannot be empty")
        validate_manual_memory_content(content, allow_sensitive=allow_sensitive)
        if auto_classify and category is None and kind is None:
            kind, category = classify_manual_memory(db, user_id, content)
        embedding = embed_memory_text(normalized)
        memory.content = content.strip()
        memory.normalized_content = normalized
        memory.content_hash = hash_content(normalized)
        memory.embedding = embedding.vector
        memory.embedding_model = embedding.model
        memory.embedding_dimension = embedding.dimension
    if status is not None:
        if requested_status == "active":
            pass
        else:
            memory.status = requested_status
        if requested_status in {"pending"}:
            memory.invalid_at = None
        elif requested_status not in {None, "active"} and memory.invalid_at is None:
            memory.invalid_at = datetime.now(timezone.utc)
    if category is not None:
        memory.category = category
    if kind is not None:
        memory.kind = kind
    if canonical_key is not None:
        memory.canonical_key = memory_policy.normalize_canonical_key(canonical_key)
    if memory_layer is not None:
        memory.memory_layer = memory_policy.validate_memory_layer(memory_layer)
    else:
        memory.memory_layer = memory_policy.memory_layer_for_fields(memory.kind, memory.category)
    if profile_slot is not None:
        memory.profile_slot = profile_slot.strip().lower()[:80]
    else:
        memory.profile_slot = memory_policy.profile_slot_for_fields(memory.kind, memory.category)
    if canonical_key is None and any(value is not None for value in (category, kind, memory_layer, profile_slot)):
        memory.canonical_key = (
            memory_policy.canonical_key_for_profile_slot(memory.profile_slot)
            or memory_policy.canonical_key_for_fields(
                kind=memory.kind,
                category=memory.category,
                normalized_content=memory.normalized_content,
                explicit_key="",
            )
        )
    if pinned is not None:
        memory.pinned = pinned
    else:
        memory.pinned = memory_policy.pinned_for_layer(memory.memory_layer)
    if not memory.scope_type:
        memory.scope_type = "user"
    if not memory.scope_id:
        memory.scope_id = user_id
    memory.extra_metadata = {
        **(memory.extra_metadata or {}),
        "canonical_key": memory.canonical_key,
        "memory_layer": memory.memory_layer,
        "profile_slot": memory.profile_slot,
    }
    if requested_status == "active" or (requested_status is None and memory.status == "active"):
        conflict_reason = (
            "manual memory activation superseded conflicting active memory"
            if requested_status == "active"
            else "manual memory update superseded conflicting active memory"
        )
        activation_conflicts = resolve_active_memory_conflicts(
            db,
            memory,
            actor_user_id=user_id,
            reason=conflict_reason,
        )
    if requested_status == "active":
        memory.status = "active"
        memory.invalid_at = None
    memory.revision += 1
    memory.last_touched_at = datetime.now(timezone.utc)
    db.add(memory)
    db.flush()
    memory_events.record_memory_event(
        db,
        memory,
        "manual_update",
        actor_type="user",
        actor_user_id=user_id,
        reason="manual memory update",
        previous_status=previous_status,
        new_status=memory.status,
        payload={
            "previous_content_hash": previous_content_hash,
            "superseded_conflict_ids": [conflict.id for conflict in activation_conflicts],
        },
    )
    db.commit()
    db.refresh(memory)
    record_memory_governance_audit(
        db,
        user_id,
        "memory.update",
        memory,
        previous_status=previous_status,
        metadata={
            "updated_fields": memory_update_field_names(
                content=content,
                status=status,
                category=category,
                kind=kind,
                canonical_key=canonical_key,
                memory_layer=memory_layer,
                profile_slot=profile_slot,
                pinned=pinned,
            ),
            "superseded_conflict_ids": [conflict.id for conflict in activation_conflicts],
        },
    )
    return memory


def classify_manual_memory(db: Session, user_id: str, content: str) -> tuple[str, str]:
    fallback = ("preference", infer_memory_category(normalize_memory_content(content)))
    try:
        classification = get_llm_provider().classify_memory_with_metadata(content)
        create_llm_call_log(
            db,
            classification.completion,
            user_id=user_id,
            agent_name="memory_classifier",
        )
    except Exception:
        db.rollback()
        return fallback

    kind = classification.kind
    category = classification.category
    if category == "global_instruction" and not memory_policy.is_explicit_global_instruction(content):
        return "instruction", "task_instruction"
    return kind, category


def validate_manual_memory_content(content: str, *, allow_sensitive: bool) -> None:
    if allow_sensitive:
        return
    if memory_policy.has_sensitive_memory_content(content):
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=SENSITIVE_MEMORY_CONFIRMATION_REQUIRED,
        )


def approve_user_memory(db: Session, user_id: str, memory_id: str) -> UserMemory:
    memory = get_user_memory_or_404(db, user_id, memory_id)
    if memory.status != "pending":
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail="Only pending memories can be approved")
    previous_status = memory.status
    apply_memory_governance(memory)
    activation_conflicts = resolve_active_memory_conflicts(
        db,
        memory,
        actor_user_id=user_id,
        reason="approved pending memory superseded conflicting active memory",
    )
    memory.status = "active"
    memory.invalid_at = None
    memory.revision += 1
    memory.last_touched_at = datetime.now(timezone.utc)
    db.add(memory)
    db.flush()
    memory_events.record_memory_event(
        db,
        memory,
        "approve",
        actor_type="user",
        actor_user_id=user_id,
        reason="user approved pending memory",
        previous_status=previous_status,
        new_status=memory.status,
        payload={"superseded_conflict_ids": [conflict.id for conflict in activation_conflicts]},
    )
    db.commit()
    db.refresh(memory)
    record_memory_governance_audit(
        db,
        user_id,
        "memory.approve",
        memory,
        previous_status=previous_status,
        metadata={"superseded_conflict_ids": [conflict.id for conflict in activation_conflicts]},
    )
    return memory


def reject_user_memory(db: Session, user_id: str, memory_id: str) -> UserMemory:
    memory = get_user_memory_or_404(db, user_id, memory_id)
    if memory.status != "pending":
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail="Only pending memories can be rejected")
    previous_status = memory.status
    memory.status = "ignored"
    memory.invalid_at = datetime.now(timezone.utc)
    memory.last_touched_at = memory.invalid_at
    memory.revision += 1
    db.add(memory)
    db.flush()
    memory_events.record_memory_event(
        db,
        memory,
        "reject",
        actor_type="user",
        actor_user_id=user_id,
        reason="user rejected pending memory",
        previous_status=previous_status,
        new_status=memory.status,
    )
    db.commit()
    db.refresh(memory)
    record_memory_governance_audit(
        db,
        user_id,
        "memory.reject",
        memory,
        previous_status=previous_status,
    )
    return memory


def restore_user_memory(db: Session, user_id: str, memory_id: str) -> UserMemory:
    memory = get_user_memory_or_404(db, user_id, memory_id)
    if memory.status != "deleted":
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail="Only deleted memories can be restored")
    previous_status = memory.status
    apply_memory_governance(memory)
    activation_conflicts = resolve_active_memory_conflicts(
        db,
        memory,
        actor_user_id=user_id,
        reason="restored memory superseded conflicting active memory",
    )
    memory.status = "active"
    memory.invalid_at = None
    memory.revision += 1
    memory.last_touched_at = datetime.now(timezone.utc)
    db.add(memory)
    db.flush()
    memory_events.record_memory_event(
        db,
        memory,
        "restore",
        actor_type="user",
        actor_user_id=user_id,
        reason="user restored deleted memory",
        previous_status=previous_status,
        new_status=memory.status,
        payload={"superseded_conflict_ids": [conflict.id for conflict in activation_conflicts]},
    )
    db.commit()
    db.refresh(memory)
    record_memory_governance_audit(
        db,
        user_id,
        "memory.restore",
        memory,
        previous_status=previous_status,
        metadata={"superseded_conflict_ids": [conflict.id for conflict in activation_conflicts]},
    )
    return memory


def delete_user_memory(db: Session, user_id: str, memory_id: str) -> None:
    memory = get_user_memory_or_404(db, user_id, memory_id)
    previous_status = memory.status
    memory_commands.soft_delete_memory(db, memory, actor_user_id=user_id)
    record_memory_governance_audit(
        db,
        user_id,
        "memory.delete",
        memory,
        previous_status=previous_status,
    )


def purge_user_memory(db: Session, user_id: str, memory_id: str) -> None:
    memory = get_user_memory_or_404(db, user_id, memory_id)
    redaction_counts = redact_purged_memory_references(db, memory)
    audit_payload = {**purged_memory_audit_payload(memory), **redaction_counts}
    event = UserMemoryEvent(
        user_id=memory.user_id,
        memory_id=None,
        event_type="purge",
        actor_type="user",
        actor_user_id=user_id,
        source="memory_service",
        reason="user permanently purged memory",
        previous_status=memory.status,
        new_status="purged",
        payload=audit_payload,
    )
    db.add(event)
    db.flush()
    db.delete(memory)
    db.commit()
    record_audit_event(
        db,
        actor_user_id=user_id,
        action="memory.purge",
        resource_type="user_memory",
        resource_id=memory_id,
        metadata=audit_payload,
    )


def purged_memory_audit_payload(memory: UserMemory) -> dict:
    return {
        "erased": True,
        "memory_id": memory.id,
        "previous_status": memory.status,
        "kind": memory.kind,
        "category": memory.category,
        "canonical_key": memory.canonical_key,
        "memory_layer": memory.memory_layer,
        "profile_slot": memory.profile_slot,
        "scope_type": memory.scope_type,
        "scope_id": memory.scope_id,
        "revision": memory.revision,
        "created_at": memory.created_at.isoformat() if memory.created_at else None,
        "updated_at": memory.updated_at.isoformat() if memory.updated_at else None,
        "last_touched_at": memory.last_touched_at.isoformat() if memory.last_touched_at else None,
    }


def record_memory_governance_audit(
    db: Session,
    actor_user_id: str,
    action: str,
    memory: UserMemory,
    *,
    previous_status: str | None = None,
    metadata: dict | None = None,
) -> None:
    payload = {
        **memory_governance_audit_payload(memory, previous_status=previous_status),
        **(metadata or {}),
    }
    record_audit_event(
        db,
        actor_user_id=actor_user_id,
        action=action,
        resource_type="user_memory",
        resource_id=memory.id,
        metadata=payload,
    )


def memory_governance_audit_payload(memory: UserMemory, *, previous_status: str | None = None) -> dict:
    return {
        "previous_status": previous_status,
        "status": memory.status,
        "kind": memory.kind,
        "category": memory.category,
        "canonical_key": memory.canonical_key,
        "memory_layer": memory.memory_layer,
        "profile_slot": memory.profile_slot,
        "scope_type": memory.scope_type,
        "scope_id": memory.scope_id,
        "pinned": memory.pinned,
        "revision": memory.revision,
        "superseded_by_id": memory.superseded_by_id,
    }


def memory_update_field_names(**fields: object) -> list[str]:
    return sorted(name for name, value in fields.items() if value is not None)


def redact_purged_memory_references(db: Session, memory: UserMemory) -> dict:
    return {
        "redacted_event_count": redact_purged_memory_events(db, memory),
        "redacted_recall_log_count": redact_purged_memory_recall_logs(db, memory),
        "redacted_update_job_count": redact_purged_memory_update_jobs(db, memory),
        "redacted_agent_run_count": redact_purged_memory_agent_runs(db, memory),
    }


def redact_purged_memory_events(db: Session, memory: UserMemory) -> int:
    events = db.scalars(
        select(UserMemoryEvent).where(
            UserMemoryEvent.user_id == memory.user_id,
            UserMemoryEvent.memory_id == memory.id,
        )
    ).all()
    for event in events:
        event.memory_id = None
        event.payload = {
            "erased": True,
            "memory_id": memory.id,
            "redacted_event_type": event.event_type,
        }
        db.add(event)
    return len(events)


def redact_purged_memory_recall_logs(db: Session, memory: UserMemory) -> int:
    logs = db.scalars(select(UserMemoryRecallLog).where(UserMemoryRecallLog.user_id == memory.user_id)).all()
    redacted_count = 0
    for log in logs:
        selected_ids = list(log.selected_memory_ids or [])
        candidates = list(log.candidates or [])
        filtered_selected_ids = [memory_id for memory_id in selected_ids if memory_id != memory.id]
        filtered_candidates = [
            candidate
            for candidate in candidates
            if not (isinstance(candidate, dict) and candidate.get("memory_id") == memory.id)
        ]
        if filtered_selected_ids == selected_ids and filtered_candidates == candidates:
            continue
        log.selected_memory_ids = filtered_selected_ids
        log.selected_count = len(filtered_selected_ids)
        log.candidates = filtered_candidates
        db.add(log)
        redacted_count += 1
    return redacted_count


def redact_purged_memory_update_jobs(db: Session, memory: UserMemory) -> int:
    jobs = db.scalars(select(UserMemoryUpdateJob).where(UserMemoryUpdateJob.user_id == memory.user_id)).all()
    redacted_count = 0
    for job in jobs:
        actions = list(job.actions or [])
        if not any(value_references_memory_id(action, memory.id) for action in actions):
            continue
        job.actions = [redact_memory_action(action, memory.id) for action in actions]
        job.user_message = PURGED_MEMORY_REDACTION_TEXT
        job.assistant_message = ""
        db.add(job)
        redacted_count += 1
    return redacted_count


def value_references_memory_id(value: object, memory_id: str) -> bool:
    if isinstance(value, dict):
        return any(value_references_memory_id(item, memory_id) for item in value.values())
    if isinstance(value, list):
        return any(value_references_memory_id(item, memory_id) for item in value)
    return value == memory_id


def redact_memory_action(action: object, memory_id: str) -> object:
    if not value_references_memory_id(action, memory_id):
        return action
    if not isinstance(action, dict):
        return {"redacted": True, "reason": "memory reference redacted after purge"}
    return {
        "action": action.get("action") or "redacted",
        "memory_id": None,
        "content": "",
        "reason": "memory reference redacted after purge",
        "redacted": True,
    }


def redact_purged_memory_agent_runs(db: Session, memory: UserMemory) -> int:
    runs = db.scalars(select(AgentRun).where(AgentRun.user_id == memory.user_id)).all()
    redacted_count = 0
    for run in runs:
        state = run.state if isinstance(run.state, dict) else {}
        trace = run.trace if isinstance(run.trace, list) else []
        if not (
            value_references_memory_id(state, memory.id)
            or value_references_memory_id(trace, memory.id)
        ):
            continue
        run.state = redact_purged_memory_value(state, memory.id)
        run.trace = redact_purged_memory_value(trace, memory.id)
        db.add(run)
        redacted_count += 1
    return redacted_count


def redact_purged_memory_value(value: object, memory_id: str) -> object:
    if isinstance(value, dict):
        if value.get("id") == memory_id or value.get("memory_id") == memory_id:
            redacted = {
                key: redact_purged_memory_value(item, memory_id)
                for key, item in value.items()
                if key not in {"content", "source_text"}
            }
            if "id" in value:
                redacted["id"] = None
            if "memory_id" in value:
                redacted["memory_id"] = None
            redacted["content"] = ""
            redacted["redacted"] = True
            redacted["reason"] = "memory reference redacted after purge"
            return redacted
        return {key: redact_purged_memory_value(item, memory_id) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_purged_memory_value(item, memory_id) for item in value]
    return value


def get_user_memory_or_404(db: Session, user_id: str, memory_id: str) -> UserMemory:
    memory = memory_repository.get_user_memory(db, user_id, memory_id)
    if memory is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Memory not found")
    return memory


def validate_memory_status(value: str) -> str:
    return memory_policy.validate_memory_status(value)


def apply_memory_governance(
    memory: UserMemory,
    memory_layer: str | None = None,
    profile_slot: str | None = None,
    pinned: bool | None = None,
    canonical_key: str | None = None,
) -> None:
    if memory_layer is None:
        memory.memory_layer = memory_policy.memory_layer_for_fields(memory.kind, memory.category)
    else:
        memory.memory_layer = memory_policy.validate_memory_layer(memory_layer)
    if profile_slot is None:
        memory.profile_slot = memory_policy.profile_slot_for_fields(memory.kind, memory.category)
    else:
        memory.profile_slot = profile_slot.strip().lower()[:80]
    if canonical_key is None:
        existing_key = memory_policy.normalize_canonical_key(memory.canonical_key)
        slot_key = memory_policy.canonical_key_for_profile_slot(memory.profile_slot)
        field_key = memory_policy.canonical_key_for_fields(
            kind=memory.kind,
            category=memory.category,
            normalized_content=memory.normalized_content,
        )
        if profile_slot is not None or memory_layer is not None:
            memory.canonical_key = slot_key or field_key or existing_key
        else:
            memory.canonical_key = existing_key or slot_key or field_key
    else:
        memory.canonical_key = memory_policy.normalize_canonical_key(canonical_key)
    memory.pinned = memory_policy.pinned_for_layer(memory.memory_layer) if pinned is None else pinned
    if not memory.scope_type:
        memory.scope_type = "user"
    if not memory.scope_id:
        memory.scope_id = memory.user_id
    memory.extra_metadata = {
        **(memory.extra_metadata or {}),
        "canonical_key": memory.canonical_key,
        "memory_layer": memory.memory_layer,
        "profile_slot": memory.profile_slot,
    }


def resolve_active_memory_conflicts(
    db: Session,
    memory: UserMemory,
    *,
    actor_user_id: str,
    reason: str,
) -> list[UserMemory]:
    return memory_commands.supersede_activation_conflicts(
        db,
        memory,
        actor_type="user",
        actor_user_id=actor_user_id,
        reason=reason,
    )


def list_active_memory_activation_conflicts(db: Session, memory: UserMemory) -> list[UserMemory]:
    return memory_commands.list_activation_conflicts_for_existing_memory(db, memory)


def to_memory_action_dict(action: MemoryAction) -> dict:
    return {
        "action": action.action,
        "memory_id": action.memory_id,
        "content": action.content,
        "reason": action.reason,
    }


def short_memory_key(user_id: str, conversation_id: str) -> str:
    return short_term.short_memory_key(user_id, conversation_id)


def normalize_memory_content(content: str) -> str:
    return memory_policy.normalize_memory_content(content)


def hash_content(content: str) -> str:
    return memory_policy.hash_content(content)


def infer_memory_category(content: str) -> str:
    return memory_policy.infer_memory_category(content)


def find_conflicting_memory(memories: list[UserMemory], normalized: str, category: str) -> UserMemory | None:
    return memory_editor.find_conflicting_memory(memories, normalized, category)


def embed_memory_text(text: str) -> MemoryEmbedding:
    return memory_embedding.embed_memory_text(text)


def create_memory_row(
    db: Session,
    user_id: str,
    content: str,
    normalized: str,
    content_hash: str,
    category: str,
    source: MemorySource,
    embedding: MemoryEmbedding,
    status: str = "active",
    kind: str = "preference",
    extra_metadata: dict | None = None,
    canonical_key: str | None = None,
    memory_layer: str | None = None,
    profile_slot: str | None = None,
    scope_type: str = "user",
    scope_id: str | None = None,
    pinned: bool | None = None,
    expires_at: datetime | None = None,
    enforce_profile_singleton: bool = True,
    enforce_canonical_key_conflicts: bool = True,
    event_type: str | None = None,
    event_reason: str = "",
) -> UserMemory:
    return memory_commands.create_memory_row(
        db,
        user_id,
        content,
        normalized,
        content_hash,
        category,
        source,
        embedding,
        status=status,
        kind=kind,
        extra_metadata=extra_metadata,
        canonical_key=canonical_key,
        memory_layer=memory_layer,
        profile_slot=profile_slot,
        scope_type=scope_type,
        scope_id=scope_id,
        pinned=pinned,
        expires_at=expires_at,
        enforce_profile_singleton=enforce_profile_singleton,
        enforce_canonical_key_conflicts=enforce_canonical_key_conflicts,
        event_type=event_type,
        event_reason=event_reason,
    )


def find_exact_memory(
    db: Session,
    user_id: str,
    content_hash: str,
    statuses: set[str],
) -> UserMemory | None:
    return memory_repository.find_exact_memory(db, user_id, content_hash, statuses)


def touch_exact_memory(db: Session, memory: UserMemory, activate_pending: bool = False) -> MemoryAction:
    return memory_editor.touch_exact_memory(db, memory, activate_pending=activate_pending)


def resolve_memory_category(candidate: MemoryCandidate) -> str:
    return memory_policy.resolve_memory_category(candidate)


def dedupe_memories(memories: list[UserMemory]) -> list[UserMemory]:
    return memory_retrieval.dedupe_memories(memories)


def cosine_similarity(left: list[float], right: list[float]) -> float:
    return memory_retrieval.cosine_similarity(left, right)
