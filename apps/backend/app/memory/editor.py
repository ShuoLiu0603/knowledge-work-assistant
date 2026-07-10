from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.models.user_memory import UserMemory
from app.llm.provider import MemoryCandidate, MemoryOperation
from app.memory import commands, embedding, events, policy, repository, retrieval, vector_index
from app.memory.types import MemoryAction, MemoryEmbedding, MemorySource

ConflictReviewer = Callable[[MemoryOperation, list[UserMemory]], MemoryOperation | None]


def process_memory_operation(
    db: Session,
    user_id: str,
    operation: MemoryOperation,
    source: MemorySource,
    conflict_reviewer: ConflictReviewer | None = None,
    autocommit: bool = True,
    user_message: str | None = None,
) -> MemoryAction:
    if operation.action == "ignore":
        return MemoryAction("ignore", operation.target_memory_id, operation.content, operation.reason or "memory editor ignored")

    normalized = policy.normalize_memory_content(operation.content)
    if not normalized:
        return MemoryAction("ignore", operation.target_memory_id, "", "memory operation has no content")
    if not policy.is_evidence_grounded(operation.evidence, user_message):
        return MemoryAction(
            "ignore",
            operation.target_memory_id,
            operation.content,
            "memory evidence is not grounded in the user turn",
        )
    if policy.has_sensitive_memory_content(operation.content, operation.evidence):
        return MemoryAction(
            "ignore",
            operation.target_memory_id,
            operation.content,
            "sensitive memory requires explicit manual save",
        )

    safe_operation = policy.is_safe_memory_operation(operation, user_message=user_message)
    exact = repository.find_exact_memory(db, user_id, policy.hash_content(normalized), statuses={"active", "pending"})
    if exact and operation.action in {"create", "pending"}:
        if not safe_operation:
            return MemoryAction(
                "ignore",
                exact.id,
                exact.content,
                "memory operation is not eligible for automatic persistence",
            )
        return touch_exact_memory(
            db,
            exact,
            activate_pending=operation.action == "create",
            autocommit=autocommit,
        )

    if operation.action == "create":
        if safe_operation:
            return create_memory_from_operation_with_conflict_gate(
                db,
                user_id,
                operation,
                normalized,
                source,
                conflict_reviewer=conflict_reviewer,
                autocommit=autocommit,
                user_message=user_message,
            )
        return create_pending_memory_from_operation(db, user_id, operation, normalized, source, autocommit=autocommit)

    if operation.action == "pending":
        return create_pending_memory_from_operation(db, user_id, operation, normalized, source, autocommit=autocommit)

    target = get_operation_target(db, user_id, operation.target_memory_id)
    if target is None:
        return MemoryAction("ignore", None, operation.content, "target memory not found")
    if operation.expected_revision is not None and target.revision != operation.expected_revision:
        return MemoryAction(
            "ignore",
            target.id,
            operation.content,
            "target memory changed after review; stale operation skipped",
        )

    if operation.action == "update":
        return update_memory_from_operation(
            db,
            target,
            operation,
            normalized,
            source,
            autocommit=autocommit,
            user_message=user_message,
        )

    if operation.action == "supersede":
        return supersede_memory_from_operation(
            db,
            user_id,
            target,
            operation,
            normalized,
            source,
            autocommit=autocommit,
            user_message=user_message,
        )

    return MemoryAction("ignore", operation.target_memory_id, operation.content, "unsupported memory operation")


