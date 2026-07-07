from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone

import redis
from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models.conversation import Conversation, Message
from app.db.models.user_memory import UserMemory
from app.llm.provider import MemoryCandidate, MemoryOperation, get_llm_provider
from app.rag.embeddings import get_embedding_provider
from app.services.llm_log_service import create_llm_call_log

ALLOWED_MEMORY_STATUSES = {"active", "pending", "superseded", "ignored"}
AUTO_MEMORY_CONFIDENCE = 0.75
PENDING_MEMORY_CONFIDENCE = 0.55
AUTO_OPERATION_CONFIDENCE = 0.8
SUPERSEDE_OPERATION_CONFIDENCE = 0.85
PENDING_OPERATION_CONFIDENCE = 0.6
MAX_MEMORY_OPERATIONS = 3
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


@dataclass(frozen=True)
class MemoryAction:
    action: str
    memory_id: str | None
    content: str
    reason: str


def get_redis_client():
    settings = get_settings()
    try:
        return redis.Redis.from_url(settings.redis_url, decode_responses=True)
    except Exception:
        return None


def append_short_term_memory(user_id: str, conversation_id: str | None, role: str, content: str) -> None:
    if not conversation_id or not content.strip():
        return
    client = get_redis_client()
    if client is None:
        return
    settings = get_settings()
    key = short_memory_key(user_id, conversation_id)
    payload = json.dumps(
        {
            "role": role,
            "content": content.strip()[:2000],
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
        ensure_ascii=False,
    )
    try:
        client.lpush(key, payload)
        client.ltrim(key, 0, settings.short_memory_max_messages - 1)
        client.expire(key, 60 * 60 * 24)
    except Exception:
        return


def get_short_term_memory(user_id: str, conversation_id: str | None) -> list[dict]:
    if not conversation_id:
        return []
    client = get_redis_client()
    if client is None:
        return []
    key = short_memory_key(user_id, conversation_id)
    try:
        rows = client.lrange(key, 0, -1)
    except Exception:
        return []
    messages = []
    for row in reversed(rows):
        try:
            messages.append(json.loads(row))
        except json.JSONDecodeError:
            continue
    return messages


def get_recent_db_messages(db: Session, conversation_id: str | None, limit: int = 8) -> list[dict]:
    if not conversation_id:
        return []
    rows = db.scalars(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(limit)
    ).all()
    return [
        {
            "role": message.role,
            "content": message.content,
            "created_at": message.created_at.isoformat(),
        }
        for message in reversed(rows)
    ]


def process_user_memory(
    db: Session,
    user_id: str,
    text: str,
    conversation_id: str | None = None,
    assistant_text: str = "",
) -> list[MemoryAction]:
    lowered = text.lower()
    if any(marker in text for marker in ("不要记住", "别记住", "无需记住", "不要保存", "别保存")) or any(
        marker in lowered for marker in ("do not remember", "don't remember", "dont remember")
    ):
        return [MemoryAction("ignore", None, "", "user asked not to remember")]

    provider = get_llm_provider()
    if hasattr(provider, "review_memory_operations"):
        operations = review_memory_operations_with_logging(db, provider, user_id, text, assistant_text, conversation_id)
        actions = [
            process_memory_operation(db, user_id, operation, source_text=memory_source_text(text, assistant_text))
            for operation in operations[:MAX_MEMORY_OPERATIONS]
        ]
        return actions or [MemoryAction("ignore", None, "", "no durable memory operation")]

    candidates = extract_memory_candidates_with_logging(db, provider, user_id, text, conversation_id)
    actions: list[MemoryAction] = []
    for candidate in candidates:
        action = process_memory_candidate(db, user_id, candidate, source_text=text)
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
        existing_memories=list_memory_editor_context(db, user_id),
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
    source_text: str,
) -> MemoryAction:
    if operation.action == "ignore":
        return MemoryAction("ignore", operation.target_memory_id, operation.content, operation.reason or "memory editor ignored")

    normalized = normalize_memory_content(operation.content)
    if not normalized:
        return MemoryAction("ignore", operation.target_memory_id, "", "memory operation has no content")

    candidate = candidate_from_operation(operation)
    exact = find_exact_memory(db, user_id, hash_content(normalized), statuses={"active", "pending"})
    if exact and operation.action in {"create", "pending"}:
        return touch_exact_memory(db, exact, candidate)

    if operation.action == "create":
        if can_auto_create(operation):
            memory = create_memory_from_operation(db, user_id, operation, normalized, source_text, status="active")
            return MemoryAction("create", memory.id, memory.content, operation.reason or "memory editor created memory")
        return create_pending_memory_from_operation(db, user_id, operation, normalized, source_text)

    if operation.action == "pending":
        return create_pending_memory_from_operation(db, user_id, operation, normalized, source_text)

    target = get_operation_target(db, user_id, operation.target_memory_id)
    if target is None:
        return MemoryAction("ignore", None, operation.content, "target memory not found")

    if operation.action == "update":
        if not can_auto_update(operation):
            return create_pending_memory_from_operation(db, user_id, operation, normalized, source_text)
        target.content = operation.content
        target.normalized_content = normalized
        target.content_hash = hash_content(normalized)
        target.category = resolve_operation_category(operation)
        target.kind = operation.kind
        target.source_text = source_text
        target.embedding = get_embedding_provider().embed_text(normalized)
        target.merge_count += 1
        target.last_touched_at = datetime.now(timezone.utc)
        target.extra_metadata = memory_operation_metadata(operation, decision="auto_update")
        if target.status == "pending":
            target.status = "active"
        db.add(target)
        db.commit()
        db.refresh(target)
        return MemoryAction("update", target.id, target.content, operation.reason or "memory editor updated memory")

    if operation.action == "supersede":
        if not can_auto_supersede(operation, target):
            return create_pending_memory_from_operation(db, user_id, operation, normalized, source_text)
        memory = create_memory_from_operation(db, user_id, operation, normalized, source_text, status="active")
        target.status = "superseded"
        target.superseded_by_id = memory.id
        target.last_touched_at = datetime.now(timezone.utc)
        db.add(target)
        db.commit()
        db.refresh(memory)
        return MemoryAction("supersede", memory.id, memory.content, operation.reason or f"superseded {target.id}")

    return MemoryAction("ignore", operation.target_memory_id, operation.content, "unsupported memory operation")


