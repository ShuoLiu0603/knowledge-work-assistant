from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

from fastapi import HTTPException, status as http_status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models.conversation import Conversation, Message
from app.db.models.user_memory import UserMemory, UserMemoryEvent, UserMemoryRecallLog, UserMemoryUpdateJob
from app.llm.provider import MemoryCandidate, MemoryOperation, get_llm_provider
from app.memory import commands as memory_commands
from app.memory import context as memory_contexts
from app.memory import editor as memory_editor
from app.memory import embedding as memory_embedding
from app.memory import events as memory_events
from app.memory import jobs as memory_jobs
from app.memory import policy as memory_policy
from app.memory import repository as memory_repository
from app.memory import retrieval as memory_retrieval
from app.memory import short_term
from app.memory import vector_index as memory_vector_index
from app.memory.types import MemoryAction, MemoryEmbedding, MemorySource
from app.services.llm_log_service import create_llm_call_log

ALLOWED_MEMORY_STATUSES = memory_policy.ALLOWED_MEMORY_STATUSES
AUTO_MEMORY_CONFIDENCE = memory_policy.AUTO_MEMORY_CONFIDENCE
PENDING_MEMORY_CONFIDENCE = memory_policy.PENDING_MEMORY_CONFIDENCE
PENDING_OPERATION_CONFIDENCE = memory_policy.PENDING_OPERATION_CONFIDENCE
MAX_MEMORY_OPERATIONS = memory_policy.MAX_MEMORY_OPERATIONS
MEMORY_EDITOR_CONTEXT_LIMIT = memory_policy.MEMORY_EDITOR_CONTEXT_LIMIT
MEMORY_EDITOR_CANDIDATE_LIMIT = memory_policy.MEMORY_EDITOR_CANDIDATE_LIMIT
MEMORY_SOURCE_MAX_CHARS = memory_policy.MEMORY_SOURCE_MAX_CHARS
SUMMARY_DELTA_MAX_CHARS = memory_policy.SUMMARY_DELTA_MAX_CHARS
FULL_MEMORY_RECALL_LIMIT = memory_policy.FULL_MEMORY_RECALL_LIMIT
STICKY_MEMORY_CATEGORIES = memory_policy.STICKY_MEMORY_CATEGORIES
ALLOWED_MEMORY_UPDATE_JOB_STATUSES = {"queued", "processing", "completed", "failed"}


def get_redis_client():
    return short_term.get_redis_client()


def append_short_term_memory(user_id: str, conversation_id: str | None, role: str, content: str) -> None:
    short_term.append_short_term_memory(user_id, conversation_id, role, content)


def get_short_term_memory(user_id: str, conversation_id: str | None) -> list[dict]:
    return short_term.get_short_term_memory(user_id, conversation_id)


def get_recent_db_messages(db: Session, conversation_id: str | None, limit: int = 8) -> list[dict]:
    return short_term.get_recent_db_messages(db, conversation_id, limit=limit)


def process_user_memory(
    db: Session,
    user_id: str,
    text: str,
    conversation_id: str | None = None,
    assistant_text: str = "",
    message_id: str | None = None,
) -> list[MemoryAction]:
    if memory_policy.should_skip_memory_for_turn(text):
        return [MemoryAction("ignore", None, "", "user requested no memory for this turn")]
    if memory_policy.should_ignore_memory_request(text):
        return [MemoryAction("ignore", None, "", "user asked not to remember")]

    provider = get_llm_provider()
    if hasattr(provider, "review_memory_operations"):
        operations = review_memory_operations_with_logging(db, provider, user_id, text, assistant_text, conversation_id)
        actions = [
            process_memory_operation(
                db,
                user_id,
                operation,
                source=memory_source_from_turn(db, conversation_id, text, evidence=operation.evidence, message_id=message_id),
            )
            for operation in operations[:MAX_MEMORY_OPERATIONS]
        ]
        return actions or [MemoryAction("ignore", None, "", "no durable memory operation")]

    candidates = extract_memory_candidates_with_logging(db, provider, user_id, text, conversation_id)
    source = memory_source_from_turn(db, conversation_id, text, message_id=message_id)
    actions: list[MemoryAction] = []
    for candidate in candidates:
        action = process_memory_candidate(db, user_id, candidate, source=source)
        actions.append(action)
    if not actions:
        actions.append(MemoryAction("ignore", None, "", "no durable preference found"))
    return actions


