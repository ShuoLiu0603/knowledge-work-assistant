from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.models.user_memory import UserMemory
from app.llm.provider import MemoryCandidate, MemoryOperation
from app.memory import commands, embedding, events, policy, repository, retrieval, vector_index
from app.memory.types import MemoryAction, MemoryEmbedding, MemorySource


def process_memory_operation(
    db: Session,
    user_id: str,
    operation: MemoryOperation,
    source: MemorySource,
) -> MemoryAction:
    if operation.action == "ignore":
        return MemoryAction("ignore", operation.target_memory_id, operation.content, operation.reason or "memory editor ignored")

    normalized = policy.normalize_memory_content(operation.content)
    if not normalized:
        return MemoryAction("ignore", operation.target_memory_id, "", "memory operation has no content")

    candidate = candidate_from_operation(operation)
    exact = repository.find_exact_memory(db, user_id, policy.hash_content(normalized), statuses={"active", "pending"})
    if exact and operation.action in {"create", "pending"}:
        return touch_exact_memory(db, exact, candidate)

    if operation.action == "create":
        if policy.can_auto_create(operation):
            memory = create_memory_from_operation(db, user_id, operation, normalized, source, status="active")
            return MemoryAction("create", memory.id, memory.content, operation.reason or "memory editor created memory")
        return create_pending_memory_from_operation(db, user_id, operation, normalized, source)

    if operation.action == "pending":
        return create_pending_memory_from_operation(db, user_id, operation, normalized, source)

    target = get_operation_target(db, user_id, operation.target_memory_id)
    if target is None:
        return MemoryAction("ignore", None, operation.content, "target memory not found")

    if operation.action == "update":
        return update_memory_from_operation(db, target, operation, normalized, source)

    if operation.action == "supersede":
        return supersede_memory_from_operation(db, user_id, target, operation, normalized, source)

    return MemoryAction("ignore", operation.target_memory_id, operation.content, "unsupported memory operation")


def update_memory_from_operation(
    db: Session,
    target: UserMemory,
    operation: MemoryOperation,
    normalized: str,
    source: MemorySource,
) -> MemoryAction:
    if not policy.can_auto_update(operation):
        return create_pending_memory_from_operation(db, target.user_id, operation, normalized, source)

    memory_embedding = embedding.embed_memory_text(normalized)
    previous_status = target.status
    target.content = operation.content
    target.normalized_content = normalized
    target.content_hash = policy.hash_content(normalized)
    target.category = policy.resolve_operation_category(operation)
    target.kind = operation.kind
    target.source_text = source.text
    target.source_conversation_id = source.conversation_id
    target.source_message_id = source.message_id
    target.embedding = memory_embedding.vector
    target.embedding_model = memory_embedding.model
    target.embedding_dimension = memory_embedding.dimension
    target.merge_count += 1
    target.last_touched_at = datetime.now(timezone.utc)
    target.extra_metadata = policy.memory_operation_metadata(operation, decision="auto_update")
    if target.status == "pending":
        target.status = "active"
        target.invalid_at = None
    db.add(target)
    db.flush()
    events.record_memory_event(
        db,
        target,
        "update",
        reason=operation.reason or "memory editor updated memory",
        previous_status=previous_status,
        new_status=target.status,
        payload={"operation": policy.memory_operation_metadata(operation, decision="auto_update")},
    )
    db.commit()
    db.refresh(target)
    vector_index.try_sync_memory_vector(target)
    return MemoryAction("update", target.id, target.content, operation.reason or "memory editor updated memory")


def supersede_memory_from_operation(
    db: Session,
    user_id: str,
    target: UserMemory,
    operation: MemoryOperation,
    normalized: str,
    source: MemorySource,
) -> MemoryAction:
    if not policy.can_auto_supersede(operation, target.status):
        return create_pending_memory_from_operation(db, user_id, operation, normalized, source)

    memory = create_memory_from_operation(db, user_id, operation, normalized, source, status="active")
    previous_status = target.status
    target.status = "superseded"
    target.superseded_by_id = memory.id
    target.invalid_at = datetime.now(timezone.utc)
    target.last_touched_at = datetime.now(timezone.utc)
    db.add(target)
    db.flush()
    events.record_memory_event(
        db,
        target,
        "supersede",
        reason=operation.reason or f"superseded {target.id}",
        previous_status=previous_status,
        new_status=target.status,
        payload={
            "superseded_by_id": memory.id,
            "operation": policy.memory_operation_metadata(operation, decision="auto_supersede"),
        },
    )
    db.commit()
    db.refresh(memory)
    vector_index.try_sync_memory_vector(target)
    return MemoryAction("supersede", memory.id, memory.content, operation.reason or f"superseded {target.id}")