def create_memory_from_operation(
    db: Session,
    user_id: str,
    operation: MemoryOperation,
    normalized: str,
    source_text: str,
    status: str,
) -> UserMemory:
    return create_memory_row(
        db,
        user_id,
        operation.content,
        normalized,
        hash_content(normalized),
        resolve_operation_category(operation),
        source_text,
        get_embedding_provider().embed_text(normalized),
        status=status,
        kind=operation.kind,
        extra_metadata=memory_operation_metadata(operation, decision=f"auto_{operation.action}"),
    )


def create_pending_memory_from_operation(
    db: Session,
    user_id: str,
    operation: MemoryOperation,
    normalized: str,
    source_text: str,
) -> MemoryAction:
    if operation.action != "pending" and operation.confidence < PENDING_OPERATION_CONFIDENCE:
        return MemoryAction("ignore", operation.target_memory_id, operation.content, "memory operation confidence below threshold")
    memory = create_memory_row(
        db,
        user_id,
        operation.content,
        normalized,
        hash_content(normalized),
        resolve_operation_category(operation),
        source_text,
        get_embedding_provider().embed_text(normalized),
        status="pending",
        kind=operation.kind,
        extra_metadata=memory_operation_metadata(operation, decision="pending_user_review"),
    )
    return MemoryAction("pending", memory.id, memory.content, operation.reason or "memory operation requires user review")


def can_auto_create(operation: MemoryOperation) -> bool:
    return is_safe_memory_operation(operation, AUTO_OPERATION_CONFIDENCE) and operation.importance == "high"


def can_auto_update(operation: MemoryOperation) -> bool:
    return is_safe_memory_operation(operation, AUTO_OPERATION_CONFIDENCE) and operation.importance in {"medium", "high"}