def review_memory_operations_with_logging(
    db: Session,
    provider,
    user_id: str,
    user_message: str,
    assistant_message: str,
    conversation_id: str | None,
) -> list[MemoryOperation]:
    review = provider.review_memory_operations(
        user_message=user_message,
        assistant_message=assistant_message,
        existing_memories=list_memory_editor_context(db, user_id, user_message),
    )
    create_llm_call_log(
        db,
        review.completion,
        user_id=user_id,
        conversation_id=conversation_id,
        agent_name="memory_editor",
    )
    return review.operations


def extract_memory_candidates_with_logging(
    db: Session,
    provider,
    user_id: str,
    text: str,
    conversation_id: str | None,
) -> list[MemoryCandidate]:
    if hasattr(provider, "extract_memory_candidates_with_metadata"):
        extraction = provider.extract_memory_candidates_with_metadata(text)
        create_llm_call_log(
            db,
            extraction.completion,
            user_id=user_id,
            conversation_id=conversation_id,
            agent_name="memory_extractor",
        )
        return normalize_memory_candidates(extraction.candidates)
    return normalize_memory_candidates(provider.extract_memory_candidates(text))


def normalize_memory_candidates(candidates: list) -> list[MemoryCandidate]:
    normalized: list[MemoryCandidate] = []
    for candidate in candidates:
        if isinstance(candidate, MemoryCandidate):
            normalized.append(candidate)
        else:
            content = str(candidate).strip()
            if content:
                normalized.append(MemoryCandidate(content=content))
    return normalized


def process_memory_operation(
    db: Session,
    user_id: str,
    operation: MemoryOperation,
    source: MemorySource,
) -> MemoryAction:
    return memory_editor.process_memory_operation(db, user_id, operation, source)


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


def can_auto_create(operation: MemoryOperation) -> bool:
    return memory_policy.can_auto_create(operation)


def can_auto_update(operation: MemoryOperation) -> bool:
    return memory_policy.can_auto_update(operation)


def can_auto_supersede(operation: MemoryOperation, target: UserMemory) -> bool:
    return memory_policy.can_auto_supersede(operation, target.status)


def is_safe_memory_operation(operation: MemoryOperation, confidence_threshold: float) -> bool:
    return memory_policy.is_safe_memory_operation(operation, confidence_threshold)


def get_operation_target(db: Session, user_id: str, memory_id: str | None) -> UserMemory | None:
    return memory_editor.get_operation_target(db, user_id, memory_id)


def candidate_from_operation(operation: MemoryOperation) -> MemoryCandidate:
    return memory_editor.candidate_from_operation(operation)


def resolve_operation_category(operation: MemoryOperation) -> str:
    return memory_policy.resolve_operation_category(operation)


def memory_operation_metadata(operation: MemoryOperation, decision: str) -> dict:
    return memory_policy.memory_operation_metadata(operation, decision)


