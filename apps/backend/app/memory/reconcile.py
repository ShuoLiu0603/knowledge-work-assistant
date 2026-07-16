from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from itertools import combinations
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models.user_memory import UserMemory
from app.memory import events, policy, retrieval, vector_index


@dataclass(frozen=True)
class MemoryReconcileFinding:
    finding_type: str
    severity: str
    memory_id: str
    related_memory_id: str | None
    proposed_action: str
    reason: str
    applied: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MemoryReconcileReport:
    user_id: str
    apply: bool
    scanned_count: int
    applied_count: int
    findings: list[MemoryReconcileFinding]


def reconcile_user_memories(
    db: Session,
    user_id: str,
    *,
    apply: bool = False,
    now: datetime | None = None,
    max_semantic_pairs: int | None = None,
    include_semantic_candidates: bool = False,
) -> MemoryReconcileReport:
    max_semantic_pairs = max_semantic_pairs or get_settings().memory_reconcile_max_semantic_pairs
    now = now or datetime.now(timezone.utc)
    memories = list_reconcile_candidates(db, user_id)
    findings: list[MemoryReconcileFinding] = []
    changed: list[UserMemory] = []

    expired_ids = set()
    for memory in memories:
        expires_at = aware_datetime(memory.expires_at)
        if expires_at is None or expires_at > now or memory.status not in {"active", "pending"}:
            continue
        expired_ids.add(memory.id)
        applied = False
        if apply:
            expire_memory(db, memory, now)
            changed.append(memory)
            applied = True
        findings.append(
            MemoryReconcileFinding(
                finding_type="expired_memory",
                severity="medium",
                memory_id=memory.id,
                related_memory_id=None,
                proposed_action="expire",
                reason="memory expires_at is in the past",
                applied=applied,
                metadata={"expires_at": expires_at.isoformat()},
            )
        )

    active_or_pending = [
        memory
        for memory in memories
        if memory.status in {"active", "pending"} and memory.id not in expired_ids
    ]
    findings.extend(reconcile_exact_duplicates(db, active_or_pending, apply=apply, changed=changed, now=now))
    findings.extend(reconcile_profile_singletons(db, active_or_pending, apply=apply, changed=changed, now=now))
    if include_semantic_candidates:
        findings.extend(find_semantic_relation_candidates(active_or_pending, max_pairs=max_semantic_pairs))

    if apply and changed:
        db.commit()
        for memory in changed:
            db.refresh(memory)
            vector_index.try_sync_memory_vector(memory)
    findings.extend(reconcile_vector_index(db, user_id, apply=apply))

    return MemoryReconcileReport(
        user_id=user_id,
        apply=apply,
        scanned_count=len(memories),
        applied_count=sum(1 for finding in findings if finding.applied),
        findings=findings,
    )


def list_reconcile_candidates(db: Session, user_id: str) -> list[UserMemory]:
    return db.scalars(
        select(UserMemory)
        .where(
            UserMemory.user_id == user_id,
            UserMemory.status.in_(("active", "pending")),
        )
        .order_by(UserMemory.last_touched_at.desc(), UserMemory.updated_at.desc(), UserMemory.created_at.desc())
    ).all()


def list_vector_reconcile_candidates(db: Session, user_id: str) -> list[UserMemory]:
    return db.scalars(
        select(UserMemory)
        .where(UserMemory.user_id == user_id)
        .order_by(UserMemory.last_touched_at.desc(), UserMemory.updated_at.desc(), UserMemory.created_at.desc())
    ).all()