def can_auto_supersede(operation: MemoryOperation, target: UserMemory) -> bool:
    return (
        target.status == "active"
        and is_safe_memory_operation(operation, SUPERSEDE_OPERATION_CONFIDENCE)
        and operation.importance == "high"
    )


def is_safe_memory_operation(operation: MemoryOperation, confidence_threshold: float) -> bool:
    return (
        operation.confidence >= confidence_threshold
        and operation.sensitivity == "low"
        and bool(operation.evidence.strip())
    )


def get_operation_target(db: Session, user_id: str, memory_id: str | None) -> UserMemory | None:
    if not memory_id:
        return None
    memory = db.get(UserMemory, memory_id)
    if memory is None or memory.user_id != user_id:
        return None
    return memory


def candidate_from_operation(operation: MemoryOperation) -> MemoryCandidate:
    return MemoryCandidate(
        content=operation.content,
        kind=operation.kind,
        category=operation.category,
        confidence=operation.confidence,
        sensitivity=operation.sensitivity,
    )


def resolve_operation_category(operation: MemoryOperation) -> str:
    category = (operation.category or "").strip().lower()
    if category and category != "general":
        return category[:80]
    return infer_memory_category(normalize_memory_content(operation.content))


def memory_operation_metadata(operation: MemoryOperation, decision: str) -> dict:
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


def list_memory_editor_context(db: Session, user_id: str) -> list[dict]:
    memories = db.scalars(
        select(UserMemory)
        .where(UserMemory.user_id == user_id)
        .order_by(UserMemory.status.asc(), UserMemory.updated_at.desc(), UserMemory.created_at.desc())
    ).all()
    return [
        {
            "id": memory.id,
            "status": memory.status,
            "kind": memory.kind,
            "category": memory.category,
            "content": memory.content,
        }
        for memory in memories
    ]


def memory_source_text(user_message: str, assistant_message: str) -> str:
    if assistant_message.strip():
        return f"User:\n{user_message.strip()}\n\nAssistant:\n{assistant_message.strip()}"
    return user_message.strip()


def process_memory_candidate(
    db: Session,
    user_id: str,
    candidate: MemoryCandidate,
    source_text: str,
) -> MemoryAction:
    normalized = normalize_memory_content(candidate.content)
    if not normalized:
        return MemoryAction("ignore", None, "", "empty memory candidate")
    content_hash = hash_content(normalized)
    existing = find_exact_memory(db, user_id, content_hash, statuses={"active", "pending"})
    if existing:
        return touch_exact_memory(db, existing, candidate)
    if candidate.confidence < PENDING_MEMORY_CONFIDENCE:
        return MemoryAction("ignore", None, candidate.content, "candidate confidence below threshold")
    if candidate.sensitivity != "low" or candidate.confidence < AUTO_MEMORY_CONFIDENCE:
        memory = create_memory_row(
            db,
            user_id,
            candidate.content,
            normalized,
            content_hash,
            resolve_memory_category(candidate),
            source_text,
            get_embedding_provider().embed_text(normalized),
            status="pending",
            kind=candidate.kind,
            extra_metadata={
                "confidence": candidate.confidence,
                "sensitivity": candidate.sensitivity,
                "decision": "pending_user_review",
            },
        )
        return MemoryAction("pending", memory.id, memory.content, "candidate requires user review")
    return upsert_memory_candidate(db, user_id, candidate, source_text=source_text)