def create_memory_from_operation(
    db: Session,
    user_id: str,
    operation: MemoryOperation,
    normalized: str,
    source: MemorySource,
    status: str,
) -> UserMemory:
    return commands.create_memory_row(
        db,
        user_id,
        operation.content,
        normalized,
        policy.hash_content(normalized),
        policy.resolve_operation_category(operation),
        source,
        embedding.embed_memory_text(normalized),
        status=status,
        kind=operation.kind,
        extra_metadata=policy.memory_operation_metadata(operation, decision=f"auto_{operation.action}"),
        event_reason=operation.reason or f"memory editor {operation.action}",
    )


def create_pending_memory_from_operation(
    db: Session,
    user_id: str,
    operation: MemoryOperation,
    normalized: str,
    source: MemorySource,
) -> MemoryAction:
    if operation.sensitivity != "low":
        return MemoryAction(
            "ignore",
            operation.target_memory_id,
            operation.content,
            "sensitive memory requires explicit manual save",
        )
    if operation.action != "pending" and operation.confidence < policy.PENDING_OPERATION_CONFIDENCE:
        return MemoryAction("ignore", operation.target_memory_id, operation.content, "memory operation confidence below threshold")
    memory = commands.create_memory_row(
        db,
        user_id,
        operation.content,
        normalized,
        policy.hash_content(normalized),
        policy.resolve_operation_category(operation),
        source,
        embedding.embed_memory_text(normalized),
        status="pending",
        kind=operation.kind,
        extra_metadata=policy.memory_operation_metadata(operation, decision="pending_user_review"),
        event_type="pending",
        event_reason=operation.reason or "memory operation requires user review",
    )
    return MemoryAction("pending", memory.id, memory.content, operation.reason or "memory operation requires user review")


def process_memory_candidate(
    db: Session,
    user_id: str,
    candidate: MemoryCandidate,
    source: MemorySource,
) -> MemoryAction:
    normalized = policy.normalize_memory_content(candidate.content)
    if not normalized:
        return MemoryAction("ignore", None, "", "empty memory candidate")
    content_hash = policy.hash_content(normalized)
    existing = repository.find_exact_memory(db, user_id, content_hash, statuses={"active", "pending"})
    if existing:
        return touch_exact_memory(db, existing, candidate)
    if candidate.confidence < policy.PENDING_MEMORY_CONFIDENCE:
        return MemoryAction("ignore", None, candidate.content, "candidate confidence below threshold")
    if candidate.sensitivity != "low":
        return MemoryAction("ignore", None, candidate.content, "sensitive memory requires explicit manual save")
    if candidate.confidence < policy.AUTO_MEMORY_CONFIDENCE:
        memory = commands.create_memory_row(
            db,
            user_id,
            candidate.content,
            normalized,
            content_hash,
            policy.resolve_memory_category(candidate),
            source,
            embedding.embed_memory_text(normalized),
            status="pending",
            kind=candidate.kind,
            extra_metadata={
                "confidence": candidate.confidence,
                "sensitivity": candidate.sensitivity,
                "decision": "pending_user_review",
            },
            event_type="pending",
            event_reason="candidate requires user review",
        )
        return MemoryAction("pending", memory.id, memory.content, "candidate requires user review")
    return upsert_memory_candidate(db, user_id, candidate, source=source)


def upsert_memory_candidate(db: Session, user_id: str, content: str | MemoryCandidate, source: MemorySource) -> MemoryAction:
    candidate = content if isinstance(content, MemoryCandidate) else MemoryCandidate(content=content)
    normalized = policy.normalize_memory_content(candidate.content)
    if not normalized:
        return MemoryAction("ignore", None, "", "empty memory candidate")
    content_hash = policy.hash_content(normalized)
    existing_exact = repository.find_exact_memory(db, user_id, content_hash, statuses={"active"})
    if existing_exact:
        return touch_exact_memory(db, existing_exact, candidate)

    category = policy.resolve_memory_category(candidate)
    active_same_category = repository.list_active_memories_by_category(db, user_id, category)
    memory_embedding = embedding.embed_memory_text(normalized)

    conflict = find_conflicting_memory(active_same_category, normalized, category)
    if conflict:
        return supersede_conflicting_memory(db, user_id, candidate, normalized, content_hash, category, source, memory_embedding, conflict)

    similar = find_similar_memory(active_same_category, memory_embedding.vector, normalized)
    if similar:
        return merge_similar_memory(db, candidate, similar)

    new_memory = commands.create_memory_row(
        db,
        user_id,
        candidate.content,
        normalized,
        content_hash,
        category,
        source,
        memory_embedding,
        kind=candidate.kind,
        extra_metadata={
            "confidence": candidate.confidence,
            "sensitivity": candidate.sensitivity,
        },
        event_reason="new durable preference",
    )
    return MemoryAction("create", new_memory.id, new_memory.content, "new durable preference")