def reconcile_vector_index(db: Session, user_id: str, *, apply: bool) -> list[MemoryReconcileFinding]:
    if not vector_index.is_memory_vector_index_enabled():
        return []

    memories = list_vector_reconcile_candidates(db, user_id)
    if not memories:
        return []

    try:
        payloads = vector_index.get_memory_vector_payloads([memory.id for memory in memories])
    except Exception as exc:
        return [
            MemoryReconcileFinding(
                finding_type="vector_index_unavailable",
                severity="medium",
                memory_id=memories[0].id,
                related_memory_id=None,
                proposed_action="retry_vector_reconcile",
                reason="memory vector index could not be inspected",
                applied=False,
                metadata={"error": str(exc)},
            )
        ]
    findings: list[MemoryReconcileFinding] = []
    applied_events = 0
    for memory in memories:
        payload = payloads.get(memory.id)
        expected_present = should_memory_have_vector(memory)
        if expected_present and payload is None:
            applied = sync_memory_vector_from_reconcile(db, memory, apply=apply, reason="memory vector is missing")
            applied_events += int(applied)
            findings.append(
                MemoryReconcileFinding(
                    finding_type="missing_vector",
                    severity="medium",
                    memory_id=memory.id,
                    related_memory_id=None,
                    proposed_action="sync_vector",
                    reason="memory should have a vector index point but none was found",
                    applied=applied,
                    metadata={"status": memory.status, "revision": memory.revision},
                )
            )
            continue

        if not expected_present and payload is not None:
            applied = delete_memory_vector_from_reconcile(db, memory, apply=apply, reason="memory vector should be absent")
            applied_events += int(applied)
            findings.append(
                MemoryReconcileFinding(
                    finding_type="stale_vector",
                    severity="medium",
                    memory_id=memory.id,
                    related_memory_id=None,
                    proposed_action="delete_vector",
                    reason="memory should not have a vector index point",
                    applied=applied,
                    metadata={"status": memory.status, "revision": memory.revision},
                )
            )
            continue

        if expected_present and payload is not None:
            mismatches = vector_payload_mismatches(memory, payload)
            if not mismatches:
                continue
            applied = sync_memory_vector_from_reconcile(db, memory, apply=apply, reason="memory vector payload is stale")
            applied_events += int(applied)
            findings.append(
                MemoryReconcileFinding(
                    finding_type="stale_vector_payload",
                    severity="low",
                    memory_id=memory.id,
                    related_memory_id=None,
                    proposed_action="sync_vector",
                    reason="memory vector payload does not match the database row",
                    applied=applied,
                    metadata={"mismatches": mismatches},
                )
            )

    if apply and applied_events:
        db.commit()
    return findings


def should_memory_have_vector(memory: UserMemory) -> bool:
    return vector_index.should_index_memory(memory)