def upsert_memory_candidate(db: Session, user_id: str, content: str | MemoryCandidate, source_text: str) -> MemoryAction:
    candidate = content if isinstance(content, MemoryCandidate) else MemoryCandidate(content=content)
    normalized = normalize_memory_content(candidate.content)
    content_hash = hash_content(normalized)
    existing_exact = find_exact_memory(db, user_id, content_hash, statuses={"active"})
    if existing_exact:
        return touch_exact_memory(db, existing_exact, candidate)

    category = resolve_memory_category(candidate)
    active_same_category = db.scalars(
        select(UserMemory).where(
            UserMemory.user_id == user_id,
            UserMemory.status == "active",
            UserMemory.category == category,
        )
    ).all()
    embedding = get_embedding_provider().embed_text(normalized)

    conflict = find_conflicting_memory(active_same_category, normalized, category)
    if conflict:
        new_memory = create_memory_row(
            db,
            user_id,
            candidate.content,
            normalized,
            content_hash,
            category,
            source_text,
            embedding,
            kind=candidate.kind,
            extra_metadata={
                "confidence": candidate.confidence,
                "sensitivity": candidate.sensitivity,
            },
        )
        conflict.status = "superseded"
        conflict.superseded_by_id = new_memory.id
        db.add(conflict)
        db.commit()
        db.refresh(new_memory)
        return MemoryAction("supersede", new_memory.id, new_memory.content, f"superseded {conflict.id}")

    similar = find_similar_memory(active_same_category, embedding, normalized)
    if similar:
        similar.content = merge_memory_content(similar.content, candidate.content)
        similar.normalized_content = normalize_memory_content(similar.content)
        similar.content_hash = hash_content(similar.normalized_content)
        similar.embedding = get_embedding_provider().embed_text(similar.normalized_content)
        similar.merge_count += 1
        similar.last_touched_at = datetime.now(timezone.utc)
        db.add(similar)
        db.commit()
        db.refresh(similar)
        return MemoryAction("merge", similar.id, similar.content, "semantic similarity above threshold")

    new_memory = create_memory_row(
        db,
        user_id,
        candidate.content,
        normalized,
        content_hash,
        category,
        source_text,
        embedding,
        kind=candidate.kind,
        extra_metadata={
            "confidence": candidate.confidence,
            "sensitivity": candidate.sensitivity,
        },
    )
    return MemoryAction("create", new_memory.id, new_memory.content, "new durable preference")


def retrieve_relevant_memories(db: Session, user_id: str, query: str, limit: int = 5) -> list[UserMemory]:
    active = db.scalars(
        select(UserMemory)
        .where(UserMemory.user_id == user_id, UserMemory.status == "active")
        .order_by(UserMemory.last_touched_at.desc())
    ).all()
    if not active:
        return []
    sticky = [memory for memory in active if memory.category in STICKY_MEMORY_CATEGORIES]
    non_sticky = [memory for memory in active if memory.category not in STICKY_MEMORY_CATEGORIES]
    if not non_sticky:
        return dedupe_memories(sticky)[:limit]
    query_embedding = get_embedding_provider().embed_text(query)
    scored = [
        (memory, cosine_similarity(query_embedding, memory.embedding or []))
        for memory in non_sticky
    ]
    scored.sort(key=lambda item: (item[1], item[0].last_touched_at), reverse=True)
    threshold = retrieval_similarity_threshold()
    semantic = [memory for memory, score in scored if score >= threshold]
    return dedupe_memories([*sticky, *semantic])[:limit]


def is_memory_recall_query(query: str) -> bool:
    normalized = " ".join(query.lower().split())
    return any(marker in normalized for marker in MEMORY_RECALL_MARKERS)


def build_memory_context_for_question(
    db: Session,
    user_id: str,
    query: str,
    conversation_id: str | None = None,
    preloaded_short_memory: list[dict] | None = None,
    preloaded_long_memories: list[dict] | None = None,
    conversation_summary: str | None = None,
) -> str:
    short_memory = preloaded_short_memory
    if short_memory is None:
        short_memory = get_short_term_memory(user_id, conversation_id)
    if not short_memory and conversation_id:
        short_memory = get_recent_db_messages(db, conversation_id)

    if preloaded_long_memories is None:
        memories = retrieve_relevant_memories(db, user_id, query)
        long_memories = [
            {
                "content": memory.content,
                "category": memory.category,
            }
            for memory in memories
        ]
    else:
        long_memories = preloaded_long_memories

    summary = conversation_summary
    if summary is None and conversation_id:
        conversation = db.get(Conversation, conversation_id)
        summary = conversation.summary if conversation else None

    return format_memory_context(long_memories, short_memory, summary)