def update_memory_from_operation(
    db: Session,
    target: UserMemory,
    operation: MemoryOperation,
    normalized: str,
    source: MemorySource,
    autocommit: bool = True,
    user_message: str | None = None,
) -> MemoryAction:
    if not policy.can_auto_update(operation, user_message=user_message):
        return create_pending_memory_from_operation(db, target.user_id, operation, normalized, source, autocommit=autocommit)

    memory_embedding = embedding.embed_memory_text(normalized)
    previous_status = target.status
    category = policy.resolve_operation_category(operation)
    canonical_key = policy.canonical_key_for_operation(operation, category, normalized)
    operation_metadata = policy.memory_operation_metadata(operation, decision="auto_update")
    target.content = operation.content
    target.normalized_content = normalized
    target.content_hash = policy.hash_content(normalized)
    target.category = category
    target.kind = operation.kind
    target.canonical_key = canonical_key
    apply_memory_classification(target, operation.kind, target.category, operation_metadata)
    target.source_text = source.text
    target.source_conversation_id = source.conversation_id
    target.source_message_id = source.message_id
    target.embedding = memory_embedding.vector
    target.embedding_model = memory_embedding.model
    target.embedding_dimension = memory_embedding.dimension
    target.merge_count += 1
    target.revision += 1
    target.last_touched_at = datetime.now(timezone.utc)
    target.extra_metadata = operation_metadata
    if target.status == "pending":
        target.status = "active"
        target.invalid_at = None
    activation_conflicts: list[UserMemory] = []
    if target.status == "active":
        activation_conflicts = commands.supersede_activation_conflicts(
            db,
            target,
            reason=operation.reason or "memory editor update superseded conflicting active memory",
            payload={"operation": operation_metadata},
        )
    db.add(target)
    db.flush()
    events.record_memory_event(
        db,
        target,
        "update",
        reason=operation.reason or "memory editor updated memory",
        previous_status=previous_status,
        new_status=target.status,
        payload={
            "operation": operation_metadata,
            "superseded_conflict_ids": [conflict.id for conflict in activation_conflicts],
        },
    )
    if not autocommit:
        commands.queue_memory_vector_sync(db, target, *activation_conflicts)
        return MemoryAction("update", target.id, target.content, operation.reason or "memory editor updated memory")
    db.commit()
    db.refresh(target)
    for conflict in activation_conflicts:
        vector_index.try_sync_memory_vector(conflict)
    vector_index.try_sync_memory_vector(target)
    return MemoryAction("update", target.id, target.content, operation.reason or "memory editor updated memory")


def supersede_memory_from_operation(
    db: Session,
    user_id: str,
    target: UserMemory,
    operation: MemoryOperation,
    normalized: str,
    source: MemorySource,
    autocommit: bool = True,
    user_message: str | None = None,
) -> MemoryAction:
    if not policy.can_auto_supersede(operation, target.status, user_message=user_message):
        return create_pending_memory_from_operation(db, user_id, operation, normalized, source, autocommit=autocommit)

    previous_status = stage_memory_for_replacement(db, target)
    memory = create_memory_from_operation(
        db,
        user_id,
        operation,
        normalized,
        source,
        status="active",
        autocommit=autocommit,
    )
    target.superseded_by_id = memory.id
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
    if not autocommit:
        commands.queue_memory_vector_sync(db, target, memory)
        return MemoryAction("supersede", memory.id, memory.content, operation.reason or f"superseded {target.id}")
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
    decision: str | None = None,
    enforce_profile_singleton: bool = True,
    enforce_canonical_key_conflicts: bool = True,
    autocommit: bool = True,
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
        extra_metadata=policy.memory_operation_metadata(operation, decision=decision or f"auto_{operation.action}"),
        canonical_key=policy.canonical_key_for_operation(
            operation,
            policy.resolve_operation_category(operation),
            normalized,
        ),
        enforce_profile_singleton=enforce_profile_singleton,
        enforce_canonical_key_conflicts=enforce_canonical_key_conflicts,
        event_reason=operation.reason or f"memory editor {operation.action}",
        autocommit=autocommit,
    )


def create_memory_from_operation_with_conflict_gate(
    db: Session,
    user_id: str,
    operation: MemoryOperation,
    normalized: str,
    source: MemorySource,
    conflict_reviewer: ConflictReviewer | None = None,
    autocommit: bool = True,
    user_message: str | None = None,
) -> MemoryAction:
    category = policy.resolve_operation_category(operation)
    canonical_key = policy.canonical_key_for_operation(operation, category, normalized)
    content_hash = policy.hash_content(normalized)
    memory_embedding = embedding.embed_memory_text(normalized)
    conflict_candidates = list_conflict_candidates(db, user_id, category, canonical_key)
    active_candidates = [memory for memory in conflict_candidates if memory.status == "active"]

    canonical_conflict = find_canonical_key_conflict(conflict_candidates, canonical_key)
    profile_conflict = find_profile_singleton_activation_conflict(active_candidates, category)
    if canonical_conflict or profile_conflict:
        conflict = canonical_conflict or profile_conflict
        reviewed = review_and_apply_conflict_decision(
            db,
            user_id,
            operation,
            source,
            conflict_candidates,
            conflict_reviewer,
            autocommit=autocommit,
            user_message=user_message,
        )
        if reviewed is not None:
            return reviewed
        return create_pending_memory_from_operation(
            db,
            user_id,
            replace(
                operation,
                reason=operation.reason or f"possible conflict with memory {conflict.id}",
            ),
            normalized,
            source,
            autocommit=autocommit,
        )

    similar = find_similar_memory(active_candidates, memory_embedding.vector, normalized)
    if similar:
        return merge_similar_memory_from_operation(db, operation, source, similar, autocommit=autocommit)

    memory = commands.create_memory_row(
        db,
        user_id,
        operation.content,
        normalized,
        content_hash,
        category,
        source,
        memory_embedding,
        status="active",
        kind=operation.kind,
        extra_metadata=policy.memory_operation_metadata(operation, decision="auto_create"),
        canonical_key=canonical_key,
        event_reason=operation.reason or "memory editor created memory",
        autocommit=autocommit,
    )
    return MemoryAction("create", memory.id, memory.content, operation.reason or "memory editor created memory")