def vector_payload_mismatches(memory: UserMemory, payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    expected = vector_index.memory_payload(memory)
    checked_fields = (
        "status",
        "kind",
        "category",
        "canonical_key",
        "memory_layer",
        "profile_slot",
        "scope_type",
        "scope_id",
        "pinned",
        "revision",
        "expires_at",
        "content_hash",
        "embedding_model",
        "embedding_dimension",
    )
    return {
        field_name: {"expected": expected.get(field_name), "actual": payload.get(field_name)}
        for field_name in checked_fields
        if payload.get(field_name) != expected.get(field_name)
    }


def sync_memory_vector_from_reconcile(db: Session, memory: UserMemory, *, apply: bool, reason: str) -> bool:
    if not apply:
        return False
    if not vector_index.try_sync_memory_vector(memory):
        return False
    events.record_memory_event(
        db,
        memory,
        "vector_sync",
        reason=reason,
        previous_status=memory.status,
        new_status=memory.status,
        payload={"vector_reconcile": True},
    )
    return True


def delete_memory_vector_from_reconcile(db: Session, memory: UserMemory, *, apply: bool, reason: str) -> bool:
    if not apply:
        return False
    if not vector_index.try_delete_memory_vector(memory.id):
        return False
    events.record_memory_event(
        db,
        memory,
        "vector_delete",
        reason=reason,
        previous_status=memory.status,
        new_status=memory.status,
        payload={"vector_reconcile": True},
    )
    return True


def reconcile_exact_duplicates(
    db: Session,
    memories: list[UserMemory],
    *,
    apply: bool,
    changed: list[UserMemory],
    now: datetime,
) -> list[MemoryReconcileFinding]:
    findings: list[MemoryReconcileFinding] = []
    groups: dict[tuple[str, str, str], list[UserMemory]] = {}
    for memory in memories:
        if not memory.content_hash:
            continue
        key = (memory.scope_type or "user", memory.scope_id or memory.user_id, memory.content_hash)
        groups.setdefault(key, []).append(memory)

    for group in groups.values():
        if len(group) < 2:
            continue
        winner = choose_memory_winner(group)
        for duplicate in group:
            if duplicate.id == winner.id:
                continue
            applied = False
            if apply and duplicate.status in {"active", "pending"}:
                supersede_or_ignore_duplicate(db, duplicate, winner, now, reason="exact duplicate memory")
                changed.append(duplicate)
                applied = True
            findings.append(
                MemoryReconcileFinding(
                    finding_type="exact_duplicate",
                    severity="high",
                    memory_id=duplicate.id,
                    related_memory_id=winner.id,
                    proposed_action="supersede" if duplicate.status == "active" else "ignore",
                    reason="same normalized content hash as another memory",
                    applied=applied,
                    metadata={"content_hash": duplicate.content_hash},
                )
            )
    return findings


def reconcile_profile_singletons(
    db: Session,
    memories: list[UserMemory],
    *,
    apply: bool,
    changed: list[UserMemory],
    now: datetime,
) -> list[MemoryReconcileFinding]:
    findings: list[MemoryReconcileFinding] = []
    groups: dict[tuple[str, str, str], list[UserMemory]] = {}
    for memory in memories:
        if memory.status != "active" or memory.memory_layer != "profile":
            continue
        if not policy.is_profile_singleton_slot(memory.profile_slot):
            continue
        key = (memory.scope_type or "user", memory.scope_id or memory.user_id, memory.profile_slot)
        groups.setdefault(key, []).append(memory)

    for group in groups.values():
        if len(group) < 2:
            continue
        winner = choose_memory_winner(group)
        for duplicate in group:
            if duplicate.id == winner.id:
                continue
            applied = False
            if apply:
                mark_superseded(db, duplicate, winner, now, reason="profile singleton duplicate")
                changed.append(duplicate)
                applied = True
            findings.append(
                MemoryReconcileFinding(
                    finding_type="profile_singleton_duplicate",
                    severity="critical",
                    memory_id=duplicate.id,
                    related_memory_id=winner.id,
                    proposed_action="supersede",
                    reason="only one active profile singleton memory is allowed for this slot",
                    applied=applied,
                    metadata={"profile_slot": duplicate.profile_slot},
                )
            )
    return findings


def find_semantic_relation_candidates(memories: list[UserMemory], *, max_pairs: int) -> list[MemoryReconcileFinding]:
    semantic = [
        memory
        for memory in memories
        if memory.status == "active"
        and memory.memory_layer == "semantic"
        and memory.embedding
        and memory.category
    ]
    scored_pairs: list[tuple[float, UserMemory, UserMemory]] = []
    checked = 0
    for left, right in combinations(semantic, 2):
        if checked >= max_pairs:
            break
        if left.category != right.category:
            continue
        checked += 1
        score = retrieval.cosine_similarity(left.embedding or [], right.embedding or [])
        scored_pairs.append((score, left, right))

    scored_pairs.sort(key=lambda item: item[0], reverse=True)
    findings: list[MemoryReconcileFinding] = []
    for score, left, right in scored_pairs:
        winner = choose_memory_winner([left, right])
        candidate = right if winner.id == left.id else left
        findings.append(
            MemoryReconcileFinding(
                finding_type="semantic_relation_candidate",
                severity="low",
                memory_id=candidate.id,
                related_memory_id=winner.id,
                proposed_action="llm_review",
                reason="ranked candidate pair requires LLM relation review",
                applied=False,
                metadata={"score": round(score, 6), "category": left.category},
            )
        )
    return findings


def expire_memory(db: Session, memory: UserMemory, now: datetime) -> None:
    previous_status = memory.status
    memory.status = "ignored" if previous_status == "pending" else "deleted"
    memory.invalid_at = now
    memory.last_touched_at = now
    memory.revision += 1
    db.add(memory)
    events.record_memory_event(
        db,
        memory,
        "expire",
        reason="memory expired during reconcile",
        previous_status=previous_status,
        new_status=memory.status,
    )


def supersede_or_ignore_duplicate(db: Session, duplicate: UserMemory, winner: UserMemory, now: datetime, reason: str) -> None:
    if duplicate.status == "pending":
        previous_status = duplicate.status
        duplicate.status = "ignored"
        duplicate.invalid_at = now
        duplicate.last_touched_at = now
        duplicate.superseded_by_id = winner.id
        duplicate.revision += 1
        db.add(duplicate)
        events.record_memory_event(
            db,
            duplicate,
            "ignore",
            reason=reason,
            previous_status=previous_status,
            new_status=duplicate.status,
            payload={"duplicate_of_id": winner.id},
        )
        return
    mark_superseded(db, duplicate, winner, now, reason=reason)


def mark_superseded(db: Session, duplicate: UserMemory, winner: UserMemory, now: datetime, reason: str) -> None:
    previous_status = duplicate.status
    duplicate.status = "superseded"
    duplicate.superseded_by_id = winner.id
    duplicate.invalid_at = now
    duplicate.last_touched_at = now
    duplicate.revision += 1
    db.add(duplicate)
    events.record_memory_event(
        db,
        duplicate,
        "supersede",
        reason=reason,
        previous_status=previous_status,
        new_status=duplicate.status,
        payload={"superseded_by_id": winner.id},
    )


def choose_memory_winner(memories: list[UserMemory]) -> UserMemory:
    return sorted(
        memories,
        key=lambda memory: (
            1 if memory.status == "active" else 0,
            memory.last_touched_at,
            memory.updated_at,
            memory.created_at,
            memory.id,
        ),
        reverse=True,
    )[0]


def aware_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value