def list_memory_editor_context(db: Session, user_id: str, query: str = "") -> list[dict]:
    memories = memory_repository.list_memory_editor_candidates(db, user_id, MEMORY_EDITOR_CANDIDATE_LIMIT)
    ranked = rank_memory_editor_context(list(memories), query)
    return [
        {
            "id": memory.id,
            "status": memory.status,
            "kind": memory.kind,
            "category": memory.category,
            "content": memory.content,
        }
        for memory in ranked[:MEMORY_EDITOR_CONTEXT_LIMIT]
    ]


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
    limit: int = 5,
    conversation_id: str | None = None,
    message_id: str | None = None,
) -> list[UserMemory]:
    active = memory_repository.list_active_memories(db, user_id)
    result = None
    if active and not is_full_memory_recall_query(query):
        try:
            query_vector = embed_memory_text(query).vector
            threshold = memory_policy.retrieval_similarity_threshold()
            vector_hits = memory_vector_index.search_active_memories(
                user_id,
                query_vector,
                limit=max(limit, FULL_MEMORY_RECALL_LIMIT),
                score_threshold=threshold,
            )
            if vector_hits:
                result = memory_retrieval.retrieve_relevant_memories_with_vector_hits(
                    list(active),
                    query,
                    limit,
                    vector_hits,
                    threshold=threshold,
                )
        except Exception:
            result = None
    if result is None:
        result = memory_retrieval.retrieve_relevant_memories_with_metadata(
            list(active),
            query,
            limit,
            embed=lambda text: embed_memory_text(text).vector,
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
    conversation_summary: str | None = None,
) -> str:
    if should_skip_memory_for_turn(query):
        return format_memory_context([], [], None)

    short_memory = preloaded_short_memory
    if short_memory is None:
        short_memory = get_short_term_memory(user_id, conversation_id)
    if not short_memory and conversation_id:
        short_memory = get_recent_db_messages(db, conversation_id)

    if preloaded_long_memories is None:
        memories = retrieve_relevant_memories(db, user_id, query, conversation_id=conversation_id)
        long_memories = [
            {
                "content": memory.content,
                "category": memory.category,
                "kind": memory.kind,
                "status": memory.status,
                "metadata": memory.extra_metadata or {},
            }
            for memory in memories
        ]
    else:
        long_memories = preloaded_long_memories

    summary = conversation_summary
    if summary is None and conversation_id:
        conversation = db.get(Conversation, conversation_id)
        summary = conversation.summary if conversation else None

    max_long_memories = FULL_MEMORY_RECALL_LIMIT if is_full_memory_recall_query(query) else 8
    return format_memory_context(long_memories, short_memory, summary, max_long_memories=max_long_memories)


def format_memory_context(
    long_memories: list[dict],
    short_memory: list[dict],
    conversation_summary: str | None,
    max_long_memories: int = 8,
    max_chars: int | None = None,
    max_tokens: int | None = None,
) -> str:
    settings = get_settings()
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
    )


def update_conversation_summary(
    db: Session,
    conversation: Conversation,
    user_message: str,
    assistant_message: str,
    user_id: str | None = None,
) -> str:
    messages, message_count = get_unprocessed_summary_messages(db, conversation)
    text = build_conversation_summary_prompt(
        conversation.summary or "",
        messages,
        fallback_user_message=user_message,
        fallback_assistant_message=assistant_message,
    )
    provider = get_llm_provider()
    if hasattr(provider, "summarize_with_metadata"):
        completion = provider.summarize_with_metadata(text)
        create_llm_call_log(
            db,
            completion,
            user_id=user_id,
            conversation_id=conversation.id,
            agent_name="conversation_summary",
        )
        summary = completion.content.strip()
    else:
        summary = provider.summarize(text).strip()
    conversation.summary = summary[:3000]
    conversation.summary_message_count = message_count
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation.summary or ""


def should_update_conversation_summary(db: Session, conversation_id: str) -> bool:
    conversation = db.get(Conversation, conversation_id)
    if conversation is None:
        return False
    message_count = db.scalar(select(func.count(Message.id)).where(Message.conversation_id == conversation_id)) or 0
    processed_count = min(max(conversation.summary_message_count or 0, 0), message_count)
    if processed_count == 0:
        return message_count >= 10
    return message_count - processed_count >= 4


def get_unprocessed_summary_messages(db: Session, conversation: Conversation) -> tuple[list[Message], int]:
    message_count = db.scalar(select(func.count(Message.id)).where(Message.conversation_id == conversation.id)) or 0
    processed_count = min(max(conversation.summary_message_count or 0, 0), message_count)
    messages = db.scalars(
        select(Message)
        .where(Message.conversation_id == conversation.id)
        .order_by(Message.created_at.asc(), Message.id.asc())
        .offset(processed_count)
    ).all()
    return list(messages), message_count