def review_and_apply_conflict_decision(
    db: Session,
    user_id: str,
    operation: MemoryOperation,
    source: MemorySource,
    conflict_candidates: list[UserMemory],
    conflict_reviewer: ConflictReviewer | None,
    autocommit: bool = True,
    user_message: str | None = None,
) -> MemoryAction | None:
    if conflict_reviewer is None:
        return None
    decision = conflict_reviewer(operation, conflict_candidates)
    if decision is None or decision.action == "create":
        return None
    if decision.action == "ignore":
        return MemoryAction("ignore", None, decision.content, decision.reason or "conflict reviewer ignored memory")

    normalized = policy.normalize_memory_content(decision.content)
    if not normalized:
        return MemoryAction("ignore", decision.target_memory_id, "", "conflict reviewer returned empty content")
    if not policy.is_evidence_grounded(decision.evidence, user_message):
        return MemoryAction(
            "ignore",
            decision.target_memory_id,
            decision.content,
            "memory evidence is not grounded in the user turn",
        )

    if decision.action == "pending":
        return create_pending_memory_from_operation(db, user_id, decision, normalized, source, autocommit=autocommit)

    candidate_ids = {memory.id for memory in conflict_candidates}
    if decision.target_memory_id not in candidate_ids:
        if not policy.is_evidence_grounded(operation.evidence, user_message):
            return MemoryAction(
                "ignore",
                operation.target_memory_id,
                operation.content,
                "memory evidence is not grounded in the user turn",
            )
        return create_pending_memory_from_operation(
            db,
            user_id,
            replace(
                operation,
                reason=operation.reason or "conflict reviewer did not target a provided memory",
            ),
            policy.normalize_memory_content(operation.content),
            source,
            autocommit=autocommit,
        )
    target = get_operation_target(db, user_id, decision.target_memory_id)
    if target is None:
        return None
    if decision.action == "update":
        return update_memory_from_operation(
            db,
            target,
            decision,
            normalized,
            source,
            autocommit=autocommit,
            user_message=user_message,
        )
    if decision.action == "supersede":
        return supersede_memory_from_operation(
            db,
            user_id,
            target,
            decision,
            normalized,
            source,
            autocommit=autocommit,
            user_message=user_message,
        )
    return None


def create_pending_memory_from_operation(
    db: Session,
    user_id: str,
    operation: MemoryOperation,
    normalized: str,
    source: MemorySource,
    autocommit: bool = True,
) -> MemoryAction:
    if (
        policy.normalize_sensitivity_level(operation.sensitivity) != "low"
        or policy.has_sensitive_memory_content(operation.content, operation.evidence)
    ):
        return MemoryAction(
            "ignore",
            operation.target_memory_id,
            operation.content,
            "sensitive memory requires explicit manual save",
        )
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
        canonical_key=policy.canonical_key_for_operation(
            operation,
            policy.resolve_operation_category(operation),
            normalized,
        ),
        event_type="pending",
        event_reason=operation.reason or "memory operation requires user review",
        autocommit=autocommit,
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
        return touch_exact_memory(db, existing)
    if (
        policy.normalize_sensitivity_level(candidate.sensitivity) != "low"
        or policy.has_sensitive_memory_content(candidate.content)
    ):
        return MemoryAction("ignore", None, candidate.content, "sensitive memory requires explicit manual save")
    return upsert_memory_candidate(db, user_id, candidate, source=source)