def format_memory_context(
    long_memories: list[dict],
    short_memory: list[dict],
    conversation_summary: str | None,
) -> str:
    memories = "\n".join(f"- {item.get('content')}" for item in long_memories[:8]) or "- 无"
    recent = "\n".join(
        f"- {item.get('role')}: {item.get('content')}"
        for item in short_memory[-8:]
        if item.get("content")
    ) or "- 无"
    summary = conversation_summary or "无"
    return f"长期记忆:\n{memories}\n\n会话摘要:\n{summary}\n\n最近对话:\n{recent}"


def update_conversation_summary(
    db: Session,
    conversation: Conversation,
    user_message: str,
    assistant_message: str,
    user_id: str | None = None,
) -> str:
    previous = conversation.summary or ""
    text = (
        f"Existing summary:\n{previous}\n\n"
        f"User:\n{user_message}\n\nAssistant:\n{assistant_message}"
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
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation.summary or ""


def should_update_conversation_summary(db: Session, conversation_id: str) -> bool:
    message_count = db.scalar(select(func.count(Message.id)).where(Message.conversation_id == conversation_id)) or 0
    return message_count >= 10 and message_count % 4 == 0


def list_user_memories(db: Session, user_id: str, status: str | None = None) -> list[UserMemory]:
    query = select(UserMemory).where(UserMemory.user_id == user_id)
    if status:
        status = validate_memory_status(status)
        query = query.where(UserMemory.status == status)
    return db.scalars(query.order_by(UserMemory.updated_at.desc(), UserMemory.created_at.desc())).all()


def create_manual_memory(
    db: Session,
    user_id: str,
    content: str,
    category: str = "general",
    kind: str = "preference",
) -> UserMemory:
    action = upsert_memory_candidate(db, user_id, content, source_text="manual")
    memory = db.get(UserMemory, action.memory_id) if action.memory_id else None
    if memory is None:
        memory = create_memory_row(
            db,
            user_id,
            content,
            normalize_memory_content(content),
            hash_content(normalize_memory_content(content)),
            category,
            "manual",
            get_embedding_provider().embed_text(normalize_memory_content(content)),
        )
    memory.category = category or memory.category
    memory.kind = kind or memory.kind
    db.add(memory)
    db.commit()
    db.refresh(memory)
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
    if content is not None:
        normalized = normalize_memory_content(content)
        memory.content = content
        memory.normalized_content = normalized
        memory.content_hash = hash_content(normalized)
        memory.embedding = get_embedding_provider().embed_text(normalized)
    if status is not None:
        memory.status = validate_memory_status(status)
    if category is not None:
        memory.category = category
    if kind is not None:
        memory.kind = kind
    memory.last_touched_at = datetime.now(timezone.utc)
    db.add(memory)
    db.commit()
    db.refresh(memory)
    return memory


def delete_user_memory(db: Session, user_id: str, memory_id: str) -> None:
    memory = get_user_memory_or_404(db, user_id, memory_id)
    db.delete(memory)
    db.commit()


def get_user_memory_or_404(db: Session, user_id: str, memory_id: str) -> UserMemory:
    memory = db.get(UserMemory, memory_id)
    if memory is None or memory.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory not found")
    return memory


def validate_memory_status(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in ALLOWED_MEMORY_STATUSES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid memory status")
    return normalized


def to_memory_action_dict(action: MemoryAction) -> dict:
    return {
        "action": action.action,
        "memory_id": action.memory_id,
        "content": action.content,
        "reason": action.reason,
    }


def short_memory_key(user_id: str, conversation_id: str) -> str:
    return f"memory:short:{user_id}:{conversation_id}"


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


def find_conflicting_memory(memories: list[UserMemory], normalized: str, category: str) -> UserMemory | None:
    if category == "response_detail":
        wants_brief = any(marker in normalized for marker in ("简洁", "concise", "brief", "short"))
        wants_detail = any(marker in normalized for marker in ("详细", "detailed", "完整"))
        for memory in memories:
            old = memory.normalized_content
            old_brief = any(marker in old for marker in ("简洁", "concise", "brief", "short"))
            old_detail = any(marker in old for marker in ("详细", "detailed", "完整"))
            if (wants_brief and old_detail) or (wants_detail and old_brief):
                return memory
    return None


def find_similar_memory(memories: list[UserMemory], embedding: list[float], normalized: str = "") -> UserMemory | None:
    if normalized:
        same_direction = find_same_direction_preference(memories, normalized)
        if same_direction:
            return same_direction
    threshold = get_settings().memory_semantic_threshold
    best_memory = None
    best_score = 0.0
    for memory in memories:
        score = cosine_similarity(embedding, memory.embedding or [])
        if score > best_score:
            best_memory = memory
            best_score = score
    if best_memory is not None and best_score >= threshold:
        return best_memory
    return None


def find_same_direction_preference(memories: list[UserMemory], normalized: str) -> UserMemory | None:
    wants_brief = any(marker in normalized for marker in ("简洁", "concise", "brief", "short"))
    wants_detail = any(marker in normalized for marker in ("详细", "detailed", "完整"))
    for memory in memories:
        old = memory.normalized_content
        old_brief = any(marker in old for marker in ("简洁", "concise", "brief", "short"))
        old_detail = any(marker in old for marker in ("详细", "detailed", "完整"))
        if (wants_brief and old_brief) or (wants_detail and old_detail):
            return memory
    return None


def create_memory_row(
    db: Session,
    user_id: str,
    content: str,
    normalized: str,
    content_hash: str,
    category: str,
    source_text: str,
    embedding: list[float],
    status: str = "active",
    kind: str = "preference",
    extra_metadata: dict | None = None,
) -> UserMemory:
    memory = UserMemory(
        user_id=user_id,
        content=content,
        normalized_content=normalized,
        content_hash=content_hash,
        category=category,
        source_text=source_text,
        embedding=embedding,
        status=status,
        kind=kind,
        extra_metadata=extra_metadata or {},
    )
    db.add(memory)
    db.commit()
    db.refresh(memory)
    return memory


def merge_memory_content(existing: str, incoming: str) -> str:
    if incoming in existing:
        return existing
    return f"{existing}；{incoming}"


def find_exact_memory(
    db: Session,
    user_id: str,
    content_hash: str,
    statuses: set[str],
) -> UserMemory | None:
    return db.scalar(
        select(UserMemory).where(
            UserMemory.user_id == user_id,
            UserMemory.content_hash == content_hash,
            UserMemory.status.in_(statuses),
        )
    )


def touch_exact_memory(db: Session, memory: UserMemory, candidate: MemoryCandidate) -> MemoryAction:
    memory.touched_count += 1
    memory.last_touched_at = datetime.now(timezone.utc)
    memory.extra_metadata = {
        **(memory.extra_metadata or {}),
        "confidence": max(metadata_confidence(memory), candidate.confidence),
        "sensitivity": candidate.sensitivity,
    }
    reason = "exact content_hash match"
    if memory.status == "pending" and candidate.sensitivity == "low" and candidate.confidence >= AUTO_MEMORY_CONFIDENCE:
        memory.status = "active"
        memory.extra_metadata = {
            **memory.extra_metadata,
            "decision": "auto_activated_from_pending",
        }
        reason = "pending exact match promoted to active"
    db.add(memory)
    db.commit()
    db.refresh(memory)
    return MemoryAction("touch", memory.id, memory.content, reason)


def metadata_confidence(memory: UserMemory) -> float:
    try:
        return float((memory.extra_metadata or {}).get("confidence", 0))
    except (TypeError, ValueError):
        return 0.0


def resolve_memory_category(candidate: MemoryCandidate) -> str:
    category = (candidate.category or "").strip().lower()
    if category and category != "general":
        return category[:80]
    return infer_memory_category(normalize_memory_content(candidate.content))


def retrieval_similarity_threshold() -> float:
    return max(0.2, min(0.45, get_settings().memory_semantic_threshold * 0.35))


def dedupe_memories(memories: list[UserMemory]) -> list[UserMemory]:
    seen = set()
    result = []
    for memory in memories:
        if memory.id in seen:
            continue
        seen.add(memory.id)
        result.append(memory)
    return result


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)