def build_conversation_summary_prompt(
    previous_summary: str,
    messages: list[Message],
    fallback_user_message: str,
    fallback_assistant_message: str,
) -> str:
    if messages:
        delta = "\n".join(f"{message.role}: {message.content}" for message in messages)
    else:
        delta = f"user: {fallback_user_message}\nassistant: {fallback_assistant_message}"
    delta = delta[:SUMMARY_DELTA_MAX_CHARS]
    return f"Existing summary:\n{previous_summary or '无'}\n\nNew messages since previous summary:\n{delta}"


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


def ratio(numerator: int | float, denominator: int | float) -> float:
    if not denominator:
        return 0.0
    return round(float(numerator) / float(denominator), 6)


def list_user_memory_update_jobs(db: Session, user_id: str, status: str | None = None) -> list[UserMemoryUpdateJob]:
    if status is not None:
        status = validate_memory_update_job_status(status)
    query = select(UserMemoryUpdateJob).where(UserMemoryUpdateJob.user_id == user_id)
    if status:
        query = query.where(UserMemoryUpdateJob.status == status)
    return db.scalars(query.order_by(UserMemoryUpdateJob.created_at.desc(), UserMemoryUpdateJob.id.desc())).all()


def retry_user_memory_update_job(db: Session, user_id: str, job_id: str) -> UserMemoryUpdateJob:
    job = get_user_memory_update_job_or_404(db, user_id, job_id)
    if job.status not in {"queued", "failed"}:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail="Only queued or failed memory update jobs can be retried",
        )
    job.status = "queued"
    job.error_message = ""
    db.add(job)
    db.commit()
    db.refresh(job)
    try:
        memory_jobs.dispatch_memory_update_job(job.id)
    except Exception as exc:
        job.error_message = f"worker dispatch failed: {exc}"
        db.add(job)
        db.commit()
        db.refresh(job)
    return job


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
) -> UserMemory:
    normalized = normalize_memory_content(content)
    if not normalized:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail="Memory content cannot be empty")
    action = upsert_memory_candidate(db, user_id, content, source=MemorySource(text="manual"))
    memory = db.get(UserMemory, action.memory_id) if action.memory_id else None
    if memory is None:
        memory = create_memory_row(
            db,
            user_id,
            content,
            normalized,
            hash_content(normalized),
            category,
            MemorySource(text="manual"),
            embed_memory_text(normalized),
            event_reason="manual memory create",
        )
    previous_status = memory.status
    memory.category = category or memory.category
    memory.kind = kind or memory.kind
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
    )
    db.commit()
    db.refresh(memory)
    memory_vector_index.try_sync_memory_vector(memory)
    return memory


def update_user_memory(
    db: Session,
    user_id: str,
    memory_id: str,
    content: str | None = None,
    status: str | None = None,
    category: str | None = None,
    kind: str | None = None,
) -> UserMemory:
    memory = get_user_memory_or_404(db, user_id, memory_id)
    previous_status = memory.status
    previous_content_hash = memory.content_hash
    if content is not None:
        normalized = normalize_memory_content(content)
        if not normalized:
            raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail="Memory content cannot be empty")
        embedding = embed_memory_text(normalized)
        memory.content = content.strip()
        memory.normalized_content = normalized
        memory.content_hash = hash_content(normalized)
        memory.embedding = embedding.vector
        memory.embedding_model = embedding.model
        memory.embedding_dimension = embedding.dimension
    if status is not None:
        memory.status = validate_memory_status(status)
        if memory.status in {"active", "pending"}:
            memory.invalid_at = None
        elif memory.invalid_at is None:
            memory.invalid_at = datetime.now(timezone.utc)
    if category is not None:
        memory.category = category
    if kind is not None:
        memory.kind = kind
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
        payload={"previous_content_hash": previous_content_hash},
    )
    db.commit()
    db.refresh(memory)
    if memory.status == "deleted":
        memory_vector_index.try_delete_memory_vector(memory.id)
    else:
        memory_vector_index.try_sync_memory_vector(memory)
    return memory