def upsert_memory_candidate(db: Session, user_id: str, content: str | MemoryCandidate, source: MemorySource) -> MemoryAction:
    candidate = content if isinstance(content, MemoryCandidate) else MemoryCandidate(content=content)
    normalized = policy.normalize_memory_content(candidate.content)
    if not normalized:
        return MemoryAction("ignore", None, "", "empty memory candidate")
    content_hash = policy.hash_content(normalized)
    existing_exact = repository.find_exact_memory(db, user_id, content_hash, statuses={"active"})
    if existing_exact:
        return touch_exact_memory(db, existing_exact)

    category = policy.resolve_memory_category(candidate)
    canonical_key = policy.canonical_key_for_candidate(candidate, category, normalized)
    conflict_candidates = list_conflict_candidates(db, user_id, category, canonical_key)
    active_same_category = [memory for memory in conflict_candidates if memory.status == "active"]
    memory_embedding = embedding.embed_memory_text(normalized)

    conflict = find_conflicting_memory(active_same_category, normalized, category)
    if conflict:
        return supersede_conflicting_memory(db, user_id, candidate, normalized, content_hash, category, source, memory_embedding, conflict)

    canonical_conflict = find_canonical_key_conflict(active_same_category, canonical_key)
    if canonical_conflict and policy.is_profile_singleton_category(category):
        return supersede_conflicting_memory(
            db,
            user_id,
            candidate,
            normalized,
            content_hash,
            category,
            source,
            memory_embedding,
            canonical_conflict,
        )

    similar = find_similar_memory(active_same_category, memory_embedding.vector, normalized)
    if similar:
        return merge_similar_memory(db, candidate, similar)

    if canonical_conflict:
        return supersede_conflicting_memory(
            db,
            user_id,
            candidate,
            normalized,
            content_hash,
            category,
            source,
            memory_embedding,
            canonical_conflict,
        )

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
            "sensitivity": candidate.sensitivity,
            "canonical_key": canonical_key,
            "memory_layer": policy.memory_layer_for_fields(candidate.kind, category),
            "profile_slot": policy.profile_slot_for_fields(candidate.kind, category),
        },
        canonical_key=canonical_key,
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
    canonical_key = policy.canonical_key_for_candidate(candidate, category, normalized)
    previous_status = stage_memory_for_replacement(db, conflict)
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
            "sensitivity": candidate.sensitivity,
            "canonical_key": canonical_key,
            "memory_layer": policy.memory_layer_for_fields(candidate.kind, category),
            "profile_slot": policy.profile_slot_for_fields(candidate.kind, category),
        },
        canonical_key=canonical_key,
        event_reason="new memory superseding conflicting preference",
    )
    conflict.superseded_by_id = new_memory.id
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


def supersede_conflicting_memory_from_operation(
    db: Session,
    user_id: str,
    operation: MemoryOperation,
    normalized: str,
    content_hash: str,
    source: MemorySource,
    conflict: UserMemory,
) -> MemoryAction:
    category = policy.resolve_operation_category(operation)
    canonical_key = policy.canonical_key_for_operation(operation, category, normalized)
    previous_status = stage_memory_for_replacement(db, conflict)
    new_memory = commands.create_memory_row(
        db,
        user_id,
        operation.content,
        normalized,
        content_hash,
        category,
        source,
        embedding.embed_memory_text(normalized),
        kind=operation.kind,
        extra_metadata=policy.memory_operation_metadata(operation, decision="auto_supersede_guard"),
        canonical_key=canonical_key,
        event_reason=operation.reason or "new memory superseding conflicting active memory",
    )
    conflict.superseded_by_id = new_memory.id
    db.add(conflict)
    db.flush()
    events.record_memory_event(
        db,
        conflict,
        "supersede",
        reason=operation.reason or f"superseded {conflict.id}",
        previous_status=previous_status,
        new_status=conflict.status,
        payload={
            "superseded_by_id": new_memory.id,
            "operation": policy.memory_operation_metadata(operation, decision="auto_supersede_guard"),
        },
    )
    db.commit()
    db.refresh(new_memory)
    vector_index.try_sync_memory_vector(conflict)
    return MemoryAction(
        "supersede",
        new_memory.id,
        new_memory.content,
        operation.reason or f"superseded conflicting memory {conflict.id}",
    )


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
    similar.revision += 1
    similar.last_touched_at = datetime.now(timezone.utc)
    apply_memory_classification(
        similar,
        candidate.kind,
        similar.category,
        {
            **(similar.extra_metadata or {}),
            "sensitivity": candidate.sensitivity,
            "canonical_key": policy.canonical_key_for_candidate(
                candidate,
                similar.category,
                similar.normalized_content,
            ),
        },
    )
    similar.extra_metadata = {
        **(similar.extra_metadata or {}),
        "sensitivity": candidate.sensitivity,
        "canonical_key": similar.canonical_key,
        "memory_layer": policy.memory_layer_for_fields(candidate.kind, similar.category),
        "profile_slot": policy.profile_slot_for_fields(candidate.kind, similar.category),
    }
    activation_conflicts: list[UserMemory] = []
    if similar.status == "active":
        activation_conflicts = commands.supersede_activation_conflicts(
            db,
            similar,
            reason="memory merge superseded conflicting active memory",
            payload={"previous_content_hash": previous_content_hash},
        )
    db.add(similar)
    db.flush()
    events.record_memory_event(
        db,
        similar,
        "merge",
        reason="semantic similarity above threshold",
        previous_status=previous_status,
        new_status=similar.status,
        payload={
            "previous_content_hash": previous_content_hash,
            "superseded_conflict_ids": [conflict.id for conflict in activation_conflicts],
        },
    )
    db.commit()
    db.refresh(similar)
    for conflict in activation_conflicts:
        vector_index.try_sync_memory_vector(conflict)
    vector_index.try_sync_memory_vector(similar)
    return MemoryAction("merge", similar.id, similar.content, "semantic similarity above threshold")