def supersede_conflicting_memory(
    db: Session,
    user_id: str,
    candidate: MemoryCandidate,
    normalized: str,
    content_hash: str,
    category: str,
    source: MemorySource,
    memory_embedding: MemoryEmbedding,
    conflict: UserMemory,
) -> MemoryAction:
    new_memory = commands.create_memory_row(
        db,
        user_id,
        candidate.content,
        normalized,
        content_hash,
        category,
        source,
        memory_embedding,
        kind=candidate.kind,
        extra_metadata={
            "confidence": candidate.confidence,
            "sensitivity": candidate.sensitivity,
        },
        event_reason="new memory superseding conflicting preference",
    )
    previous_status = conflict.status
    conflict.status = "superseded"
    conflict.superseded_by_id = new_memory.id
    conflict.invalid_at = datetime.now(timezone.utc)
    db.add(conflict)
    db.flush()
    events.record_memory_event(
        db,
        conflict,
        "supersede",
        reason=f"superseded {conflict.id}",
        previous_status=previous_status,
        new_status=conflict.status,
        payload={"superseded_by_id": new_memory.id},
    )
    db.commit()
    db.refresh(new_memory)
    vector_index.try_sync_memory_vector(conflict)
    return MemoryAction("supersede", new_memory.id, new_memory.content, f"superseded {conflict.id}")


def merge_similar_memory(db: Session, candidate: MemoryCandidate, similar: UserMemory) -> MemoryAction:
    previous_status = similar.status
    previous_content_hash = similar.content_hash
    similar.content = merge_memory_content(similar.content, candidate.content)
    similar.normalized_content = policy.normalize_memory_content(similar.content)
    similar.content_hash = policy.hash_content(similar.normalized_content)
    merged_embedding = embedding.embed_memory_text(similar.normalized_content)
    similar.embedding = merged_embedding.vector
    similar.embedding_model = merged_embedding.model
    similar.embedding_dimension = merged_embedding.dimension
    similar.merge_count += 1
    similar.last_touched_at = datetime.now(timezone.utc)
    db.add(similar)
    db.flush()
    events.record_memory_event(
        db,
        similar,
        "merge",
        reason="semantic similarity above threshold",
        previous_status=previous_status,
        new_status=similar.status,
        payload={"previous_content_hash": previous_content_hash},
    )
    db.commit()
    db.refresh(similar)
    vector_index.try_sync_memory_vector(similar)
    return MemoryAction("merge", similar.id, similar.content, "semantic similarity above threshold")


def get_operation_target(db: Session, user_id: str, memory_id: str | None) -> UserMemory | None:
    if not memory_id:
        return None
    return repository.get_user_memory(db, user_id, memory_id)


def candidate_from_operation(operation: MemoryOperation) -> MemoryCandidate:
    return MemoryCandidate(
        content=operation.content,
        kind=operation.kind,
        category=operation.category,
        confidence=operation.confidence,
        sensitivity=operation.sensitivity,
    )


def touch_exact_memory(db: Session, memory: UserMemory, candidate: MemoryCandidate) -> MemoryAction:
    touched, reason = commands.touch_memory(
        db,
        memory,
        confidence=candidate.confidence,
        sensitivity=candidate.sensitivity,
        existing_confidence=policy.metadata_confidence(memory),
        auto_promote_confidence=policy.AUTO_MEMORY_CONFIDENCE,
    )
    return MemoryAction("touch", touched.id, touched.content, reason)


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


def find_similar_memory(memories: list[UserMemory], memory_embedding: list[float], normalized: str = "") -> UserMemory | None:
    if normalized:
        same_direction = find_same_direction_preference(memories, normalized)
        if same_direction:
            return same_direction
    return retrieval.find_similar_memory(
        memories,
        memory_embedding,
        threshold=policy.semantic_similarity_threshold(),
    )


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


def merge_memory_content(existing: str, incoming: str) -> str:
    if incoming in existing:
        return existing
    return f"{existing}；{incoming}"