def approve_user_memory(db: Session, user_id: str, memory_id: str) -> UserMemory:
    memory = get_user_memory_or_404(db, user_id, memory_id)
    if memory.status != "pending":
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail="Only pending memories can be approved")
    previous_status = memory.status
    memory.status = "active"
    memory.invalid_at = None
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
    )
    db.commit()
    db.refresh(memory)
    memory_vector_index.try_sync_memory_vector(memory)
    return memory


def reject_user_memory(db: Session, user_id: str, memory_id: str) -> UserMemory:
    memory = get_user_memory_or_404(db, user_id, memory_id)
    if memory.status != "pending":
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail="Only pending memories can be rejected")
    previous_status = memory.status
    memory.status = "ignored"
    memory.invalid_at = datetime.now(timezone.utc)
    memory.last_touched_at = memory.invalid_at
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
    memory_vector_index.try_sync_memory_vector(memory)
    return memory


def restore_user_memory(db: Session, user_id: str, memory_id: str) -> UserMemory:
    memory = get_user_memory_or_404(db, user_id, memory_id)
    if memory.status != "deleted":
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail="Only deleted memories can be restored")
    previous_status = memory.status
    memory.status = "active"
    memory.invalid_at = None
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
    )
    db.commit()
    db.refresh(memory)
    memory_vector_index.try_sync_memory_vector(memory)
    return memory


def delete_user_memory(db: Session, user_id: str, memory_id: str) -> None:
    memory = get_user_memory_or_404(db, user_id, memory_id)
    memory_commands.soft_delete_memory(db, memory, actor_user_id=user_id)


def purge_user_memory(db: Session, user_id: str, memory_id: str) -> None:
    memory = get_user_memory_or_404(db, user_id, memory_id)
    memory_vector_index.try_delete_memory_vector(memory.id)
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
        payload=memory_events.memory_snapshot(memory),
    )
    db.add(event)
    db.flush()
    db.delete(memory)
    db.commit()


def get_user_memory_or_404(db: Session, user_id: str, memory_id: str) -> UserMemory:
    memory = memory_repository.get_user_memory(db, user_id, memory_id)
    if memory is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Memory not found")
    return memory


def validate_memory_status(value: str) -> str:
    return memory_policy.validate_memory_status(value)


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


def find_similar_memory(memories: list[UserMemory], embedding: list[float], normalized: str = "") -> UserMemory | None:
    return memory_editor.find_similar_memory(memories, embedding, normalized)


def find_same_direction_preference(memories: list[UserMemory], normalized: str) -> UserMemory | None:
    return memory_editor.find_same_direction_preference(memories, normalized)


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
        event_type=event_type,
        event_reason=event_reason,
    )


def merge_memory_content(existing: str, incoming: str) -> str:
    return memory_editor.merge_memory_content(existing, incoming)


def find_exact_memory(
    db: Session,
    user_id: str,
    content_hash: str,
    statuses: set[str],
) -> UserMemory | None:
    return memory_repository.find_exact_memory(db, user_id, content_hash, statuses)


def touch_exact_memory(db: Session, memory: UserMemory, candidate: MemoryCandidate) -> MemoryAction:
    return memory_editor.touch_exact_memory(db, memory, candidate)


def metadata_confidence(memory: UserMemory) -> float:
    return memory_policy.metadata_confidence(memory)


def resolve_memory_category(candidate: MemoryCandidate) -> str:
    return memory_policy.resolve_memory_category(candidate)


def retrieval_similarity_threshold() -> float:
    return memory_policy.retrieval_similarity_threshold()


def dedupe_memories(memories: list[UserMemory]) -> list[UserMemory]:
    return memory_retrieval.dedupe_memories(memories)


def cosine_similarity(left: list[float], right: list[float]) -> float:
    return memory_retrieval.cosine_similarity(left, right)