def merge_similar_memory_from_operation(
    db: Session,
    operation: MemoryOperation,
    source: MemorySource,
    similar: UserMemory,
    autocommit: bool = True,
) -> MemoryAction:
    previous_status = similar.status
    previous_content_hash = similar.content_hash
    operation_metadata = policy.memory_operation_metadata(operation, decision="auto_merge")
    similar.content = merge_memory_content(similar.content, operation.content)
    similar.normalized_content = policy.normalize_memory_content(similar.content)
    similar.content_hash = policy.hash_content(similar.normalized_content)
    merged_embedding = embedding.embed_memory_text(similar.normalized_content)
    similar.category = policy.resolve_operation_category(operation)
    similar.kind = operation.kind
    apply_memory_classification(similar, operation.kind, similar.category, operation_metadata)
    similar.source_text = source.text
    similar.source_conversation_id = source.conversation_id
    similar.source_message_id = source.message_id
    similar.embedding = merged_embedding.vector
    similar.embedding_model = merged_embedding.model
    similar.embedding_dimension = merged_embedding.dimension
    similar.merge_count += 1
    similar.revision += 1
    similar.last_touched_at = datetime.now(timezone.utc)
    similar.extra_metadata = {
        **(similar.extra_metadata or {}),
        **operation_metadata,
    }
    activation_conflicts: list[UserMemory] = []
    if similar.status == "active":
        activation_conflicts = commands.supersede_activation_conflicts(
            db,
            similar,
            reason=operation.reason or "memory editor merge superseded conflicting active memory",
            payload={"operation": operation_metadata},
        )
    db.add(similar)
    db.flush()
    events.record_memory_event(
        db,
        similar,
        "merge",
        reason=operation.reason or "memory editor create merged with similar active memory",
        previous_status=previous_status,
        new_status=similar.status,
        payload={
            "previous_content_hash": previous_content_hash,
            "operation": operation_metadata,
            "superseded_conflict_ids": [conflict.id for conflict in activation_conflicts],
        },
    )
    if not autocommit:
        commands.queue_memory_vector_sync(db, similar, *activation_conflicts)
        return MemoryAction(
            "merge",
            similar.id,
            similar.content,
            operation.reason or "memory editor create merged with similar active memory",
        )
    db.commit()
    db.refresh(similar)
    for conflict in activation_conflicts:
        vector_index.try_sync_memory_vector(conflict)
    vector_index.try_sync_memory_vector(similar)
    return MemoryAction(
        "merge",
        similar.id,
        similar.content,
        operation.reason or "memory editor create merged with similar active memory",
    )


def get_operation_target(db: Session, user_id: str, memory_id: str | None) -> UserMemory | None:
    if not memory_id:
        return None
    return repository.get_user_memory(db, user_id, memory_id)


def stage_memory_for_replacement(db: Session, memory: UserMemory) -> str:
    previous_status = memory.status
    now = datetime.now(timezone.utc)
    memory.status = "superseded"
    memory.invalid_at = now
    memory.last_touched_at = now
    memory.revision += 1
    db.add(memory)
    db.flush([memory])
    return previous_status


def candidate_from_operation(operation: MemoryOperation) -> MemoryCandidate:
    return MemoryCandidate(
        content=operation.content,
        kind=operation.kind,
        category=operation.category,
        canonical_key=operation.canonical_key,
        sensitivity=operation.sensitivity,
    )


def apply_memory_classification(memory: UserMemory, kind: str, category: str, metadata: dict | None = None) -> None:
    metadata = metadata or {}
    layer = policy.memory_layer_for_fields(kind, category, metadata)
    memory.memory_layer = layer
    memory.profile_slot = policy.profile_slot_for_fields(kind, category)
    memory.canonical_key = policy.canonical_key_for_fields(
        kind=kind,
        category=category,
        normalized_content=memory.normalized_content,
        explicit_key=metadata.get("canonical_key") or memory.canonical_key,
    )
    memory.pinned = policy.pinned_for_layer(layer)
    if not memory.scope_type:
        memory.scope_type = "user"
    if not memory.scope_id:
        memory.scope_id = memory.user_id


def list_conflict_candidates(
    db: Session,
    user_id: str,
    category: str,
    canonical_key: str,
) -> list[UserMemory]:
    return retrieval.dedupe_memories(
        [
            *repository.list_active_or_pending_memories_by_canonical_key(db, user_id, canonical_key),
            *repository.list_active_or_pending_memories_by_category(db, user_id, category),
        ]
    )


def touch_exact_memory(db: Session, memory: UserMemory, activate_pending: bool = False, autocommit: bool = True) -> MemoryAction:
    touched, reason = commands.touch_memory(
        db,
        memory,
        sensitivity=str((memory.extra_metadata or {}).get("sensitivity") or "low"),
        activate_pending=activate_pending,
        autocommit=autocommit,
    )
    return MemoryAction("touch", touched.id, touched.content, reason)


def find_conflicting_memory(memories: list[UserMemory], normalized: str, category: str) -> UserMemory | None:
    if category == "response_detail":
        wants_brief = has_brief_direction(normalized)
        wants_detail = has_detailed_direction(normalized)
        for memory in memories:
            old = memory.normalized_content
            old_brief = has_brief_direction(old)
            old_detail = has_detailed_direction(old)
            if (wants_brief and old_detail) or (wants_detail and old_brief):
                return memory

    if category == "language":
        wanted_language = language_direction(normalized)
        for memory in memories:
            old_language = language_direction(memory.normalized_content)
            if wanted_language and old_language and wanted_language != old_language:
                return memory

    return None


def find_profile_singleton_activation_conflict(memories: list[UserMemory], category: str) -> UserMemory | None:
    if not policy.is_profile_singleton_category(category):
        return None
    for memory in memories:
        if memory.memory_layer == "profile" and policy.is_profile_singleton_slot(memory.profile_slot):
            return memory
    return memories[0] if memories else None


def find_canonical_key_conflict(memories: list[UserMemory], canonical_key: str) -> UserMemory | None:
    if not canonical_key:
        return None
    for memory in memories:
        if memory.canonical_key == canonical_key:
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
    wants_brief = has_brief_direction(normalized)
    wants_detail = has_detailed_direction(normalized)
    wanted_language = language_direction(normalized)
    for memory in memories:
        old = memory.normalized_content
        old_brief = has_brief_direction(old)
        old_detail = has_detailed_direction(old)
        old_language = language_direction(old)
        if (wants_brief and old_brief) or (wants_detail and old_detail):
            return memory
        if wanted_language and old_language and wanted_language == old_language:
            return memory
    return None


def has_brief_direction(text: str) -> bool:
    return any(marker in text for marker in policy.RESPONSE_BRIEF_MARKERS)


def has_detailed_direction(text: str) -> bool:
    return any(marker in text for marker in policy.RESPONSE_DETAILED_MARKERS)


def language_direction(text: str) -> str:
    if any(marker in text for marker in ("\u4e2d\u6587", "\u6c49\u8bed", "chinese", "mandarin")):
        return "chinese"
    if any(marker in text for marker in ("\u82f1\u6587", "\u82f1\u8bed", "english")):
        return "english"
    return ""


def merge_memory_content(existing: str, incoming: str) -> str:
    if incoming in existing:
        return existing
    return f"{existing}; {incoming}"
