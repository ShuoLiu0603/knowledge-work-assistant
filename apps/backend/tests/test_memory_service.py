from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.models.agent_run import AgentRun
from app.db.models.audit_log import AuditLog
from app.db.models.conversation import Conversation, Message
from app.db.models.llm_call_log import LlmCallLog
from app.db.models.user_memory import UserMemory, UserMemoryEvent, UserMemoryRecallLog, UserMemoryUpdateJob
from app.memory import context as memory_context
from app.memory.vector_index import MemoryVectorHit
from app.services import memory_service
from helpers import create_user, isolated_session


def fake_completion(content: str = "[]"):
    from app.llm.provider import LlmCompletion

    return LlmCompletion(
        content=content,
        provider="openai_compatible",
        model_name="fake-chat",
        prompt_tokens=1,
        completion_tokens=1,
        total_tokens=2,
        latency_ms=1,
        status="success",
    )


class ConversationSummaryTrimmingTests(unittest.TestCase):
    def test_summary_is_trimmed_at_complete_sentence_boundary(self) -> None:
        summary = "First complete fact. Second complete fact. Third fact is unfinished and much too long"

        trimmed = memory_service.trim_conversation_summary_tokens(summary, 9)

        self.assertEqual(trimmed, "First complete fact. Second complete fact.")
        self.assertLessEqual(memory_service.count_tokens(trimmed), 9)

    def test_structured_summary_trimming_preserves_semantic_priority(self) -> None:
        core_summary = (
            "## CURRENT GOAL\n"
            "- Ship the production memory flow.\n\n"
            "## ACTIVE CONSTRAINTS AND DECISIONS\n"
            "- Do not modify the Agent or RAG flow.\n\n"
            "## OPEN QUESTIONS OR BLOCKERS\n"
            "- The overflow behavior still needs verification.\n\n"
            "## NEXT STEP\n"
            "- Run the focused summary tests."
        )
        summary = (
            "## CURRENT GOAL\n"
            "- Ship the production memory flow.\n\n"
            "## ACTIVE CONSTRAINTS AND DECISIONS\n"
            "- Do not modify the Agent or RAG flow.\n\n"
            "## ESTABLISHED FACTS AND COMPLETED WORK\n"
            f"- {'Low-priority historical detail. ' * 40}\n\n"
            "## IMPORTANT ARTIFACTS\n"
            "- An optional background document.\n\n"
            "## OPEN QUESTIONS OR BLOCKERS\n"
            "- The overflow behavior still needs verification.\n\n"
            "## NEXT STEP\n"
            "- Run the focused summary tests."
        )

        trimmed = memory_service.trim_conversation_summary_tokens(
            summary,
            memory_service.count_tokens(core_summary),
        )

        self.assertEqual(trimmed, core_summary)
        self.assertNotIn("Low-priority historical detail", trimmed)
        self.assertNotIn("optional background document", trimmed)


def find_memory_id(existing_memories: list[dict], marker: str) -> str | None:
    marker = marker.lower()
    for memory in existing_memories:
        if marker in str(memory.get("content") or "").lower():
            return str(memory["id"])
    return None


class SecondPassReviewMixin:
    def review_memory_conflict_candidates(
        self,
        operation: dict,
        conflict_memories: list[dict],
        user_message: str = "",
        assistant_message: str = "",
    ):
        self.judge_call_count = getattr(self, "judge_call_count", 0) + 1
        self.seen_conflict_operation = operation
        self.seen_conflict_memories = conflict_memories
        return self.review_memory_operations(user_message, assistant_message, conflict_memories)


class FakeLlmProvider(SecondPassReviewMixin):
    def review_memory_operations(self, user_message: str, assistant_message: str, existing_memories: list[dict]):
        from app.llm.provider import MemoryOperation, MemoryReview

        lowered = user_message.lower()
        if "more detailed" in lowered:
            target_id = find_memory_id(existing_memories, "detailed answers")
            operations = [
                MemoryOperation(
                    action="update",
                    target_memory_id=target_id,
                    content="detailed answers; more detailed answers",
                    category="response_detail",
                    importance="high",
                    sensitivity="low",
                    evidence="I prefer more detailed answers",
                )
            ]
        elif "detailed" in lowered:
            target_id = find_memory_id(existing_memories, "concise answers")
            operations = [
                MemoryOperation(
                    action="supersede",
                    target_memory_id=target_id,
                    content="detailed answers",
                    category="response_detail",
                    importance="high",
                    sensitivity="low",
                    evidence="I prefer detailed answers",
                )
            ]
        elif "concise" in lowered:
            operations = [
                MemoryOperation(
                    action="create",
                    content="concise answers",
                    category="response_detail",
                    importance="high",
                    sensitivity="low",
                    evidence="I prefer concise answers",
                )
            ]
        else:
            operations = [MemoryOperation(action="ignore", reason="nothing durable")]
        return MemoryReview(operations=operations, completion=fake_completion())


class DeltaSummaryProvider:
    def summarize_with_metadata(self, text: str):
        return fake_completion(text.split("New messages since previous summary:\n", 1)[-1])


class RetryingConversationSummaryProvider:
    def __init__(self) -> None:
        self.update_calls = 0
        self.compaction_calls = 0

    def summarize_with_metadata(self, _text: str):
        raise AssertionError("generic summarization must not handle conversation summaries")

    def update_conversation_summary_with_metadata(self, existing_summary: str, new_messages: str):
        self.update_calls += 1
        return fake_completion(
            "## CURRENT GOAL\n- Continue the production summary work.\n\n"
            "## ESTABLISHED FACTS AND COMPLETED WORK\n"
            f"- {'Verbose background detail. ' * 80}"
        )

    def compact_conversation_summary_with_metadata(self, summary: str):
        self.compaction_calls += 1
        return fake_completion(
            "## CURRENT GOAL\n- Continue the production summary work.\n\n"
            "## NEXT STEP\n- Run focused verification."
        )


class StructuredFakeLlmProvider(SecondPassReviewMixin):
    def review_memory_operations(self, user_message: str, assistant_message: str, existing_memories: list[dict]):
        from app.llm.provider import MemoryOperation, MemoryReview

        if "temporary" in user_message.lower():
            operation = MemoryOperation(
                action="pending",
                content="temporary pending detail",
                category="general",
                importance="low",
                sensitivity="low",
                evidence="Remember this temporary detail",
            )
        else:
            operation = MemoryOperation(
                action="create",
                content="user prefers concise answers",
                category="response_detail",
                importance="high",
                sensitivity="low",
                evidence="I prefer concise answers",
            )
        return MemoryReview(operations=[operation], completion=fake_completion())


class FakeMemoryClassificationProvider:
    def __init__(self, kind: str, category: str):
        self.kind = kind
        self.category = category

    def classify_memory_with_metadata(self, _content: str):
        from app.llm.provider import MemoryClassification

        return MemoryClassification(
            kind=self.kind,
            category=self.category,
            completion=fake_completion(),
        )


class PromotingFakeLlmProvider(StructuredFakeLlmProvider):
    def review_memory_operations(self, user_message: str, assistant_message: str, existing_memories: list[dict]):
        from app.llm.provider import MemoryOperation, MemoryReview

        action = "create" if "sure" in user_message.lower() else "pending"
        return MemoryReview(
            operations=[
                MemoryOperation(
                    action=action,
                    content="temporary pending detail",
                    category="general",
                    importance="low",
                    sensitivity="low",
                    evidence=user_message,
                )
            ],
            completion=fake_completion(),
        )


class ReviewFakeLlmProvider(SecondPassReviewMixin):
    def __init__(self, operations):
        self.operations = operations
        self.seen_existing_memories = None
        self.seen_assistant_message = None

    def review_memory_operations(self, user_message: str, assistant_message: str, existing_memories: list[dict]):
        from app.llm.provider import LlmCompletion, MemoryReview

        self.seen_existing_memories = existing_memories
        self.seen_assistant_message = assistant_message
        return MemoryReview(
            operations=self.operations,
            completion=LlmCompletion(
                content="[]",
                provider="openai_compatible",
                model_name="fake-chat",
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
                latency_ms=1,
                status="success",
            ),
        )


class GroupedReviewFakeLlmProvider(ReviewFakeLlmProvider):
    def __init__(self, operations):
        super().__init__(operations)
        self.seen_profile_memories = None
        self.seen_candidate_memories = None
        self.seen_pending_memories = None

    def review_memory_operations(
        self,
        user_message: str,
        assistant_message: str,
        existing_memories: list[dict] | None = None,
        profile_memories: list[dict] | None = None,
        candidate_memories: list[dict] | None = None,
        pending_memories: list[dict] | None = None,
    ):
        self.seen_existing_memories = existing_memories
        self.seen_profile_memories = profile_memories
        self.seen_candidate_memories = candidate_memories
        self.seen_pending_memories = pending_memories
        self.seen_assistant_message = assistant_message
        from app.llm.provider import LlmCompletion, MemoryReview

        return MemoryReview(
            operations=self.operations,
            completion=LlmCompletion(
                content="[]",
                provider="openai_compatible",
                model_name="fake-chat",
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
                latency_ms=1,
                status="success",
            ),
        )


class FakeEmbeddingProvider:
    name = "fake"
    dimension = 2

    def embed_text(self, text: str) -> list[float]:
        lowered = text.lower()
        if "concise" in lowered:
            return [1.0, 0.0]
        if "detailed" in lowered:
            return [0.0, 1.0]
        return [0.5, 0.5]


class ConflictEmbeddingProvider(FakeEmbeddingProvider):
    def embed_text(self, text: str) -> list[float]:
        lowered = text.lower()
        if "django" in lowered:
            return [1.0, 0.0]
        if "fastapi" in lowered:
            return [0.0, 1.0]
        return super().embed_text(text)


class FailingEmbeddingProvider(FakeEmbeddingProvider):
    def embed_text(self, text: str) -> list[float]:
        if "explode" in text.lower():
            raise RuntimeError("embedding failed")
        return super().embed_text(text)


class TrackingEmbeddingProvider(FakeEmbeddingProvider):
    def __init__(self) -> None:
        self.calls: list[str] = []

    def embed_text(self, text: str) -> list[float]:
        self.calls.append(text)
        return super().embed_text(text)


class ReconcileReviewFakeLlmProvider:
    def __init__(self, operations):
        self.operations = operations
        self.seen_findings = None
        self.seen_memories = None

    def review_memory_reconcile_findings(self, findings: list[dict], memories: list[dict]):
        from app.llm.provider import MemoryReview

        self.seen_findings = findings
        self.seen_memories = memories
        return MemoryReview(operations=self.operations, completion=fake_completion())


class ConflictReviewFakeLlmProvider:
    def __init__(self, primary_operations, conflict_action: str = "supersede"):
        self.primary_operations = primary_operations
        self.conflict_action = conflict_action
        self.seen_conflict_operation = None
        self.seen_conflict_memories = None

    def review_memory_operations(self, user_message: str, assistant_message: str, existing_memories: list[dict]):
        from app.llm.provider import MemoryReview

        return MemoryReview(operations=self.primary_operations, completion=fake_completion())

    def review_memory_conflict_candidates(
        self,
        operation: dict,
        conflict_memories: list[dict],
        user_message: str = "",
        assistant_message: str = "",
    ):
        from app.llm.provider import MemoryOperation, MemoryReview

        self.seen_conflict_operation = operation
        self.seen_conflict_memories = conflict_memories
        if "fastapi" not in str(operation.get("content") or "").lower():
            return MemoryReview(
                operations=[
                    MemoryOperation(
                        action="create",
                        content=str(operation.get("content") or ""),
                        kind=str(operation.get("kind") or "preference"),
                        category=str(operation.get("category") or "general"),
                        canonical_key=str(operation.get("canonical_key") or ""),
                        importance=str(operation.get("importance") or "low"),
                        sensitivity=str(operation.get("sensitivity") or "low"),
                        evidence=str(operation.get("evidence") or ""),
                        reason="second-pass judge approved a new memory",
                    )
                ],
                completion=fake_completion(),
            )
        target_id = str(conflict_memories[0]["id"]) if conflict_memories else None
        return MemoryReview(
            operations=[
                MemoryOperation(
                    action=self.conflict_action,
                    target_memory_id=target_id,
                    content="user uses FastAPI backend",
                    kind="project",
                    category="project",
                    canonical_key="project:backend_framework",
                    sensitivity="low",
                    evidence="I use FastAPI backend",
                    reason="new backend framework replaces the old backend framework memory",
                )
            ],
            completion=fake_completion(),
        )


class FailingConflictReviewFakeLlmProvider(ConflictReviewFakeLlmProvider):
    def review_memory_conflict_candidates(self, operation: dict, conflict_memories: list[dict], **_kwargs):
        raise RuntimeError("conflict reviewer unavailable")


class NoMemoryReviewProvider:
    pass


class ExtractorOnlyProvider:
    def review_memory_operations(self, user_message: str, assistant_message: str, existing_memories: list[dict]):
        from app.llm.provider import MemoryOperation, MemoryReview

        return MemoryReview(
            operations=[
                MemoryOperation(
                    action="create",
                    content="user prefers concise answers",
                    category="response_detail",
                    sensitivity="low",
                    evidence="I prefer concise answers",
                )
            ],
            completion=fake_completion(),
        )


class MemoryServiceTests(unittest.TestCase):
    def test_memory_actions_cover_dedupe_update_supersede_and_ignore(self) -> None:
        with (
            isolated_session() as session,
            patch.object(memory_service, "get_llm_provider", return_value=FakeLlmProvider()),
            patch("app.memory.embedding.get_embedding_provider", return_value=FakeEmbeddingProvider()),
        ):
            user = create_user(session, "memory@example.com", "Memory")

            create_action = memory_service.process_user_memory(session, user.id, "I prefer concise answers")[0]
            touch_action = memory_service.process_user_memory(session, user.id, "I prefer concise answers")[0]
            supersede_action = memory_service.process_user_memory(session, user.id, "I prefer detailed answers")[0]
            update_action = memory_service.process_user_memory(session, user.id, "I prefer more detailed answers")[0]
            ignore_action = memory_service.process_user_memory(session, user.id, "Do not remember this temporary note")[0]

            self.assertEqual(create_action.action, "create")
            self.assertEqual(touch_action.action, "touch")
            self.assertEqual(supersede_action.action, "supersede")
            self.assertEqual(update_action.action, "update")
            self.assertEqual(ignore_action.action, "ignore")
            self.assertEqual(ignore_action.reason, "user asked not to remember")

            rows = session.scalars(select(UserMemory).where(UserMemory.user_id == user.id)).all()
            active = [memory for memory in rows if memory.status == "active"]
            superseded = [memory for memory in rows if memory.status == "superseded"]

            self.assertEqual(len(active), 1)
            self.assertEqual(len(superseded), 1)
            self.assertIn("detailed answers", active[0].content)
            self.assertIn("more detailed answers", active[0].content)
            self.assertGreaterEqual(active[0].merge_count, 1)
            self.assertEqual(superseded[0].content, "concise answers")
            self.assertEqual(superseded[0].touched_count, 1)
            self.assertEqual(superseded[0].superseded_by_id, active[0].id)

            recalled = memory_service.retrieve_relevant_memories(session, user.id, "answer with detailed context")
            self.assertEqual(recalled[0].id, active[0].id)

            recall_log = session.scalar(
                select(UserMemoryRecallLog)
                .where(UserMemoryRecallLog.user_id == user.id, UserMemoryRecallLog.query == "answer with detailed context")
                .order_by(UserMemoryRecallLog.created_at.desc(), UserMemoryRecallLog.id.desc())
            )
            self.assertIsNotNone(recall_log)
            self.assertEqual(recall_log.recall_mode, "sticky_only")
            self.assertEqual(recall_log.selected_memory_ids, [active[0].id])
            self.assertGreaterEqual(recall_log.active_count, 1)
            self.assertGreaterEqual(len(recall_log.candidates), 1)
            self.assertTrue(any(candidate["selected"] for candidate in recall_log.candidates))

            events = session.scalars(
                select(UserMemoryEvent).where(UserMemoryEvent.user_id == user.id)
            ).all()
            event_types = [event.event_type for event in events]
            self.assertIn("create", event_types)
            self.assertIn("touch", event_types)
            self.assertIn("supersede", event_types)
            self.assertIn("update", event_types)
            self.assertEqual({event.user_id for event in events}, {user.id})

    def test_memory_update_without_provider_support_is_ignored(self) -> None:
        with (
            isolated_session() as session,
            patch.object(memory_service, "get_llm_provider", return_value=NoMemoryReviewProvider()),
        ):
            user = create_user(session, "memory-no-review-provider@example.com", "Memory No Review Provider")

            action = memory_service.process_user_memory(session, user.id, "I prefer concise answers")[0]

            self.assertEqual(action.action, "ignore")
            self.assertEqual(action.reason, "memory review is not supported by the configured provider")

    def test_memory_candidate_cannot_bypass_missing_second_pass_judge(self) -> None:
        with (
            isolated_session() as session,
            patch.object(memory_service, "get_llm_provider", return_value=ExtractorOnlyProvider()),
            patch("app.memory.embedding.get_embedding_provider", return_value=FakeEmbeddingProvider()),
        ):
            user = create_user(session, "memory-no-judge@example.com", "Memory No Judge")

            action = memory_service.process_user_memory(session, user.id, "I prefer concise answers")[0]
            rows = session.scalars(select(UserMemory).where(UserMemory.user_id == user.id)).all()

            self.assertEqual(action.action, "ignore")
            self.assertIn("mandatory memory judge", action.reason)
            self.assertEqual(rows, [])

    def test_additive_hobby_preference_is_created_beside_existing_hobby(self) -> None:
        from app.llm.provider import MemoryOperation, MemoryReview

        class AdditivePreferenceProvider:
            def __init__(self) -> None:
                self.related_memories: list[dict] = []

            def review_memory_operations(self, user_message: str, assistant_message: str, **_kwargs):
                return MemoryReview(
                    operations=[
                        MemoryOperation(
                            action="create",
                            content="用户喜欢踢足球",
                            kind="preference",
                            category="general",
                            sensitivity="low",
                            evidence="其实我也喜欢踢足球",
                        )
                    ],
                    completion=fake_completion(),
                )

            def review_memory_conflict_candidates(self, operation: dict, conflict_memories: list[dict], **_kwargs):
                self.related_memories = conflict_memories
                return MemoryReview(
                    operations=[
                        MemoryOperation(
                            action="create",
                            relation="independent",
                            content=str(operation["content"]),
                            kind=str(operation["kind"]),
                            category=str(operation["category"]),
                            sensitivity="low",
                            evidence=str(operation["evidence"]),
                        )
                    ],
                    completion=fake_completion(),
                )

        class SportsEmbeddingProvider(FakeEmbeddingProvider):
            def embed_text(self, text: str) -> list[float]:
                if "篮球" in text:
                    return [1.0, 0.0]
                if "足球" in text:
                    return [0.0, 1.0]
                return super().embed_text(text)

        provider = AdditivePreferenceProvider()
        with (
            isolated_session() as session,
            patch.object(memory_service, "get_llm_provider", return_value=provider),
            patch("app.memory.embedding.get_embedding_provider", return_value=SportsEmbeddingProvider()),
        ):
            user = create_user(session, "memory-additive-hobbies@example.com", "Memory Additive Hobbies")
            basketball = memory_service.create_manual_memory(session, user.id, "用户喜欢打篮球")

            action = memory_service.process_user_memory(session, user.id, "其实我也喜欢踢足球")[0]
            active = session.scalars(
                select(UserMemory).where(UserMemory.user_id == user.id, UserMemory.status == "active")
            ).all()

            self.assertEqual(action.action, "create")
            self.assertIn(basketball.id, {memory["id"] for memory in provider.related_memories})
            self.assertEqual({memory.content for memory in active}, {"用户喜欢打篮球", "用户喜欢踢足球"})

    def test_replacement_relation_supersedes_profile_singleton_after_invalid_judge_retry(self) -> None:
        from app.llm.provider import MemoryOperation, MemoryReview

        class NameCorrectionProvider:
            def __init__(self) -> None:
                self.judge_calls = 0
                self.retry_reasons: list[str] = []

            def review_memory_operations(self, user_message: str, assistant_message: str, **_kwargs):
                return MemoryReview(
                    operations=[
                        MemoryOperation(
                            action="create",
                            content="用户的名字是刘石页",
                            kind="profile",
                            category="name",
                            canonical_key="profile:name",
                            sensitivity="low",
                            evidence="我不叫刘硕 我叫刘石页",
                        )
                    ],
                    completion=fake_completion(),
                )

            def review_memory_conflict_candidates(
                self,
                operation: dict,
                conflict_memories: list[dict],
                retry_reason: str = "",
                **_kwargs,
            ):
                self.judge_calls += 1
                self.retry_reasons.append(retry_reason)
                if self.judge_calls == 1:
                    return MemoryReview(operations=[], completion=fake_completion())
                target = next(memory for memory in conflict_memories if memory["canonical_key"] == "profile:name")
                return MemoryReview(
                    operations=[
                        MemoryOperation(
                            action="supersede",
                            relation="replacement",
                            target_memory_id=target["id"],
                            content="用户的名字是刘石页",
                            kind="profile",
                            category="name",
                            canonical_key="profile:name",
                            sensitivity="low",
                            evidence="我不叫刘硕 我叫刘石页",
                        )
                    ],
                    completion=fake_completion(),
                )

        provider = NameCorrectionProvider()
        with (
            isolated_session() as session,
            patch.object(memory_service, "get_llm_provider", return_value=provider),
            patch("app.memory.embedding.get_embedding_provider", return_value=FakeEmbeddingProvider()),
        ):
            user = create_user(session, "memory-name-replacement@example.com", "Memory Name Replacement")
            old = memory_service.create_manual_memory(
                session,
                user.id,
                "用户的名字是刘硕",
                category="name",
                kind="profile",
                canonical_key="profile:name",
            )

            action = memory_service.process_user_memory(session, user.id, "我不叫刘硕 我叫刘石页")[0]
            session.refresh(old)
            new = session.get(UserMemory, action.memory_id)

            self.assertEqual(provider.judge_calls, 2)
            self.assertEqual(provider.retry_reasons[0], "")
            self.assertIn("violated the relation and target-id contract", provider.retry_reasons[1])
            self.assertEqual(action.action, "supersede")
            self.assertEqual(old.status, "superseded")
            self.assertEqual(old.superseded_by_id, new.id)
            self.assertEqual(new.content, "用户的名字是刘石页")
            self.assertEqual(new.status, "active")

    def test_relational_changes_preserve_target_injection_policy(self) -> None:
        from app.llm.provider import MemoryOperation

        with (
            isolated_session() as session,
            patch("app.memory.embedding.get_embedding_provider", return_value=FakeEmbeddingProvider()),
        ):
            user = create_user(session, "memory-layer-inheritance@example.com", "Memory Layer Inheritance")
            profile = memory_service.create_manual_memory(
                session,
                user.id,
                "Persistent identity value alpha",
                category="name",
                kind="profile",
            )
            semantic = memory_service.create_manual_memory(
                session,
                user.id,
                "User follows a recurring activity",
                category="general",
                kind="preference",
            )

            replacement_content = "Persistent identity value beta"
            replacement_action = memory_service.process_memory_operation(
                session,
                user.id,
                MemoryOperation(
                    action="supersede",
                    relation="replacement",
                    target_memory_id=profile.id,
                    content=replacement_content,
                    kind="preference",
                    category="general",
                    sensitivity="low",
                    evidence=replacement_content,
                ),
                memory_service.MemorySource(text=replacement_content),
                user_message=replacement_content,
            )
            replacement = session.get(UserMemory, replacement_action.memory_id)

            refinement_content = "User follows a recurring activity on weekends"
            refinement_action = memory_service.process_memory_operation(
                session,
                user.id,
                MemoryOperation(
                    action="update",
                    relation="refinement",
                    target_memory_id=semantic.id,
                    content=refinement_content,
                    kind="profile",
                    category="name",
                    canonical_key="profile:name",
                    sensitivity="low",
                    evidence=refinement_content,
                ),
                memory_service.MemorySource(text=refinement_content),
                user_message=refinement_content,
            )
            session.refresh(semantic)

            self.assertEqual(replacement_action.action, "supersede")
            self.assertEqual(replacement.category, "name")
            self.assertEqual(replacement.kind, "profile")
            self.assertEqual(replacement.memory_layer, "profile")
            self.assertEqual(replacement.profile_slot, "name")
            self.assertEqual(replacement.canonical_key, "profile:name")
            self.assertTrue(replacement.pinned)

            self.assertEqual(refinement_action.action, "update")
            self.assertEqual(semantic.category, "general")
            self.assertEqual(semantic.kind, "preference")
            self.assertEqual(semantic.memory_layer, "semantic")
            self.assertEqual(semantic.profile_slot, "")
            self.assertEqual(semantic.canonical_key, "")
            self.assertFalse(semantic.pinned)

            profile_ids = {item["id"] for item in memory_service.list_core_profile_context(session, user.id)}
            self.assertIn(replacement.id, profile_ids)
            self.assertNotIn(semantic.id, profile_ids)

            with patch("app.memory.vector_index.search_active_memories", return_value=[]):
                recalled = memory_service.retrieve_relevant_memories(
                    session,
                    user.id,
                    refinement_content,
                    include_profile=False,
                )
            self.assertIn(semantic.id, {memory.id for memory in recalled})
            self.assertNotIn(replacement.id, {memory.id for memory in recalled})

    def test_memory_recall_query_returns_saved_memories_without_embedding(self) -> None:
        provider = TrackingEmbeddingProvider()
        with isolated_session() as session, patch("app.memory.embedding.get_embedding_provider", return_value=provider):
            user = create_user(session, "memory-recall@example.com", "Memory Recall")
            first = memory_service.create_manual_memory(session, user.id, "用户偏好中文回答", category="language")
            second = memory_service.create_manual_memory(session, user.id, "用户当前项目是 RAG 平台", category="general")

            provider.calls.clear()
            recalled = memory_service.retrieve_relevant_memories(session, user.id, "你记得我什么？")

            self.assertEqual({memory.id for memory in recalled}, {first.id, second.id})
            self.assertEqual(provider.calls, [])
            recall_log = session.scalar(
                select(UserMemoryRecallLog)
                .where(UserMemoryRecallLog.user_id == user.id, UserMemoryRecallLog.query == "你记得我什么？")
                .order_by(UserMemoryRecallLog.created_at.desc(), UserMemoryRecallLog.id.desc())
            )
            self.assertIsNotNone(recall_log)
            self.assertEqual(recall_log.recall_mode, "full_recall")
            self.assertEqual(set(recall_log.selected_memory_ids), {first.id, second.id})
            self.assertEqual({candidate["route"] for candidate in recall_log.candidates}, {"full_recall"})
            context = memory_service.build_memory_context_for_question(session, user.id, "我的偏好是什么？")
            self.assertIn("用户偏好中文回答", context)

    def test_full_memory_recall_prioritizes_profile_memories_over_recent_semantic_memories(self) -> None:
        with (
            isolated_session() as session,
            patch("app.memory.embedding.get_embedding_provider", return_value=FakeEmbeddingProvider()),
        ):
            user = create_user(session, "memory-full-recall-profile-priority@example.com", "Memory Full Recall Profile")
            profile = memory_service.create_manual_memory(
                session,
                user.id,
                "user prefers Chinese answers",
                category="language",
            )
            for index in range(memory_service.FULL_MEMORY_RECALL_LIMIT + 5):
                content = f"user works on project detail {index}"
                memory_service.create_memory_row(
                    session,
                    user.id,
                    content,
                    memory_service.normalize_memory_content(content),
                    memory_service.hash_content(memory_service.normalize_memory_content(content)),
                    "project",
                    memory_service.MemorySource(text="manual"),
                    memory_service.embed_memory_text(content),
                    kind="project",
                )

            recalled = memory_service.retrieve_relevant_memories(session, user.id, "what do you remember")

            self.assertEqual(len(recalled), memory_service.FULL_MEMORY_RECALL_LIMIT)
            self.assertIn(profile.id, {memory.id for memory in recalled})
            recall_log = session.scalar(
                select(UserMemoryRecallLog)
                .where(UserMemoryRecallLog.user_id == user.id, UserMemoryRecallLog.query == "what do you remember")
                .order_by(UserMemoryRecallLog.created_at.desc(), UserMemoryRecallLog.id.desc())
            )
            self.assertIsNotNone(recall_log)
            self.assertIn(profile.id, set(recall_log.selected_memory_ids))

    def test_memory_context_is_budgeted(self) -> None:
        context = memory_service.format_memory_context(
            long_memories=[{"content": f"memory {index} " + ("A" * 120)} for index in range(10)],
            short_memory=[{"role": "user", "content": "B" * 120} for _ in range(5)],
            conversation_summary="C" * 600,
            max_long_memories=10,
            max_chars=500,
        )

        self.assertLessEqual(len(context), 500)
        self.assertIn("Stable preferences", context)
        self.assertIn("Relevant long-term memories", context)
        self.assertIn("Conversation summary", context)
        self.assertIn("Recent conversation", context)

    def test_memory_context_respects_token_budget(self) -> None:
        context = memory_context.format_memory_context(
            long_memories=[{"content": "token heavy memory " * 80} for _ in range(6)],
            short_memory=[{"role": "user", "content": "recent turn " * 80}],
            conversation_summary="summary " * 120,
            max_long_memories=6,
            max_chars=5000,
            max_tokens=120,
            model_name="gpt-4o-mini",
        )
        budget = memory_context.TextBudget.from_limits(
            max_chars=5000,
            max_tokens=120,
            model_name="gpt-4o-mini",
        )

        self.assertLessEqual(budget.count(context), 120)
        self.assertIn("Stable preferences", context)
        self.assertIn("Relevant long-term memories", context)
        self.assertIn("Conversation summary", context)
        self.assertIn("Recent conversation", context)

    def test_memory_context_prioritizes_sticky_memories_under_budget(self) -> None:
        context = memory_context.format_memory_context(
            long_memories=[
                {
                    "content": "low priority general memory " * 40,
                    "category": "general",
                    "kind": "preference",
                    "metadata": {"importance": "low"},
                },
                {
                    "content": "用户偏好中文回答",
                    "category": "language",
                    "kind": "preference",
                    "metadata": {"importance": "high"},
                },
            ],
            short_memory=[],
            conversation_summary=None,
            max_long_memories=2,
            max_chars=350,
        )

        self.assertIn("用户偏好中文回答", context)
        self.assertIn("Stable preferences", context)
        self.assertIn("Relevant long-term memories", context)

    def test_memory_context_gives_unused_budget_to_profile_memories(self) -> None:
        context = memory_context.format_memory_context(
            long_memories=[],
            short_memory=[],
            conversation_summary=None,
            profile_memories=[
                {
                    "content": "user prefers Chinese answers",
                    "category": "language",
                    "kind": "preference",
                    "metadata": {"importance": "high"},
                },
                {
                    "content": "user prefers concise code reviews",
                    "category": "response_detail",
                    "kind": "preference",
                    "metadata": {"importance": "high"},
                },
                {
                    "content": "user prefers markdown tables for comparisons",
                    "category": "format",
                    "kind": "preference",
                    "metadata": {"importance": "medium"},
                },
            ],
            max_chars=420,
        )

        self.assertLessEqual(len(context), 420)
        self.assertIn("user prefers Chinese answers", context)
        self.assertIn("user prefers concise code reviews", context)
        self.assertIn("user prefers markdown tables for comparisons", context)

    def test_profile_memories_are_loaded_even_when_semantic_recall_is_empty(self) -> None:
        with (
            isolated_session() as session,
            patch("app.memory.embedding.get_embedding_provider", return_value=FakeEmbeddingProvider()),
        ):
            user = create_user(session, "memory-profile-context@example.com", "Memory Profile Context")
            profile = memory_service.create_manual_memory(
                session,
                user.id,
                "user prefers Chinese answers",
                category="language",
            )

            with patch.object(memory_service, "retrieve_relevant_memories") as retrieve:
                context = memory_service.build_memory_context_for_question(session, user.id, "unrelated question")

            self.assertIn(profile.content, context)
            self.assertIn("Stable preferences", context)
            retrieve.assert_not_called()

    def test_account_username_is_profile_fallback_but_saved_name_takes_priority(self) -> None:
        with (
            isolated_session() as session,
            patch("app.memory.embedding.get_embedding_provider", return_value=FakeEmbeddingProvider()),
        ):
            user = create_user(session, "memory-name-fallback@example.com", "Account Name")

            fallback = memory_service.list_core_profile_context(session, user.id)
            self.assertEqual([memory["id"] for memory in fallback], ["account:username"])
            self.assertIn("Account Name", fallback[0]["content"])

            saved = memory_service.create_manual_memory(
                session,
                user.id,
                "User prefers to be called Alice",
                category="name",
                kind="profile",
            )
            profile = memory_service.list_core_profile_context(session, user.id)

            self.assertEqual([memory["id"] for memory in profile], [saved.id])
            self.assertNotIn("account:username", [memory["id"] for memory in profile])

    def test_memory_governance_fields_are_persisted_and_updated(self) -> None:
        with (
            isolated_session() as session,
            patch("app.memory.embedding.get_embedding_provider", return_value=FakeEmbeddingProvider()),
        ):
            user = create_user(session, "memory-governance@example.com", "Memory Governance")
            memory = memory_service.create_manual_memory(
                session,
                user.id,
                "user prefers English answers",
                category="language",
            )

            self.assertEqual(memory.memory_layer, "profile")
            self.assertEqual(memory.profile_slot, "language")
            self.assertEqual(memory.scope_type, "user")
            self.assertEqual(memory.scope_id, user.id)
            self.assertTrue(memory.pinned)
            self.assertEqual(memory.revision, 1)
            self.assertEqual(memory.extra_metadata["memory_layer"], "profile")
            self.assertEqual(memory.extra_metadata["profile_slot"], "language")

            updated = memory_service.update_user_memory(
                session,
                user.id,
                memory.id,
                category="project",
                kind="project",
            )

            self.assertEqual(updated.memory_layer, "semantic")
            self.assertEqual(updated.profile_slot, "")
            self.assertFalse(updated.pinned)
            self.assertEqual(updated.revision, 2)

    def test_identity_is_core_profile_while_current_project_is_on_demand(self) -> None:
        with (
            isolated_session() as session,
            patch("app.memory.embedding.get_embedding_provider", return_value=FakeEmbeddingProvider()),
        ):
            user = create_user(session, "memory-sticky-slots@example.com", "Memory Sticky Slots")
            old_name = memory_service.create_manual_memory(
                session,
                user.id,
                "user name is Alice",
                category="name",
                kind="profile",
            )
            new_name = memory_service.create_manual_memory(
                session,
                user.id,
                "user name is Bob",
                category="name",
                kind="profile",
            )
            project = memory_service.create_manual_memory(
                session,
                user.id,
                "user is currently building an agentic RAG platform",
                category="current_project",
                kind="project",
            )

            session.refresh(old_name)
            session.refresh(new_name)

            self.assertEqual(old_name.status, "superseded")
            self.assertEqual(old_name.superseded_by_id, new_name.id)
            self.assertEqual(new_name.memory_layer, "profile")
            self.assertEqual(new_name.profile_slot, "name")
            self.assertEqual(new_name.canonical_key, "profile:name")
            self.assertTrue(new_name.pinned)
            self.assertEqual(project.memory_layer, "semantic")
            self.assertEqual(project.profile_slot, "")
            self.assertEqual(project.canonical_key, "project:current_project")

            with patch.object(memory_service, "retrieve_relevant_memories", return_value=[]):
                context = memory_service.build_memory_context_for_question(session, user.id, "unrelated question")

            self.assertIn(new_name.content, context)
            self.assertNotIn(project.content, context)

    def test_memory_vector_payload_contains_governance_fields(self) -> None:
        from app.memory import vector_index

        with (
            isolated_session() as session,
            patch("app.memory.embedding.get_embedding_provider", return_value=FakeEmbeddingProvider()),
        ):
            user = create_user(session, "memory-vector-payload@example.com", "Memory Vector Payload")
            memory = memory_service.create_manual_memory(
                session,
                user.id,
                "user prefers English answers",
                category="language",
            )

            payload = vector_index.memory_payload(memory)

            self.assertEqual(payload["memory_layer"], "profile")
            self.assertEqual(payload["profile_slot"], "language")
            self.assertEqual(payload["scope_type"], "user")
            self.assertEqual(payload["scope_id"], user.id)
            self.assertTrue(payload["pinned"])
            self.assertEqual(payload["revision"], 1)

    def test_memory_vector_sync_deletes_non_active_memory_vectors(self) -> None:
        from app.memory import vector_index

        with (
            isolated_session() as session,
            patch("app.memory.embedding.get_embedding_provider", return_value=FakeEmbeddingProvider()),
            patch("app.memory.vector_index.is_memory_vector_index_enabled", return_value=True),
            patch("app.memory.vector_index.delete_memory_vector") as delete_memory_vector,
            patch("app.memory.vector_index.get_qdrant_client") as get_qdrant_client,
        ):
            user = create_user(session, "memory-vector-non-active@example.com", "Memory Vector Non Active")
            memory = memory_service.create_manual_memory(session, user.id, "user works on vector governance")
            delete_memory_vector.reset_mock()
            get_qdrant_client.reset_mock()

            for status in ("pending", "ignored", "superseded", "deleted"):
                memory.status = status
                vector_index.sync_memory_vector(memory)

            self.assertEqual(delete_memory_vector.call_count, 4)
            delete_memory_vector.assert_called_with(memory.id)
            get_qdrant_client.assert_not_called()

    def test_low_level_create_supersedes_existing_profile_singleton(self) -> None:
        with (
            isolated_session() as session,
            patch("app.memory.embedding.get_embedding_provider", return_value=FakeEmbeddingProvider()),
        ):
            user = create_user(session, "memory-singleton@example.com", "Memory Singleton")
            first = memory_service.create_memory_row(
                session,
                user.id,
                "user prefers concise answers",
                "user prefers concise answers",
                memory_service.hash_content("user prefers concise answers"),
                "response_detail",
                memory_service.MemorySource(text="manual"),
                memory_service.embed_memory_text("user prefers concise answers"),
            )
            second = memory_service.create_memory_row(
                session,
                user.id,
                "user prefers detailed answers",
                "user prefers detailed answers",
                memory_service.hash_content("user prefers detailed answers"),
                "response_detail",
                memory_service.MemorySource(text="manual"),
                memory_service.embed_memory_text("user prefers detailed answers"),
            )

            session.refresh(first)
            session.refresh(second)
            active = session.scalars(
                select(UserMemory).where(
                    UserMemory.user_id == user.id,
                    UserMemory.status == "active",
                    UserMemory.profile_slot == "response_detail",
                )
            ).all()

            self.assertEqual([memory.id for memory in active], [second.id])
            self.assertEqual(first.status, "superseded")
            self.assertEqual(first.superseded_by_id, second.id)

    def test_explicit_profile_slot_generates_canonical_key(self) -> None:
        with (
            isolated_session() as session,
            patch("app.memory.embedding.get_embedding_provider", return_value=FakeEmbeddingProvider()),
        ):
            user = create_user(session, "memory-explicit-profile-slot@example.com", "Memory Explicit Profile Slot")
            memory = memory_service.create_memory_row(
                session,
                user.id,
                "user prefers Chinese answers",
                "user prefers chinese answers",
                memory_service.hash_content("user prefers chinese answers"),
                "general",
                memory_service.MemorySource(text="manual"),
                memory_service.embed_memory_text("user prefers chinese answers"),
                memory_layer="profile",
                profile_slot="language",
            )

            self.assertEqual(memory.memory_layer, "profile")
            self.assertEqual(memory.profile_slot, "language")
            self.assertEqual(memory.canonical_key, "profile:language")
            self.assertEqual(memory.extra_metadata["canonical_key"], "profile:language")

    def test_explicit_profile_slot_replaces_stale_manual_canonical_key(self) -> None:
        with (
            isolated_session() as session,
            patch("app.memory.embedding.get_embedding_provider", return_value=FakeEmbeddingProvider()),
        ):
            user = create_user(session, "memory-profile-slot-overrides-key@example.com", "Memory Profile Slot Overrides")
            memory = memory_service.create_manual_memory(
                session,
                user.id,
                "user prefers Chinese answers",
                category="project",
                kind="project",
                canonical_key="project:language_note",
            )

            updated = memory_service.create_manual_memory(
                session,
                user.id,
                "user prefers Chinese answers",
                memory_layer="profile",
                profile_slot="language",
            )

            self.assertEqual(updated.id, memory.id)
            self.assertEqual(updated.memory_layer, "profile")
            self.assertEqual(updated.profile_slot, "language")
            self.assertEqual(updated.canonical_key, "profile:language")
            self.assertEqual(updated.extra_metadata["canonical_key"], "profile:language")

    def test_low_level_create_supersedes_existing_canonical_key_conflict(self) -> None:
        with (
            isolated_session() as session,
            patch("app.memory.embedding.get_embedding_provider", return_value=ConflictEmbeddingProvider()),
        ):
            user = create_user(session, "memory-canonical-singleton@example.com", "Memory Canonical Singleton")
            first = memory_service.create_memory_row(
                session,
                user.id,
                "user uses Django backend",
                "user uses django backend",
                memory_service.hash_content("user uses django backend"),
                "project",
                memory_service.MemorySource(text="manual"),
                memory_service.embed_memory_text("user uses django backend"),
                kind="project",
                canonical_key="project:backend_framework",
            )
            second = memory_service.create_memory_row(
                session,
                user.id,
                "user uses FastAPI backend",
                "user uses fastapi backend",
                memory_service.hash_content("user uses fastapi backend"),
                "project",
                memory_service.MemorySource(text="manual"),
                memory_service.embed_memory_text("user uses fastapi backend"),
                kind="project",
                canonical_key="project:backend_framework",
            )

            session.refresh(first)
            session.refresh(second)

            self.assertEqual(second.status, "active")
            self.assertEqual(first.status, "superseded")
            self.assertEqual(first.superseded_by_id, second.id)

    def test_low_level_create_can_keep_pending_canonical_key_conflict_for_review(self) -> None:
        with (
            isolated_session() as session,
            patch("app.memory.embedding.get_embedding_provider", return_value=ConflictEmbeddingProvider()),
        ):
            user = create_user(session, "memory-canonical-pending-review@example.com", "Memory Canonical Pending")
            first = memory_service.create_memory_row(
                session,
                user.id,
                "user uses Django backend",
                "user uses django backend",
                memory_service.hash_content("user uses django backend"),
                "project",
                memory_service.MemorySource(text="manual"),
                memory_service.embed_memory_text("user uses django backend"),
                kind="project",
                canonical_key="project:backend_framework",
            )
            second = memory_service.create_memory_row(
                session,
                user.id,
                "user uses FastAPI backend",
                "user uses fastapi backend",
                memory_service.hash_content("user uses fastapi backend"),
                "project",
                memory_service.MemorySource(text="manual"),
                memory_service.embed_memory_text("user uses fastapi backend"),
                status="pending",
                kind="project",
                canonical_key="project:backend_framework",
                enforce_canonical_key_conflicts=False,
            )

            session.refresh(first)
            session.refresh(second)

            self.assertEqual(first.status, "active")
            self.assertEqual(second.status, "pending")

    def test_database_rejects_duplicate_active_canonical_key_even_when_code_gate_is_disabled(self) -> None:
        with (
            isolated_session() as session,
            patch("app.memory.embedding.get_embedding_provider", return_value=ConflictEmbeddingProvider()),
        ):
            user = create_user(session, "memory-canonical-db-unique@example.com", "Memory Canonical DB Unique")
            first = memory_service.create_memory_row(
                session,
                user.id,
                "user uses Django backend",
                "user uses django backend",
                memory_service.hash_content("user uses django backend"),
                "project",
                memory_service.MemorySource(text="manual"),
                memory_service.embed_memory_text("user uses django backend"),
                kind="project",
                canonical_key="project:backend_framework",
                enforce_canonical_key_conflicts=False,
            )

            with self.assertRaises(IntegrityError):
                memory_service.create_memory_row(
                    session,
                    user.id,
                    "user uses FastAPI backend",
                    "user uses fastapi backend",
                    memory_service.hash_content("user uses fastapi backend"),
                    "project",
                    memory_service.MemorySource(text="manual"),
                    memory_service.embed_memory_text("user uses fastapi backend"),
                    kind="project",
                    canonical_key="project:backend_framework",
                    enforce_canonical_key_conflicts=False,
                )

            session.refresh(first)
            self.assertEqual(first.status, "active")

    def test_on_demand_role_memories_can_have_multiple_active_rows(self) -> None:
        with (
            isolated_session() as session,
            patch("app.memory.embedding.get_embedding_provider", return_value=FakeEmbeddingProvider()),
        ):
            user = create_user(session, "memory-non-singleton@example.com", "Memory Non Singleton")
            first = memory_service.create_memory_row(
                session,
                user.id,
                "user is a backend engineer",
                "user is a backend engineer",
                memory_service.hash_content("user is a backend engineer"),
                "role",
                memory_service.MemorySource(text="manual"),
                memory_service.embed_memory_text("user is a backend engineer"),
                kind="profile",
            )
            second = memory_service.create_memory_row(
                session,
                user.id,
                "user mentors junior engineers",
                "user mentors junior engineers",
                memory_service.hash_content("user mentors junior engineers"),
                "role",
                memory_service.MemorySource(text="manual"),
                memory_service.embed_memory_text("user mentors junior engineers"),
                kind="profile",
            )

            session.refresh(first)
            session.refresh(second)

            self.assertEqual(first.status, "active")
            self.assertEqual(second.status, "active")
            self.assertEqual(first.memory_layer, "semantic")
            self.assertEqual(second.memory_layer, "semantic")
            self.assertEqual(first.profile_slot, "")
            self.assertEqual(second.profile_slot, "")

    def test_memory_reconcile_dry_run_reports_without_applying(self) -> None:
        with (
            isolated_session() as session,
            patch("app.memory.embedding.get_embedding_provider", return_value=FakeEmbeddingProvider()),
        ):
            user = create_user(session, "memory-reconcile-dry@example.com", "Memory Reconcile Dry")
            first = memory_service.create_memory_row(
                session,
                user.id,
                "user works on an agentic RAG project",
                "user works on an agentic rag project",
                memory_service.hash_content("user works on an agentic rag project"),
                "project",
                memory_service.MemorySource(text="manual"),
                memory_service.embed_memory_text("user works on an agentic rag project"),
            )
            duplicate = memory_service.create_memory_row(
                session,
                user.id,
                "user works on an agentic RAG project",
                "user works on an agentic rag project",
                memory_service.hash_content("user works on an agentic rag project"),
                "project",
                memory_service.MemorySource(text="manual"),
                memory_service.embed_memory_text("user works on an agentic rag project"),
            )

            report = memory_service.reconcile_user_memories(session, user.id, apply=False)
            session.refresh(first)
            session.refresh(duplicate)

            self.assertEqual(report["applied_count"], 0)
            self.assertTrue(any(finding["finding_type"] == "exact_duplicate" for finding in report["findings"]))
            self.assertEqual(first.status, "active")
            self.assertEqual(duplicate.status, "active")

    def test_memory_reconcile_apply_expires_and_deduplicates_safe_findings(self) -> None:
        with (
            isolated_session() as session,
            patch("app.memory.embedding.get_embedding_provider", return_value=FakeEmbeddingProvider()),
        ):
            user = create_user(session, "memory-reconcile-apply@example.com", "Memory Reconcile Apply")
            first = memory_service.create_memory_row(
                session,
                user.id,
                "user works on an agentic RAG project",
                "user works on an agentic rag project",
                memory_service.hash_content("user works on an agentic rag project"),
                "project",
                memory_service.MemorySource(text="manual"),
                memory_service.embed_memory_text("user works on an agentic rag project"),
            )
            duplicate = memory_service.create_memory_row(
                session,
                user.id,
                "user works on an agentic RAG project",
                "user works on an agentic rag project",
                memory_service.hash_content("user works on an agentic rag project"),
                "project",
                memory_service.MemorySource(text="manual"),
                memory_service.embed_memory_text("user works on an agentic rag project"),
            )
            expired = memory_service.create_memory_row(
                session,
                user.id,
                "expired project memory",
                "expired project memory",
                memory_service.hash_content("expired project memory"),
                "project",
                memory_service.MemorySource(text="manual"),
                memory_service.embed_memory_text("expired project memory"),
                expires_at=datetime.now(timezone.utc) - timedelta(days=1),
            )

            with patch.object(memory_service, "review_reconcile_findings_with_llm", return_value=[]):
                report = memory_service.reconcile_user_memories(session, user.id, apply=True, llm_review=True)
            session.refresh(first)
            session.refresh(duplicate)
            session.refresh(expired)
            reconciled_duplicates = [first, duplicate]
            active_duplicates = [memory for memory in reconciled_duplicates if memory.status == "active"]
            superseded_duplicates = [memory for memory in reconciled_duplicates if memory.status == "superseded"]

            self.assertGreaterEqual(report["applied_count"], 2)
            self.assertEqual(expired.status, "deleted")
            self.assertEqual(len(active_duplicates), 1)
            self.assertEqual(len(superseded_duplicates), 1)
            self.assertEqual(superseded_duplicates[0].superseded_by_id, active_duplicates[0].id)
            event_types = [
                event.event_type
                for event in session.scalars(select(UserMemoryEvent).where(UserMemoryEvent.user_id == user.id)).all()
            ]
            self.assertIn("expire", event_types)
            self.assertIn("supersede", event_types)

    def test_memory_reconcile_reports_semantic_candidates_without_applying(self) -> None:
        with (
            isolated_session() as session,
            patch("app.memory.embedding.get_embedding_provider", return_value=FakeEmbeddingProvider()),
        ):
            user = create_user(session, "memory-reconcile-semantic@example.com", "Memory Reconcile Semantic")
            first = memory_service.create_memory_row(
                session,
                user.id,
                "user works on project alpha",
                "user works on project alpha",
                memory_service.hash_content("user works on project alpha"),
                "project",
                memory_service.MemorySource(text="manual"),
                memory_service.embed_memory_text("user works on project alpha"),
            )
            second = memory_service.create_memory_row(
                session,
                user.id,
                "user works on project beta",
                "user works on project beta",
                memory_service.hash_content("user works on project beta"),
                "project",
                memory_service.MemorySource(text="manual"),
                memory_service.embed_memory_text("user works on project beta"),
            )

            with patch.object(memory_service, "review_reconcile_findings_with_llm", return_value=[]):
                report = memory_service.reconcile_user_memories(session, user.id, apply=True, llm_review=True)
            session.refresh(first)
            session.refresh(second)

            self.assertTrue(
                any(finding["finding_type"] == "semantic_relation_candidate" for finding in report["findings"])
            )
            self.assertEqual(first.status, "active")
            self.assertEqual(second.status, "active")

    def test_memory_reconcile_reports_missing_vector_without_applying(self) -> None:
        with (
            isolated_session() as session,
            patch("app.memory.embedding.get_embedding_provider", return_value=FakeEmbeddingProvider()),
            patch("app.memory.vector_index.is_memory_vector_index_enabled", return_value=True),
            patch("app.memory.vector_index.get_memory_vector_payloads", return_value={}),
            patch("app.memory.vector_index.try_sync_memory_vector") as sync_memory_vector,
        ):
            user = create_user(session, "memory-reconcile-vector-dry@example.com", "Memory Reconcile Vector Dry")
            memory = memory_service.create_manual_memory(session, user.id, "user works on vector recall", category="project")
            sync_memory_vector.reset_mock()

            report = memory_service.reconcile_user_memories(session, user.id, apply=False)

            self.assertTrue(any(finding["finding_type"] == "missing_vector" for finding in report["findings"]))
            self.assertEqual(report["applied_count"], 0)
            sync_memory_vector.assert_not_called()
            self.assertIsNone(
                session.scalar(
                    select(UserMemoryEvent).where(
                        UserMemoryEvent.memory_id == memory.id,
                        UserMemoryEvent.event_type == "vector_sync",
                    )
                )
            )

    def test_memory_reconcile_apply_repairs_missing_vector_with_event(self) -> None:
        with (
            isolated_session() as session,
            patch("app.memory.embedding.get_embedding_provider", return_value=FakeEmbeddingProvider()),
            patch("app.memory.vector_index.is_memory_vector_index_enabled", return_value=True),
            patch("app.memory.vector_index.get_memory_vector_payloads", return_value={}),
            patch("app.memory.vector_index.try_sync_memory_vector", return_value=True) as sync_memory_vector,
        ):
            user = create_user(session, "memory-reconcile-vector-apply@example.com", "Memory Reconcile Vector Apply")
            memory = memory_service.create_manual_memory(session, user.id, "user works on vector recall", category="project")
            sync_memory_vector.reset_mock()

            report = memory_service.reconcile_user_memories(session, user.id, apply=True)

            finding = next(finding for finding in report["findings"] if finding["finding_type"] == "missing_vector")
            self.assertTrue(finding["applied"])
            self.assertGreaterEqual(report["applied_count"], 1)
            sync_memory_vector.assert_called_with(memory)
            event = session.scalar(
                select(UserMemoryEvent).where(
                    UserMemoryEvent.memory_id == memory.id,
                    UserMemoryEvent.event_type == "vector_sync",
                )
            )
            self.assertIsNotNone(event)
            self.assertEqual(event.reason, "memory vector is missing")
            self.assertTrue(event.payload["vector_reconcile"])

    def test_memory_reconcile_apply_deletes_pending_memory_vector(self) -> None:
        with (
            isolated_session() as session,
            patch("app.memory.embedding.get_embedding_provider", return_value=FakeEmbeddingProvider()),
        ):
            user = create_user(session, "memory-reconcile-pending-vector@example.com", "Memory Reconcile Pending Vector")
            memory = memory_service.create_manual_memory(session, user.id, "user works on vector retention", category="project")
            memory_service.update_user_memory(session, user.id, memory.id, status="pending")

            with (
                patch("app.memory.vector_index.is_memory_vector_index_enabled", return_value=True),
                patch("app.memory.vector_index.get_memory_vector_payloads", return_value={memory.id: {"status": "pending"}}),
                patch("app.memory.vector_index.try_delete_memory_vector", return_value=True) as delete_memory_vector,
            ):
                report = memory_service.reconcile_user_memories(session, user.id, apply=True)

            finding = next(finding for finding in report["findings"] if finding["finding_type"] == "stale_vector")
            self.assertTrue(finding["applied"])
            delete_memory_vector.assert_called_with(memory.id)
            event = session.scalar(
                select(UserMemoryEvent).where(
                    UserMemoryEvent.memory_id == memory.id,
                    UserMemoryEvent.event_type == "vector_delete",
                )
            )
            self.assertIsNotNone(event)
            self.assertEqual(event.reason, "memory vector should be absent")
            self.assertTrue(event.payload["vector_reconcile"])

    def test_create_with_hidden_canonical_conflict_becomes_pending(self) -> None:
        from app.llm.provider import MemoryOperation

        with (
            isolated_session() as session,
            patch("app.memory.embedding.get_embedding_provider", return_value=ConflictEmbeddingProvider()),
        ):
            user = create_user(session, "memory-canonical-conflict@example.com", "Memory Canonical Conflict")
            old = memory_service.create_manual_memory(
                session,
                user.id,
                "user uses Django backend",
                category="project",
                kind="project",
                canonical_key="project:backend_framework",
            )

            action = memory_service.process_memory_operation(
                session,
                user.id,
                MemoryOperation(
                    action="create",
                    content="user uses FastAPI backend",
                    kind="project",
                    category="project",
                    canonical_key="project:backend_framework",
                    sensitivity="low",
                    evidence="I use FastAPI backend",
                ),
                memory_service.MemorySource(text="I use FastAPI backend"),
                user_message="I use FastAPI backend",
            )

            session.refresh(old)
            pending = session.get(UserMemory, action.memory_id)
            self.assertEqual(action.action, "pending")
            self.assertEqual(old.status, "active")
            self.assertIsNotNone(pending)
            self.assertEqual(pending.status, "pending")
            self.assertEqual(pending.canonical_key, "project:backend_framework")

    def test_hidden_canonical_conflict_is_reviewed_with_conflict_pack(self) -> None:
        from app.llm.provider import MemoryOperation

        primary_operation = MemoryOperation(
            action="create",
            content="user uses FastAPI backend",
            kind="project",
            category="project",
            canonical_key="project:backend_framework",
            sensitivity="low",
            evidence="I use FastAPI backend",
        )
        provider = ConflictReviewFakeLlmProvider([primary_operation])
        with (
            isolated_session() as session,
            patch.object(memory_service, "get_llm_provider", return_value=provider),
            patch("app.memory.embedding.get_embedding_provider", return_value=ConflictEmbeddingProvider()),
        ):
            user = create_user(session, "memory-conflict-pack@example.com", "Memory Conflict Pack")
            old = memory_service.create_manual_memory(
                session,
                user.id,
                "user uses Django backend",
                category="project",
                kind="project",
                canonical_key="project:backend_framework",
            )

            action = memory_service.process_user_memory(session, user.id, "I use FastAPI backend")[0]
            session.refresh(old)
            new_memory = session.get(UserMemory, action.memory_id)

            self.assertEqual(action.action, "supersede")
            self.assertEqual(old.status, "superseded")
            self.assertEqual(old.superseded_by_id, new_memory.id)
            self.assertEqual(new_memory.status, "active")
            self.assertEqual(new_memory.canonical_key, "project:backend_framework")
            self.assertEqual(provider.seen_conflict_operation["canonical_key"], "project:backend_framework")
            self.assertIn(old.id, {memory["id"] for memory in provider.seen_conflict_memories})

    def test_mandatory_memory_judge_failure_is_fail_closed(self) -> None:
        from app.llm.provider import MemoryOperation

        provider = FailingConflictReviewFakeLlmProvider(
            [
                MemoryOperation(
                    action="create",
                    content="user uses FastAPI backend",
                    kind="project",
                    category="project",
                    canonical_key="project:backend_framework",
                    sensitivity="low",
                    evidence="I use FastAPI backend",
                )
            ]
        )
        with (
            isolated_session() as session,
            patch.object(memory_service, "get_llm_provider", return_value=provider),
            patch("app.memory.embedding.get_embedding_provider", return_value=ConflictEmbeddingProvider()),
        ):
            user = create_user(session, "memory-conflict-review-fallback@example.com", "Memory Conflict Fallback")
            old = memory_service.create_manual_memory(
                session,
                user.id,
                "user uses Django backend",
                category="project",
                kind="project",
                canonical_key="project:backend_framework",
            )

            action = memory_service.process_user_memory(session, user.id, "I use FastAPI backend")[0]
            session.refresh(old)

            self.assertEqual(action.action, "ignore")
            self.assertIsNone(action.memory_id)
            self.assertEqual(old.status, "active")

    def test_llm_reconcile_review_creates_pending_without_changing_active_memories(self) -> None:
        from app.llm.provider import MemoryOperation

        provider = ReconcileReviewFakeLlmProvider(
            [
                MemoryOperation(
                    action="pending",
                    content="user works on project alpha and project beta",
                    kind="project",
                    category="project",
                    canonical_key="project:current_work",
                    sensitivity="low",
                    evidence="project alpha; project beta",
                    reason="semantic duplicates can be reviewed as one project memory",
                )
            ]
        )
        with (
            isolated_session() as session,
            patch.object(memory_service, "get_llm_provider", return_value=provider),
            patch("app.memory.embedding.get_embedding_provider", return_value=FakeEmbeddingProvider()),
        ):
            user = create_user(session, "memory-reconcile-llm@example.com", "Memory Reconcile LLM")
            first = memory_service.create_memory_row(
                session,
                user.id,
                "user works on project alpha",
                "user works on project alpha",
                memory_service.hash_content("user works on project alpha"),
                "project",
                memory_service.MemorySource(text="manual"),
                memory_service.embed_memory_text("user works on project alpha"),
            )
            second = memory_service.create_memory_row(
                session,
                user.id,
                "user works on project beta",
                "user works on project beta",
                memory_service.hash_content("user works on project beta"),
                "project",
                memory_service.MemorySource(text="manual"),
                memory_service.embed_memory_text("user works on project beta"),
            )

            report = memory_service.reconcile_user_memories(session, user.id, apply=True, llm_review=True)
            session.refresh(first)
            session.refresh(second)

            pending = session.scalars(
                select(UserMemory).where(
                    UserMemory.user_id == user.id,
                    UserMemory.status == "pending",
                    UserMemory.canonical_key == "project:current_work",
                )
            ).all()
            self.assertEqual(first.status, "active")
            self.assertEqual(second.status, "active")
            self.assertEqual(len(pending), 1)
            self.assertTrue(provider.seen_findings)
            self.assertTrue(provider.seen_memories)
            self.assertTrue(
                any(
                    finding["finding_type"] == "llm_reconcile_suggestion" and finding["applied"]
                    for finding in report["findings"]
                )
            )

    def test_no_memory_turn_does_not_build_context_or_persist_memory(self) -> None:
        with (
            isolated_session() as session,
            patch("app.services.memory_service.retrieve_relevant_memories") as retrieve_relevant_memories,
            patch.object(memory_service, "get_llm_provider") as get_llm_provider,
        ):
            user = create_user(session, "no-memory-turn@example.com", "No Memory Turn")

            context = memory_service.build_memory_context_for_question(
                session,
                user.id,
                "Please answer without memory.",
                conversation_id="conversation-id",
            )
            action = memory_service.process_user_memory(session, user.id, "Please answer without memory.")[0]

            retrieve_relevant_memories.assert_not_called()
            get_llm_provider.assert_not_called()
            self.assertIn("- None", context)
            self.assertEqual(action.action, "ignore")
            self.assertEqual(action.reason, "user requested no memory for this turn")

    def test_memory_source_prefers_explicit_message_id(self) -> None:
        with (
            isolated_session() as session,
            patch.object(memory_service, "get_llm_provider", return_value=StructuredFakeLlmProvider()),
            patch("app.memory.embedding.get_embedding_provider", return_value=FakeEmbeddingProvider()),
        ):
            user = create_user(session, "memory-source-message@example.com", "Memory Source Message")
            conversation = Conversation(user_id=user.id, title="Memory source", search_scope="public")
            session.add(conversation)
            session.commit()
            first = Message(conversation_id=conversation.id, role="user", content="I prefer concise answers")
            second = Message(conversation_id=conversation.id, role="user", content="I prefer concise answers")
            session.add_all([first, second])
            session.commit()
            session.refresh(first)
            session.refresh(second)

            action = memory_service.process_user_memory(
                session,
                user.id,
                "I prefer concise answers",
                conversation_id=conversation.id,
                message_id=first.id,
            )[0]

            memory = session.get(UserMemory, action.memory_id)
            self.assertEqual(memory.source_message_id, first.id)

    def test_semantic_memory_recall_writes_scored_log(self) -> None:
        provider = TrackingEmbeddingProvider()
        with isolated_session() as session, patch("app.memory.embedding.get_embedding_provider", return_value=provider):
            user = create_user(session, "memory-semantic-log@example.com", "Memory Semantic Log")
            memory = memory_service.create_manual_memory(session, user.id, "user works on an agentic RAG project", category="project")

            provider.calls.clear()
            recalled = memory_service.retrieve_relevant_memories(session, user.id, "agentic RAG architecture")

            self.assertEqual([item.id for item in recalled], [memory.id])
            self.assertIn("agentic RAG architecture", provider.calls)
            recall_log = session.scalar(
                select(UserMemoryRecallLog)
                .where(UserMemoryRecallLog.user_id == user.id, UserMemoryRecallLog.query == "agentic RAG architecture")
                .order_by(UserMemoryRecallLog.created_at.desc(), UserMemoryRecallLog.id.desc())
            )
            self.assertIsNotNone(recall_log)
            self.assertEqual(recall_log.recall_mode, "semantic")
            self.assertEqual(recall_log.selected_memory_ids, [memory.id])
            self.assertEqual(recall_log.candidates[0]["route"], "semantic_ranked")
            self.assertEqual(recall_log.candidates[0]["memory_layer"], "semantic")
            self.assertEqual(recall_log.candidates[0]["scope_id"], user.id)
            self.assertIsNotNone(recall_log.candidates[0]["score"])

    def test_semantic_memory_recall_limits_candidates_but_logs_total_active_count(self) -> None:
        with (
            isolated_session() as session,
            patch("app.memory.embedding.get_embedding_provider", return_value=FakeEmbeddingProvider()),
            patch("app.memory.vector_index.search_active_memories", return_value=[]),
            patch.object(memory_service, "MEMORY_RECALL_CANDIDATE_LIMIT", 4),
            patch.object(memory_service, "FULL_MEMORY_RECALL_LIMIT", 4),
        ):
            user = create_user(session, "memory-candidate-limit@example.com", "Memory Candidate Limit")
            for index in range(8):
                content = f"user project note {index}"
                normalized = memory_service.normalize_memory_content(content)
                memory_service.create_memory_row(
                    session,
                    user.id,
                    content,
                    normalized,
                    memory_service.hash_content(normalized),
                    "project",
                    memory_service.MemorySource(text="manual"),
                    memory_service.embed_memory_text(content),
                    kind="project",
                )

            recalled = memory_service.retrieve_relevant_memories(session, user.id, "project note", limit=2)

            self.assertEqual(len(recalled), 2)
            recall_log = session.scalar(
                select(UserMemoryRecallLog)
                .where(UserMemoryRecallLog.user_id == user.id, UserMemoryRecallLog.query == "project note")
                .order_by(UserMemoryRecallLog.created_at.desc(), UserMemoryRecallLog.id.desc())
            )
            self.assertIsNotNone(recall_log)
            self.assertEqual(recall_log.recall_mode, "semantic")
            self.assertEqual(recall_log.active_count, 8)
            self.assertEqual(len(recall_log.candidates), 4)

    def test_vector_memory_recall_writes_scored_log(self) -> None:
        provider = TrackingEmbeddingProvider()
        with (
            isolated_session() as session,
            patch("app.memory.embedding.get_embedding_provider", return_value=provider),
        ):
            user = create_user(session, "memory-vector-log@example.com", "Memory Vector Log")
            memory = memory_service.create_manual_memory(session, user.id, "user works on an agentic RAG project", category="project")

            with patch(
                "app.memory.vector_index.search_active_memories",
                return_value=[MemoryVectorHit(memory_id=memory.id, score=0.91, payload={})],
            ) as search:
                recalled = memory_service.retrieve_relevant_memories(session, user.id, "agentic RAG architecture")

            self.assertEqual([item.id for item in recalled], [memory.id])
            self.assertNotIn("score_threshold", search.call_args.kwargs)
            recall_log = session.scalar(
                select(UserMemoryRecallLog)
                .where(UserMemoryRecallLog.user_id == user.id, UserMemoryRecallLog.query == "agentic RAG architecture")
                .order_by(UserMemoryRecallLog.created_at.desc(), UserMemoryRecallLog.id.desc())
            )
            self.assertIsNotNone(recall_log)
            self.assertEqual(recall_log.recall_mode, "hybrid")
            self.assertEqual(recall_log.selected_memory_ids, [memory.id])
            self.assertEqual(recall_log.candidates[0]["route"], "vector_ranked")
            self.assertEqual(recall_log.candidates[0]["score"], 0.91)
            self.assertIsNone(recall_log.threshold)

    def test_vector_memory_recall_keeps_low_score_hits_for_llm_review(self) -> None:
        with (
            isolated_session() as session,
            patch("app.memory.embedding.get_embedding_provider", return_value=ConflictEmbeddingProvider()),
        ):
            user = create_user(session, "memory-vector-threshold@example.com", "Memory Vector Threshold")
            memory = memory_service.create_manual_memory(session, user.id, "user works on django payroll", category="project")

            with patch(
                "app.memory.vector_index.search_active_memories",
                return_value=[MemoryVectorHit(memory_id=memory.id, score=0.01, payload={})],
            ):
                recalled = memory_service.retrieve_relevant_memories(session, user.id, "fastapi vacation policy")

            self.assertEqual([item.id for item in recalled], [memory.id])
            recall_log = session.scalar(
                select(UserMemoryRecallLog)
                .where(UserMemoryRecallLog.user_id == user.id, UserMemoryRecallLog.query == "fastapi vacation policy")
                .order_by(UserMemoryRecallLog.created_at.desc(), UserMemoryRecallLog.id.desc())
            )
            self.assertIsNotNone(recall_log)
            self.assertEqual(recall_log.recall_mode, "hybrid")
            self.assertEqual(recall_log.selected_memory_ids, [memory.id])
            self.assertEqual(recall_log.candidates[0]["route"], "vector_ranked")
            self.assertIsNone(recall_log.threshold)

    def test_vector_memory_recall_failure_falls_back_to_semantic(self) -> None:
        with (
            isolated_session() as session,
            patch("app.memory.embedding.get_embedding_provider", return_value=FakeEmbeddingProvider()),
        ):
            user = create_user(session, "memory-vector-fallback@example.com", "Memory Vector Fallback")
            memory = memory_service.create_manual_memory(session, user.id, "user works on an agentic RAG project", category="project")

            with patch("app.memory.vector_index.search_active_memories", side_effect=RuntimeError("qdrant unavailable")):
                recalled = memory_service.retrieve_relevant_memories(session, user.id, "agentic RAG architecture")

            self.assertEqual([item.id for item in recalled], [memory.id])
            recall_log = session.scalar(
                select(UserMemoryRecallLog)
                .where(UserMemoryRecallLog.user_id == user.id, UserMemoryRecallLog.query == "agentic RAG architecture")
                .order_by(UserMemoryRecallLog.created_at.desc(), UserMemoryRecallLog.id.desc())
            )
            self.assertIsNotNone(recall_log)
            self.assertEqual(recall_log.recall_mode, "semantic")
            self.assertEqual(recall_log.selected_memory_ids, [memory.id])

    def test_memory_status_updates_are_limited_to_known_states(self) -> None:
        with (
            isolated_session() as session,
            patch("app.memory.embedding.get_embedding_provider", return_value=FakeEmbeddingProvider()),
        ):
            user = create_user(session, "memory-status@example.com", "Memory Status")
            memory = memory_service.create_manual_memory(session, user.id, "Use concise answers")

            updated = memory_service.update_user_memory(session, user.id, memory.id, status="ignored")
            self.assertEqual(updated.status, "ignored")

            restored = memory_service.update_user_memory(session, user.id, memory.id, status="active")
            self.assertEqual(restored.status, "active")

            deleted = memory_service.update_user_memory(session, user.id, memory.id, status="deleted")
            self.assertEqual(deleted.status, "deleted")
            self.assertIsNotNone(deleted.invalid_at)

            with self.assertRaises(HTTPException) as update_error:
                memory_service.update_user_memory(session, user.id, memory.id, status="archived")
            self.assertEqual(update_error.exception.status_code, 400)

            session.refresh(memory)
            self.assertEqual(memory.status, "deleted")

            with self.assertRaises(HTTPException) as list_error:
                memory_service.list_user_memories(session, user.id, status="archived")
            self.assertEqual(list_error.exception.status_code, 400)

    def test_delete_memory_is_soft_delete_and_hidden_by_default(self) -> None:
        with (
            isolated_session() as session,
            patch("app.memory.embedding.get_embedding_provider", return_value=FakeEmbeddingProvider()),
        ):
            user = create_user(session, "memory-soft-delete@example.com", "Memory Soft Delete")
            memory = memory_service.create_manual_memory(session, user.id, "Use concise answers")

            memory_service.delete_user_memory(session, user.id, memory.id)

            deleted = session.get(UserMemory, memory.id)
            self.assertIsNotNone(deleted)
            self.assertEqual(deleted.status, "deleted")
            self.assertIsNotNone(deleted.invalid_at)
            self.assertEqual(memory_service.list_user_memories(session, user.id), [])
            self.assertEqual(memory_service.list_user_memories(session, user.id, status="deleted")[0].id, memory.id)

            events = session.scalars(
                select(UserMemoryEvent)
                .where(UserMemoryEvent.memory_id == memory.id)
                .order_by(UserMemoryEvent.created_at.asc(), UserMemoryEvent.id.asc())
            ).all()
            self.assertIn("delete", [event.event_type for event in events])
            delete_event = [event for event in events if event.event_type == "delete"][-1]
            self.assertEqual(delete_event.actor_type, "user")
            self.assertEqual(delete_event.actor_user_id, user.id)
            self.assertEqual(delete_event.previous_status, "active")
            self.assertEqual(delete_event.new_status, "deleted")

    def test_deleted_memory_can_be_restored_with_audit_event(self) -> None:
        with (
            isolated_session() as session,
            patch("app.memory.embedding.get_embedding_provider", return_value=FakeEmbeddingProvider()),
            patch("app.memory.vector_index.try_sync_memory_vector") as sync_memory_vector,
        ):
            user = create_user(session, "memory-restore@example.com", "Memory Restore")
            memory = memory_service.create_manual_memory(session, user.id, "Use concise answers")
            memory_service.delete_user_memory(session, user.id, memory.id)

            restored = memory_service.restore_user_memory(session, user.id, memory.id)

            self.assertEqual(restored.status, "active")
            self.assertIsNone(restored.invalid_at)
            sync_memory_vector.assert_called()
            event = session.scalar(
                select(UserMemoryEvent)
                .where(UserMemoryEvent.memory_id == memory.id, UserMemoryEvent.event_type == "restore")
                .order_by(UserMemoryEvent.created_at.desc(), UserMemoryEvent.id.desc())
            )
            self.assertIsNotNone(event)
            self.assertEqual(event.previous_status, "deleted")
            self.assertEqual(event.new_status, "active")

    def test_memory_can_be_purged_after_soft_delete(self) -> None:
        with (
            isolated_session() as session,
            patch("app.memory.embedding.get_embedding_provider", return_value=FakeEmbeddingProvider()),
            patch("app.memory.vector_index.try_delete_memory_vector") as delete_memory_vector,
        ):
            user = create_user(session, "memory-purge@example.com", "Memory Purge")
            memory = memory_service.create_manual_memory(session, user.id, "Use concise answers")
            memory_service.delete_user_memory(session, user.id, memory.id)

            memory_service.purge_user_memory(session, user.id, memory.id)

            self.assertIsNone(session.get(UserMemory, memory.id))
            delete_memory_vector.assert_called_with(memory.id)
            event = session.scalar(
                select(UserMemoryEvent)
                .where(UserMemoryEvent.user_id == user.id, UserMemoryEvent.event_type == "purge")
                .order_by(UserMemoryEvent.created_at.desc(), UserMemoryEvent.id.desc())
            )
            self.assertIsNotNone(event)
            self.assertIsNone(event.memory_id)
            self.assertEqual(event.previous_status, "deleted")
            self.assertEqual(event.new_status, "purged")
            self.assertTrue(event.payload["erased"])
            self.assertEqual(event.payload["memory_id"], memory.id)
            self.assertNotIn("content", event.payload)
            self.assertNotIn("Use concise answers", str(event.payload))

    def test_missing_memory_returns_404(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "memory-missing@example.com", "Memory Missing")

            with self.assertRaises(HTTPException) as error:
                memory_service.get_user_memory_or_404(session, user.id, "missing-memory-id")

            self.assertEqual(error.exception.status_code, 404)

    def test_memory_export_includes_user_owned_governance_data(self) -> None:
        with (
            isolated_session() as session,
            patch("app.memory.embedding.get_embedding_provider", return_value=FakeEmbeddingProvider()),
        ):
            user = create_user(session, "memory-export@example.com", "Memory Export")
            other = create_user(session, "memory-export-other@example.com", "Memory Export Other")
            memory = memory_service.create_manual_memory(session, user.id, "Use concise answers")
            other_memory = memory_service.create_manual_memory(session, other.id, "Use detailed answers")
            session.add(
                UserMemoryRecallLog(
                    user_id=user.id,
                    query="concise?",
                    recall_mode="semantic",
                    requested_limit=5,
                    recall_limit=5,
                    active_count=1,
                    selected_count=1,
                    candidates=[],
                    selected_memory_ids=[memory.id],
                )
            )
            session.add(
                UserMemoryUpdateJob(
                    user_id=user.id,
                    user_message="I prefer concise answers",
                    assistant_message="Got it.",
                    status="completed",
                    actions=[{"action": "create", "memory_id": memory.id}],
                )
            )
            session.commit()

            export = memory_service.export_user_memory_data(session, user.id)

            self.assertEqual(export["user_id"], user.id)
            self.assertEqual([item.id for item in export["memories"]], [memory.id])
            self.assertEqual(export["memories"][0].memory_layer, "profile")
            self.assertEqual(export["memories"][0].profile_slot, "response_detail")
            self.assertTrue(export["memories"][0].pinned)
            self.assertNotIn(other_memory.id, [item.id for item in export["memories"]])
            self.assertTrue(any(event.memory_id == memory.id for event in export["events"]))
            self.assertTrue(any(event.payload.get("memory_layer") == "profile" for event in export["events"]))
            self.assertEqual(export["recall_logs"][0].query, "concise?")
            self.assertEqual(export["update_jobs"][0].user_message, "I prefer concise answers")

    def test_recall_metrics_aggregate_quality_signals(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "memory-metrics@example.com", "Memory Metrics")
            other = create_user(session, "memory-metrics-other@example.com", "Memory Metrics Other")
            session.add_all(
                [
                    UserMemoryRecallLog(
                        user_id=user.id,
                        query="agentic rag",
                        recall_mode="semantic",
                        requested_limit=5,
                        recall_limit=5,
                        active_count=3,
                        selected_count=1,
                        candidates=[
                            {
                                "memory_id": "memory-a",
                                "category": "project",
                                "memory_layer": "semantic",
                                "route": "semantic",
                                "score": 0.82,
                                "selected": True,
                            },
                            {
                                "memory_id": "memory-b",
                                "category": "format",
                                "memory_layer": "profile",
                                "profile_slot": "format",
                                "route": "below_threshold",
                                "score": 0.1,
                                "selected": False,
                            },
                        ],
                        selected_memory_ids=["memory-a"],
                    ),
                    UserMemoryRecallLog(
                        user_id=user.id,
                        query="vacation policy",
                        recall_mode="vector",
                        requested_limit=5,
                        recall_limit=5,
                        active_count=3,
                        selected_count=0,
                        candidates=[
                            {
                                "memory_id": "memory-c",
                                "category": "project",
                                "memory_layer": "semantic",
                                "route": "below_threshold",
                                "score": 0.2,
                                "selected": False,
                            }
                        ],
                        selected_memory_ids=[],
                    ),
                    UserMemoryRecallLog(
                        user_id=user.id,
                        query="embedding down",
                        recall_mode="fallback_no_embedding",
                        requested_limit=5,
                        recall_limit=5,
                        active_count=2,
                        selected_count=2,
                        candidates=[],
                        selected_memory_ids=["memory-a", "memory-d"],
                    ),
                    UserMemoryRecallLog(
                        user_id=other.id,
                        query="other",
                        recall_mode="semantic",
                        requested_limit=5,
                        recall_limit=5,
                        active_count=10,
                        selected_count=10,
                        candidates=[],
                        selected_memory_ids=["other-memory"],
                    ),
                ]
            )
            session.commit()

            metrics = memory_service.get_user_memory_recall_metrics(session, user.id)

            self.assertEqual(metrics["total_logs"], 3)
            self.assertEqual(metrics["recall_mode_counts"], {"fallback_no_embedding": 1, "semantic": 1, "vector": 1})
            self.assertEqual(metrics["route_counts"], {"below_threshold": 2, "semantic": 1})
            self.assertEqual(metrics["route_selected_counts"], {"semantic": 1})
            self.assertEqual(metrics["category_counts"], {"format": 1, "project": 2})
            self.assertEqual(metrics["memory_layer_counts"], {"profile": 1, "semantic": 2})
            self.assertEqual(metrics["profile_slot_counts"], {"format": 1})
            self.assertEqual(metrics["empty_result_count"], 1)
            self.assertEqual(metrics["empty_result_rate"], 0.333333)
            self.assertEqual(metrics["fallback_count"], 1)
            self.assertEqual(metrics["vector_count"], 1)
            self.assertEqual(metrics["below_threshold_candidate_count"], 2)
            self.assertEqual(metrics["average_selected_count"], 1.0)
            self.assertEqual(metrics["average_active_count"], 2.666667)
            self.assertEqual(metrics["average_top_score"], 0.51)
            self.assertEqual(metrics["unique_selected_memory_count"], 2)
            self.assertEqual(metrics["top_selected_memories"][0], {"memory_id": "memory-a", "count": 2})

    def test_manual_memory_rejects_blank_content(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "blank-memory@example.com", "Blank Memory")

            with self.assertRaises(HTTPException) as create_error:
                memory_service.create_manual_memory(session, user.id, "   ")

            self.assertEqual(create_error.exception.status_code, 400)

    def test_manual_memory_auto_classifies_without_user_supplied_category(self) -> None:
        with (
            isolated_session() as session,
            patch("app.memory.embedding.get_embedding_provider", return_value=FakeEmbeddingProvider()),
            patch(
                "app.services.memory_service.get_llm_provider",
                return_value=FakeMemoryClassificationProvider("profile", "current_project"),
            ),
        ):
            user = create_user(session, "auto-classified-memory@example.com", "Auto Classified Memory")

            memory = memory_service.create_manual_memory(
                session,
                user.id,
                "User is building an Agentic RAG project",
                auto_classify=True,
            )

            self.assertEqual(memory.kind, "profile")
            self.assertEqual(memory.category, "current_project")
            self.assertEqual(memory.memory_layer, "semantic")
            self.assertFalse(memory.pinned)
            classifier_log = session.scalar(
                select(LlmCallLog).where(
                    LlmCallLog.user_id == user.id,
                    LlmCallLog.agent_name == "memory_classifier",
                )
            )
            self.assertIsNotNone(classifier_log)

    def test_manual_memory_content_edit_is_reclassified(self) -> None:
        with (
            isolated_session() as session,
            patch("app.memory.embedding.get_embedding_provider", return_value=FakeEmbeddingProvider()),
        ):
            user = create_user(session, "reclassified-memory@example.com", "Reclassified Memory")
            memory = memory_service.create_manual_memory(session, user.id, "User works on a temporary project")

            with patch(
                "app.services.memory_service.get_llm_provider",
                return_value=FakeMemoryClassificationProvider("profile", "name"),
            ):
                updated = memory_service.update_user_memory(
                    session,
                    user.id,
                    memory.id,
                    content="User prefers to be called Alice",
                    auto_classify=True,
                )

            self.assertEqual(updated.category, "name")
            self.assertEqual(updated.memory_layer, "profile")
            self.assertEqual(updated.profile_slot, "name")
            self.assertTrue(updated.pinned)

    def test_global_instruction_requires_explicit_global_scope(self) -> None:
        with (
            isolated_session() as session,
            patch("app.memory.embedding.get_embedding_provider", return_value=FakeEmbeddingProvider()),
            patch(
                "app.services.memory_service.get_llm_provider",
                return_value=FakeMemoryClassificationProvider("instruction", "global_instruction"),
            ),
        ):
            user = create_user(session, "scoped-instruction@example.com", "Scoped Instruction")

            memory = memory_service.create_manual_memory(
                session,
                user.id,
                "Use citations when answering policy questions",
                auto_classify=True,
            )

            self.assertEqual(memory.category, "task_instruction")
            self.assertEqual(memory.memory_layer, "procedural")
            self.assertFalse(memory.pinned)

    def test_manual_memory_requires_confirmation_for_sensitive_content(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "manual-sensitive-memory@example.com", "Manual Sensitive Memory")

            with self.assertRaises(HTTPException) as create_error:
                memory_service.create_manual_memory(
                    session,
                    user.id,
                    "user api key is sk-test-secret-value-1234567890",
                )

            rows = session.scalars(select(UserMemory).where(UserMemory.user_id == user.id)).all()
            self.assertEqual(create_error.exception.status_code, 400)
            self.assertEqual(create_error.exception.detail, memory_service.SENSITIVE_MEMORY_CONFIRMATION_REQUIRED)
            self.assertEqual(rows, [])

    def test_manual_memory_can_save_sensitive_content_with_explicit_confirmation(self) -> None:
        with (
            isolated_session() as session,
            patch("app.memory.embedding.get_embedding_provider", return_value=FakeEmbeddingProvider()),
        ):
            user = create_user(session, "manual-sensitive-confirmed@example.com", "Manual Sensitive Confirmed")

            memory = memory_service.create_manual_memory(
                session,
                user.id,
                "user api key is sk-test-secret-value-1234567890",
                allow_sensitive=True,
            )
            updated = memory_service.update_user_memory(
                session,
                user.id,
                memory.id,
                content="user api key is sk-test-secret-value-0987654321",
                allow_sensitive=True,
            )

            self.assertEqual(updated.id, memory.id)
            self.assertIn("0987654321", updated.content)

    def test_update_memory_rejects_blank_content(self) -> None:
        with (
            isolated_session() as session,
            patch("app.memory.embedding.get_embedding_provider", return_value=FakeEmbeddingProvider()),
        ):
            user = create_user(session, "blank-memory-update@example.com", "Blank Memory Update")
            memory = memory_service.create_manual_memory(session, user.id, "Use concise answers")

            with self.assertRaises(HTTPException) as update_error:
                memory_service.update_user_memory(session, user.id, memory.id, content="   ")

            self.assertEqual(update_error.exception.status_code, 400)

    def test_update_memory_requires_confirmation_for_sensitive_content(self) -> None:
        with (
            isolated_session() as session,
            patch("app.memory.embedding.get_embedding_provider", return_value=FakeEmbeddingProvider()),
        ):
            user = create_user(session, "manual-sensitive-update@example.com", "Manual Sensitive Update")
            memory = memory_service.create_manual_memory(session, user.id, "Use concise answers")

            with self.assertRaises(HTTPException) as update_error:
                memory_service.update_user_memory(
                    session,
                    user.id,
                    memory.id,
                    content="user api key is sk-test-secret-value-1234567890",
                )

            session.refresh(memory)
            self.assertEqual(update_error.exception.status_code, 400)
            self.assertEqual(update_error.exception.detail, memory_service.SENSITIVE_MEMORY_CONFIRMATION_REQUIRED)
            self.assertEqual(memory.content, "Use concise answers")

    def test_manual_memory_update_rejects_a_stale_revision(self) -> None:
        with (
            isolated_session() as session,
            patch("app.memory.embedding.get_embedding_provider", return_value=FakeEmbeddingProvider()),
        ):
            user = create_user(session, "stale-manual-memory@example.com", "Stale Manual Memory")
            memory = memory_service.create_manual_memory(session, user.id, "Use concise answers")
            stale_revision = memory.revision
            memory_service.update_user_memory(
                session,
                user.id,
                memory.id,
                content="Use detailed answers",
                expected_revision=stale_revision,
            )

            with self.assertRaises(HTTPException) as update_error:
                memory_service.update_user_memory(
                    session,
                    user.id,
                    memory.id,
                    content="Use bullet points",
                    expected_revision=stale_revision,
                )

            session.refresh(memory)
            self.assertEqual(update_error.exception.status_code, 409)
            self.assertEqual(memory.content, "Use detailed answers")

    def test_pending_memory_can_be_approved_with_audit_event(self) -> None:
        with (
            isolated_session() as session,
            patch.object(memory_service, "get_llm_provider", return_value=StructuredFakeLlmProvider()),
            patch("app.memory.embedding.get_embedding_provider", return_value=FakeEmbeddingProvider()),
        ):
            user = create_user(session, "memory-approve@example.com", "Memory Approve")
            action = memory_service.process_user_memory(session, user.id, "Remember this temporary detail")[0]

            approved = memory_service.approve_user_memory(session, user.id, action.memory_id)

            self.assertEqual(approved.status, "active")
            self.assertIsNone(approved.invalid_at)
            event = session.scalar(
                select(UserMemoryEvent)
                .where(UserMemoryEvent.memory_id == approved.id, UserMemoryEvent.event_type == "approve")
                .order_by(UserMemoryEvent.created_at.desc(), UserMemoryEvent.id.desc())
            )
            self.assertIsNotNone(event)
            self.assertEqual(event.actor_type, "user")
            self.assertEqual(event.previous_status, "pending")
            self.assertEqual(event.new_status, "active")

    def test_memory_governance_actions_are_mirrored_to_global_audit_log(self) -> None:
        with (
            isolated_session() as session,
            patch("app.memory.embedding.get_embedding_provider", return_value=FakeEmbeddingProvider()),
            patch("app.memory.vector_index.try_sync_memory_vector"),
            patch("app.memory.vector_index.try_delete_memory_vector"),
        ):
            user = create_user(session, "memory-global-audit@example.com", "Memory Global Audit")
            memory = memory_service.create_manual_memory(
                session,
                user.id,
                "sensitive launch details",
                category="project",
                kind="project",
                canonical_key="project:launch_notes",
            )
            memory = memory_service.update_user_memory(
                session,
                user.id,
                memory.id,
                content="sensitive launch details updated",
            )
            pending_approve = memory_service.create_memory_row(
                session,
                user.id,
                "pending detail to approve",
                "pending detail to approve",
                memory_service.hash_content("pending detail to approve"),
                "general",
                memory_service.MemorySource(text="manual"),
                memory_service.embed_memory_text("pending detail to approve"),
                status="pending",
            )
            pending_reject = memory_service.create_memory_row(
                session,
                user.id,
                "pending detail to reject",
                "pending detail to reject",
                memory_service.hash_content("pending detail to reject"),
                "general",
                memory_service.MemorySource(text="manual"),
                memory_service.embed_memory_text("pending detail to reject"),
                status="pending",
            )

            memory_service.approve_user_memory(session, user.id, pending_approve.id)
            memory_service.reject_user_memory(session, user.id, pending_reject.id)
            memory_service.delete_user_memory(session, user.id, memory.id)
            memory_service.restore_user_memory(session, user.id, memory.id)
            memory_service.delete_user_memory(session, user.id, memory.id)
            memory_service.purge_user_memory(session, user.id, memory.id)

            audit_logs = session.scalars(
                select(AuditLog).where(
                    AuditLog.actor_user_id == user.id,
                    AuditLog.resource_type == "user_memory",
                )
            ).all()
            governance_actions = {
                "memory.create",
                "memory.update",
                "memory.approve",
                "memory.reject",
                "memory.delete",
                "memory.restore",
                "memory.purge",
            }
            actions = {log.action for log in audit_logs}

            self.assertTrue(governance_actions.issubset(actions))
            self.assertTrue(
                all(
                    log.outcome == "success"
                    for log in audit_logs
                    if log.action in governance_actions
                )
            )
            self.assertTrue(all(log.resource_id for log in audit_logs))
            metadata_text = str([log.extra_metadata for log in audit_logs])
            self.assertNotIn("sensitive launch details", metadata_text)
            self.assertNotIn("pending detail", metadata_text)
            purge_log = next(log for log in audit_logs if log.action == "memory.purge")
            self.assertTrue(purge_log.extra_metadata["erased"])
            self.assertEqual(purge_log.extra_metadata["memory_id"], memory.id)

    def test_approving_pending_memory_supersedes_active_canonical_conflict(self) -> None:
        with (
            isolated_session() as session,
            patch("app.memory.embedding.get_embedding_provider", return_value=ConflictEmbeddingProvider()),
        ):
            user = create_user(session, "memory-approve-conflict@example.com", "Memory Approve Conflict")
            old = memory_service.create_memory_row(
                session,
                user.id,
                "user uses Django backend",
                "user uses django backend",
                memory_service.hash_content("user uses django backend"),
                "project",
                memory_service.MemorySource(text="manual"),
                memory_service.embed_memory_text("user uses django backend"),
                kind="project",
                canonical_key="project:backend_framework",
            )
            pending = memory_service.create_memory_row(
                session,
                user.id,
                "user uses FastAPI backend",
                "user uses fastapi backend",
                memory_service.hash_content("user uses fastapi backend"),
                "project",
                memory_service.MemorySource(text="manual"),
                memory_service.embed_memory_text("user uses fastapi backend"),
                status="pending",
                kind="project",
                canonical_key="project:backend_framework",
            )

            approved = memory_service.approve_user_memory(session, user.id, pending.id)
            session.refresh(old)

            self.assertEqual(approved.status, "active")
            self.assertEqual(old.status, "superseded")
            self.assertEqual(old.superseded_by_id, approved.id)
            event = session.scalar(
                select(UserMemoryEvent)
                .where(UserMemoryEvent.memory_id == approved.id, UserMemoryEvent.event_type == "approve")
                .order_by(UserMemoryEvent.created_at.desc(), UserMemoryEvent.id.desc())
            )
            self.assertEqual(event.payload["superseded_conflict_ids"], [old.id])

    def test_manual_status_activation_supersedes_active_canonical_conflict(self) -> None:
        with (
            isolated_session() as session,
            patch("app.memory.embedding.get_embedding_provider", return_value=ConflictEmbeddingProvider()),
        ):
            user = create_user(session, "memory-status-conflict@example.com", "Memory Status Conflict")
            old = memory_service.create_memory_row(
                session,
                user.id,
                "user uses Django backend",
                "user uses django backend",
                memory_service.hash_content("user uses django backend"),
                "project",
                memory_service.MemorySource(text="manual"),
                memory_service.embed_memory_text("user uses django backend"),
                kind="project",
                canonical_key="project:backend_framework",
            )
            pending = memory_service.create_memory_row(
                session,
                user.id,
                "user uses FastAPI backend",
                "user uses fastapi backend",
                memory_service.hash_content("user uses fastapi backend"),
                "project",
                memory_service.MemorySource(text="manual"),
                memory_service.embed_memory_text("user uses fastapi backend"),
                status="pending",
                kind="project",
                canonical_key="project:backend_framework",
            )

            activated = memory_service.update_user_memory(session, user.id, pending.id, status="active")
            session.refresh(old)

            self.assertEqual(activated.status, "active")
            self.assertEqual(old.status, "superseded")
            self.assertEqual(old.superseded_by_id, activated.id)

    def test_restoring_deleted_memory_supersedes_active_canonical_conflict(self) -> None:
        with (
            isolated_session() as session,
            patch("app.memory.embedding.get_embedding_provider", return_value=ConflictEmbeddingProvider()),
        ):
            user = create_user(session, "memory-restore-conflict@example.com", "Memory Restore Conflict")
            deleted = memory_service.create_memory_row(
                session,
                user.id,
                "user uses Django backend",
                "user uses django backend",
                memory_service.hash_content("user uses django backend"),
                "project",
                memory_service.MemorySource(text="manual"),
                memory_service.embed_memory_text("user uses django backend"),
                kind="project",
                canonical_key="project:backend_framework",
            )
            memory_service.delete_user_memory(session, user.id, deleted.id)
            active = memory_service.create_memory_row(
                session,
                user.id,
                "user uses FastAPI backend",
                "user uses fastapi backend",
                memory_service.hash_content("user uses fastapi backend"),
                "project",
                memory_service.MemorySource(text="manual"),
                memory_service.embed_memory_text("user uses fastapi backend"),
                kind="project",
                canonical_key="project:backend_framework",
            )

            restored = memory_service.restore_user_memory(session, user.id, deleted.id)
            session.refresh(active)

            self.assertEqual(restored.status, "active")
            self.assertEqual(active.status, "superseded")
            self.assertEqual(active.superseded_by_id, restored.id)

    def test_format_profile_memory_is_singleton_instead_of_merged(self) -> None:
        with (
            isolated_session() as session,
            patch("app.memory.embedding.get_embedding_provider", return_value=FakeEmbeddingProvider()),
        ):
            user = create_user(session, "memory-format-singleton@example.com", "Memory Format Singleton")
            old = memory_service.create_manual_memory(
                session,
                user.id,
                "user prefers table output",
                category="format",
            )

            new = memory_service.create_manual_memory(
                session,
                user.id,
                "user prefers JSON output",
                category="format",
            )
            session.refresh(old)

            active = session.scalars(
                select(UserMemory).where(
                    UserMemory.user_id == user.id,
                    UserMemory.status == "active",
                    UserMemory.profile_slot == "format",
                )
            ).all()
            self.assertEqual(len(active), 1)
            self.assertEqual(active[0].id, new.id)
            self.assertEqual(new.canonical_key, "profile:format")
            self.assertEqual(old.status, "superseded")
            self.assertEqual(old.superseded_by_id, new.id)
            self.assertNotIn("table output; user prefers JSON output", new.content)

    def test_purge_memory_keeps_only_redacted_audit_payload(self) -> None:
        with (
            isolated_session() as session,
            patch("app.memory.embedding.get_embedding_provider", return_value=FakeEmbeddingProvider()),
            patch("app.memory.vector_index.try_delete_memory_vector"),
        ):
            user = create_user(session, "memory-purge-redacted@example.com", "Memory Purge Redacted")
            memory = memory_service.create_manual_memory(
                session,
                user.id,
                "user prefers private launch details",
                category="project",
            )
            memory_id = memory.id
            session.add(
                UserMemoryRecallLog(
                    user_id=user.id,
                    query="launch details",
                    recall_mode="semantic",
                    requested_limit=5,
                    recall_limit=5,
                    active_count=2,
                    selected_count=2,
                    candidates=[
                        {"memory_id": memory_id, "category": "project", "route": "semantic", "selected": True},
                        {"memory_id": "other-memory", "category": "project", "route": "semantic", "selected": True},
                    ],
                    selected_memory_ids=[memory_id, "other-memory"],
                )
            )
            session.add(
                UserMemoryUpdateJob(
                    user_id=user.id,
                    user_message="user prefers private launch details",
                    assistant_message="Got it.",
                    status="completed",
                    actions=[{"action": "create", "memory_id": memory_id, "content": "user prefers private launch details"}],
                )
            )
            run = AgentRun(
                user_id=user.id,
                input="What do you remember?",
                status="completed",
                answer="",
                citations=[],
                trace=[
                    {
                        "node": "memory_agent",
                        "output": {
                            "actions": [
                                {
                                    "action": "create",
                                    "memory_id": memory_id,
                                    "content": "user prefers private launch details",
                                }
                            ]
                        },
                    }
                ],
                state={
                    "long_term_memories": [
                        {
                            "id": memory_id,
                            "content": "user prefers private launch details",
                            "category": "project",
                        }
                    ],
                    "memory_actions": [
                        {
                            "action": "create",
                            "memory_id": memory_id,
                            "content": "user prefers private launch details",
                        }
                    ],
                },
            )
            session.add(run)
            session.commit()

            memory_service.purge_user_memory(session, user.id, memory_id)
            session.refresh(run)

            self.assertIsNone(session.get(UserMemory, memory_id))
            event = session.scalar(
                select(UserMemoryEvent)
                .where(UserMemoryEvent.user_id == user.id, UserMemoryEvent.event_type == "purge")
                .order_by(UserMemoryEvent.created_at.desc(), UserMemoryEvent.id.desc())
            )
            self.assertIsNotNone(event)
            self.assertTrue(event.payload["erased"])
            self.assertEqual(event.payload["memory_id"], memory_id)
            self.assertNotIn("content", event.payload)
            self.assertNotIn("source_text", event.payload)
            self.assertNotIn("metadata", event.payload)
            self.assertNotIn("content_hash", event.payload)
            self.assertNotIn("private launch details", str(event.payload))
            self.assertGreaterEqual(event.payload["redacted_event_count"], 1)
            self.assertEqual(event.payload["redacted_recall_log_count"], 1)
            self.assertEqual(event.payload["redacted_update_job_count"], 1)
            self.assertEqual(event.payload["redacted_agent_run_count"], 1)

            historical_events = session.scalars(
                select(UserMemoryEvent).where(
                    UserMemoryEvent.user_id == user.id,
                    UserMemoryEvent.event_type != "purge",
                )
            ).all()
            self.assertTrue(historical_events)
            self.assertTrue(all(item.memory_id is None for item in historical_events))
            self.assertTrue(all(item.payload.get("erased") is True for item in historical_events))
            self.assertNotIn("private launch details", str([item.payload for item in historical_events]))

            recall_log = session.scalar(select(UserMemoryRecallLog).where(UserMemoryRecallLog.user_id == user.id))
            self.assertEqual(recall_log.selected_memory_ids, ["other-memory"])
            self.assertEqual(recall_log.selected_count, 1)
            self.assertEqual([candidate["memory_id"] for candidate in recall_log.candidates], ["other-memory"])

            job = session.scalar(select(UserMemoryUpdateJob).where(UserMemoryUpdateJob.user_id == user.id))
            self.assertEqual(job.user_message, memory_service.PURGED_MEMORY_REDACTION_TEXT)
            self.assertEqual(job.assistant_message, "")
            self.assertTrue(job.actions[0]["redacted"])
            self.assertIsNone(job.actions[0]["memory_id"])
            self.assertNotIn("private launch details", str(job.actions))

            self.assertTrue(run.state["long_term_memories"][0]["redacted"])
            self.assertIsNone(run.state["long_term_memories"][0]["id"])
            self.assertTrue(run.state["memory_actions"][0]["redacted"])
            self.assertIsNone(run.state["memory_actions"][0]["memory_id"])
            self.assertTrue(run.trace[0]["output"]["actions"][0]["redacted"])
            self.assertNotIn("private launch details", str(run.state))
            self.assertNotIn("private launch details", str(run.trace))

    def test_pending_memory_can_be_rejected_with_audit_event(self) -> None:
        with (
            isolated_session() as session,
            patch.object(memory_service, "get_llm_provider", return_value=StructuredFakeLlmProvider()),
            patch("app.memory.embedding.get_embedding_provider", return_value=FakeEmbeddingProvider()),
        ):
            user = create_user(session, "memory-reject@example.com", "Memory Reject")
            action = memory_service.process_user_memory(session, user.id, "Remember this temporary detail")[0]

            rejected = memory_service.reject_user_memory(session, user.id, action.memory_id)

            self.assertEqual(rejected.status, "ignored")
            self.assertIsNotNone(rejected.invalid_at)
            event = session.scalar(
                select(UserMemoryEvent)
                .where(UserMemoryEvent.memory_id == rejected.id, UserMemoryEvent.event_type == "reject")
                .order_by(UserMemoryEvent.created_at.desc(), UserMemoryEvent.id.desc())
            )
            self.assertIsNotNone(event)
            self.assertEqual(event.actor_type, "user")
            self.assertEqual(event.previous_status, "pending")
            self.assertEqual(event.new_status, "ignored")

            with self.assertRaises(HTTPException) as approve_error:
                memory_service.approve_user_memory(session, user.id, rejected.id)
            self.assertEqual(approve_error.exception.status_code, 409)

    def test_pending_memory_operation_is_not_loaded(self) -> None:
        with (
            isolated_session() as session,
            patch.object(memory_service, "get_llm_provider", return_value=StructuredFakeLlmProvider()),
            patch("app.memory.embedding.get_embedding_provider", return_value=FakeEmbeddingProvider()),
        ):
            user = create_user(session, "memory-pending@example.com", "Memory Pending")

            action = memory_service.process_user_memory(session, user.id, "Remember this temporary detail")[0]

            self.assertEqual(action.action, "pending")
            pending = session.get(UserMemory, action.memory_id)
            self.assertIsNotNone(pending)
            self.assertEqual(pending.status, "pending")

            context = memory_service.build_memory_context_for_question(session, user.id, "What do you know?")
            self.assertNotIn("temporary pending detail", context)

    def test_structured_memory_candidate_can_create_active_memory(self) -> None:
        with (
            isolated_session() as session,
            patch.object(memory_service, "get_llm_provider", return_value=StructuredFakeLlmProvider()),
            patch("app.memory.embedding.get_embedding_provider", return_value=FakeEmbeddingProvider()),
        ):
            user = create_user(session, "memory-structured@example.com", "Memory Structured")

            action = memory_service.process_user_memory(session, user.id, "I prefer concise answers")[0]

            self.assertEqual(action.action, "create")
            memory = session.get(UserMemory, action.memory_id)
            self.assertIsNotNone(memory)
            self.assertEqual(memory.status, "active")
            self.assertEqual(memory.category, "response_detail")
            self.assertEqual(memory.extra_metadata["importance"], "high")
            self.assertEqual(memory.extra_metadata["evidence"], "I prefer concise answers")

    def test_pending_memory_is_deduplicated_and_can_be_promoted(self) -> None:
        with (
            isolated_session() as session,
            patch.object(memory_service, "get_llm_provider", return_value=PromotingFakeLlmProvider()),
            patch("app.memory.embedding.get_embedding_provider", return_value=FakeEmbeddingProvider()),
        ):
            user = create_user(session, "memory-promote@example.com", "Memory Promote")

            first = memory_service.process_user_memory(session, user.id, "Remember this temporary detail")[0]
            second = memory_service.process_user_memory(session, user.id, "Remember this temporary detail")[0]
            promoted = memory_service.process_user_memory(session, user.id, "I am sure about this temporary detail")[0]

            rows = session.scalars(select(UserMemory).where(UserMemory.user_id == user.id)).all()
            memory = session.get(UserMemory, first.memory_id)

            self.assertEqual(first.action, "pending")
            self.assertEqual(second.action, "touch")
            self.assertEqual(promoted.action, "touch")
            self.assertEqual(len(rows), 1)
            self.assertEqual(memory.status, "active")
            self.assertEqual(memory.extra_metadata["decision"], "auto_activated_from_pending")

    def test_touch_promoting_pending_memory_supersedes_active_canonical_conflict(self) -> None:
        with (
            isolated_session() as session,
            patch("app.memory.embedding.get_embedding_provider", return_value=ConflictEmbeddingProvider()),
        ):
            user = create_user(session, "memory-touch-promote-conflict@example.com", "Memory Touch Promote Conflict")
            old = memory_service.create_memory_row(
                session,
                user.id,
                "user uses Django backend",
                "user uses django backend",
                memory_service.hash_content("user uses django backend"),
                "project",
                memory_service.MemorySource(text="manual"),
                memory_service.embed_memory_text("user uses django backend"),
                kind="project",
                canonical_key="project:backend_framework",
            )
            pending = memory_service.create_memory_row(
                session,
                user.id,
                "user uses FastAPI backend",
                "user uses fastapi backend",
                memory_service.hash_content("user uses fastapi backend"),
                "project",
                memory_service.MemorySource(text="manual"),
                memory_service.embed_memory_text("user uses fastapi backend"),
                status="pending",
                kind="project",
                canonical_key="project:backend_framework",
            )

            action = memory_service.touch_exact_memory(session, pending, activate_pending=True)
            session.refresh(old)
            session.refresh(pending)

            self.assertEqual(action.action, "touch")
            self.assertEqual(pending.status, "active")
            self.assertEqual(old.status, "superseded")
            self.assertEqual(old.superseded_by_id, pending.id)
            self.assertEqual(pending.extra_metadata["superseded_conflict_ids"], [old.id])

    def test_manual_active_update_supersedes_active_canonical_conflict(self) -> None:
        with (
            isolated_session() as session,
            patch("app.memory.embedding.get_embedding_provider", return_value=ConflictEmbeddingProvider()),
        ):
            user = create_user(session, "memory-manual-update-conflict@example.com", "Memory Manual Update Conflict")
            old = memory_service.create_memory_row(
                session,
                user.id,
                "user uses Django backend",
                "user uses django backend",
                memory_service.hash_content("user uses django backend"),
                "project",
                memory_service.MemorySource(text="manual"),
                memory_service.embed_memory_text("user uses django backend"),
                kind="project",
                canonical_key="project:backend_framework",
            )
            target = memory_service.create_memory_row(
                session,
                user.id,
                "user tracks frontend framework separately",
                "user tracks frontend framework separately",
                memory_service.hash_content("user tracks frontend framework separately"),
                "project",
                memory_service.MemorySource(text="manual"),
                memory_service.embed_memory_text("user tracks frontend framework separately"),
                kind="project",
                canonical_key="project:frontend_framework",
            )

            updated = memory_service.update_user_memory(
                session,
                user.id,
                target.id,
                content="user uses FastAPI backend",
                kind="project",
                category="project",
                canonical_key="project:backend_framework",
            )
            session.refresh(old)

            self.assertEqual(updated.status, "active")
            self.assertEqual(updated.canonical_key, "project:backend_framework")
            self.assertEqual(old.status, "superseded")
            self.assertEqual(old.superseded_by_id, updated.id)

    def test_memory_editor_update_supersedes_active_canonical_conflict(self) -> None:
        from app.llm.provider import MemoryOperation

        with (
            isolated_session() as session,
            patch("app.memory.embedding.get_embedding_provider", return_value=ConflictEmbeddingProvider()),
        ):
            user = create_user(session, "memory-editor-update-conflict@example.com", "Memory Editor Update Conflict")
            old = memory_service.create_memory_row(
                session,
                user.id,
                "user uses Django backend",
                "user uses django backend",
                memory_service.hash_content("user uses django backend"),
                "project",
                memory_service.MemorySource(text="manual"),
                memory_service.embed_memory_text("user uses django backend"),
                kind="project",
                canonical_key="project:backend_framework",
            )
            target = memory_service.create_memory_row(
                session,
                user.id,
                "user tracks a backend implementation note",
                "user tracks a backend implementation note",
                memory_service.hash_content("user tracks a backend implementation note"),
                "project",
                memory_service.MemorySource(text="manual"),
                memory_service.embed_memory_text("user tracks a backend implementation note"),
                kind="project",
                canonical_key="project:backend_note",
            )
            provider = ReviewFakeLlmProvider(
                [
                    MemoryOperation(
                        action="update",
                        target_memory_id=target.id,
                        content="user uses FastAPI backend",
                        kind="project",
                        category="project",
                        canonical_key="project:backend_framework",
                        importance="high",
                        sensitivity="low",
                        evidence="I use FastAPI backend",
                        reason="backend framework updated",
                    )
                ]
            )

            with patch.object(memory_service, "get_llm_provider", return_value=provider):
                action = memory_service.process_user_memory(session, user.id, "I use FastAPI backend")[0]
            session.refresh(old)
            session.refresh(target)

            self.assertEqual(action.action, "update")
            self.assertEqual(action.memory_id, target.id)
            self.assertEqual(target.status, "active")
            self.assertEqual(target.canonical_key, "project:backend_framework")
            self.assertEqual(old.status, "superseded")
            self.assertEqual(old.superseded_by_id, target.id)

    def test_manual_candidate_does_not_semantically_merge_with_another_memory(self) -> None:
        with (
            isolated_session() as session,
            patch("app.memory.embedding.get_embedding_provider", return_value=ConflictEmbeddingProvider()),
        ):
            user = create_user(session, "memory-candidate-merge-conflict@example.com", "Memory Candidate Merge Conflict")
            old = memory_service.create_memory_row(
                session,
                user.id,
                "user uses Django backend",
                "user uses django backend",
                memory_service.hash_content("user uses django backend"),
                "project",
                memory_service.MemorySource(text="manual"),
                memory_service.embed_memory_text("user uses django backend"),
                kind="project",
                canonical_key="project:backend_framework",
            )
            similar = memory_service.create_memory_row(
                session,
                user.id,
                "user uses FastAPI services",
                "user uses fastapi services",
                memory_service.hash_content("user uses fastapi services"),
                "project",
                memory_service.MemorySource(text="manual"),
                memory_service.embed_memory_text("user uses fastapi services"),
                kind="project",
                canonical_key="project:fastapi_services",
            )

            action = memory_service.upsert_memory_candidate(
                session,
                user.id,
                memory_service.MemoryCandidate(
                    content="user uses FastAPI backend",
                    kind="project",
                    category="project",
                    canonical_key="project:backend_framework",
                    sensitivity="low",
                ),
                source=memory_service.MemorySource(text="manual"),
            )
            session.refresh(old)
            session.refresh(similar)

            replacement = session.get(UserMemory, action.memory_id)
            self.assertEqual(action.action, "supersede")
            self.assertNotEqual(action.memory_id, similar.id)
            self.assertEqual(replacement.content, "user uses FastAPI backend")
            self.assertEqual(similar.status, "active")
            self.assertEqual(similar.canonical_key, "project:fastapi_services")
            self.assertEqual(old.status, "superseded")
            self.assertEqual(old.superseded_by_id, replacement.id)

    def test_memory_editor_can_create_active_memory(self) -> None:
        from app.llm.provider import MemoryOperation

        provider = ReviewFakeLlmProvider(
            [
                MemoryOperation(
                    action="create",
                    content="user prefers concise technical answers",
                    category="response_detail",
                    importance="high",
                    sensitivity="low",
                    evidence="I prefer concise technical answers",
                    reason="stable response preference",
                )
            ]
        )
        with (
            isolated_session() as session,
            patch.object(memory_service, "get_llm_provider", return_value=provider),
            patch("app.memory.embedding.get_embedding_provider", return_value=FakeEmbeddingProvider()),
        ):
            user = create_user(session, "memory-editor-create@example.com", "Memory Editor Create")

            action = memory_service.process_user_memory(
                session,
                user.id,
                "I prefer concise technical answers",
                assistant_text="Got it.",
            )[0]
            memory = session.get(UserMemory, action.memory_id)

            self.assertEqual(action.action, "create")
            self.assertEqual(memory.status, "active")
            self.assertEqual(memory.source_text, "I prefer concise technical answers")
            self.assertEqual(memory.embedding_model, "fake")
            self.assertEqual(memory.embedding_dimension, 2)
            self.assertIsNotNone(memory.valid_at)
            self.assertEqual(memory.extra_metadata["decision"], "auto_create")
            self.assertEqual(provider.seen_assistant_message, "Got it.")
            self.assertEqual(provider.judge_call_count, 1)

    def test_memory_judge_uses_qdrant_hit_outside_recent_editor_window(self) -> None:
        from app.llm.provider import MemoryOperation

        provider = ConflictReviewFakeLlmProvider(
            [
                MemoryOperation(
                    action="create",
                    content="user uses FastAPI backend",
                    kind="project",
                    category="general",
                    sensitivity="low",
                    evidence="I use FastAPI backend",
                )
            ]
        )
        with (
            isolated_session() as session,
            patch.object(memory_service, "get_llm_provider", return_value=provider),
            patch("app.memory.embedding.get_embedding_provider", return_value=ConflictEmbeddingProvider()),
            patch("app.memory.vector_index.try_sync_memory_vector", return_value=True),
        ):
            user = create_user(session, "memory-judge-qdrant@example.com", "Memory Judge Qdrant")
            old = memory_service.create_manual_memory(
                session,
                user.id,
                "user uses Django backend",
                category="project",
                kind="project",
            )
            for index in range(memory_service.MEMORY_EDITOR_CANDIDATE_LIMIT + 1):
                content = f"unrelated recent general memory {index}"
                normalized = memory_service.normalize_memory_content(content)
                memory_service.create_memory_row(
                    session,
                    user.id,
                    content,
                    normalized,
                    memory_service.hash_content(normalized),
                    "general",
                    memory_service.MemorySource(text="manual"),
                    memory_service.embed_memory_text(content),
                )

            with patch(
                "app.memory.vector_index.search_active_memories",
                return_value=[MemoryVectorHit(memory_id=old.id, score=0.91, payload={})],
            ) as search:
                action = memory_service.process_user_memory(session, user.id, "I use FastAPI backend")[0]

            self.assertEqual(action.action, "supersede")
            self.assertTrue(search.called)
            self.assertIn(old.id, {memory["id"] for memory in provider.seen_conflict_memories})

    def test_memory_editor_rolls_back_all_operations_when_later_operation_fails(self) -> None:
        from app.llm.provider import MemoryOperation

        provider = ReviewFakeLlmProvider(
            [
                MemoryOperation(
                    action="create",
                    content="user prefers concise technical answers",
                    category="response_detail",
                    importance="high",
                    sensitivity="low",
                    evidence="I prefer concise technical answers",
                ),
                MemoryOperation(
                    action="create",
                    content="explode during embedding",
                    category="project",
                    importance="high",
                    sensitivity="low",
                    evidence="explode during embedding",
                ),
            ]
        )
        with (
            isolated_session() as session,
            patch.object(memory_service, "get_llm_provider", return_value=provider),
            patch("app.memory.embedding.get_embedding_provider", return_value=FailingEmbeddingProvider()),
        ):
            user = create_user(session, "memory-editor-rollback@example.com", "Memory Editor Rollback")

            with self.assertRaises(RuntimeError):
                memory_service.process_user_memory(
                    session,
                    user.id,
                    "I prefer concise technical answers; explode during embedding",
                )

            rows = session.scalars(select(UserMemory).where(UserMemory.user_id == user.id)).all()
            self.assertEqual(rows, [])

    def test_conflict_review_logging_does_not_commit_memory_batch_early(self) -> None:
        from app.llm.provider import MemoryOperation

        provider = ConflictReviewFakeLlmProvider(
            [
                MemoryOperation(
                    action="create",
                    content="user keeps sprint notes",
                    category="general",
                    importance="medium",
                    sensitivity="low",
                    evidence="I keep sprint notes",
                ),
                MemoryOperation(
                    action="create",
                    content="user uses FastAPI backend",
                    kind="project",
                    category="project",
                    canonical_key="project:backend_framework",
                    sensitivity="low",
                    evidence="I use FastAPI backend",
                ),
                MemoryOperation(
                    action="create",
                    content="explode during embedding",
                    category="project",
                    importance="high",
                    sensitivity="low",
                    evidence="explode during embedding",
                ),
            ]
        )
        with (
            isolated_session() as session,
            patch.object(memory_service, "get_llm_provider", return_value=provider),
            patch("app.memory.embedding.get_embedding_provider", return_value=FailingEmbeddingProvider()),
        ):
            user = create_user(session, "memory-conflict-rollback@example.com", "Memory Conflict Rollback")
            old = memory_service.create_manual_memory(
                session,
                user.id,
                "user uses Django backend",
                category="project",
                kind="project",
                canonical_key="project:backend_framework",
            )

            with self.assertRaises(RuntimeError):
                memory_service.process_user_memory(
                    session,
                    user.id,
                    "I keep sprint notes; I use FastAPI backend; explode during embedding",
                )

            session.refresh(old)
            rows = session.scalars(select(UserMemory).where(UserMemory.user_id == user.id)).all()
            self.assertEqual([memory.id for memory in rows], [old.id])
            self.assertEqual(old.status, "active")
            self.assertIsNone(old.superseded_by_id)

    def test_ignored_conflict_review_log_is_committed_without_memory_changes(self) -> None:
        from app.llm.provider import MemoryOperation

        provider = ConflictReviewFakeLlmProvider(
            [
                MemoryOperation(
                    action="create",
                    content="user uses FastAPI backend",
                    kind="project",
                    category="project",
                    canonical_key="project:backend_framework",
                    sensitivity="low",
                    evidence="I use FastAPI backend",
                )
            ],
            conflict_action="ignore",
        )
        with (
            isolated_session() as session,
            patch.object(memory_service, "get_llm_provider", return_value=provider),
            patch("app.memory.embedding.get_embedding_provider", return_value=FakeEmbeddingProvider()),
        ):
            user = create_user(session, "memory-conflict-ignore-log@example.com", "Memory Conflict Ignore Log")
            old = memory_service.create_manual_memory(
                session,
                user.id,
                "user uses Django backend",
                category="project",
                kind="project",
                canonical_key="project:backend_framework",
            )

            actions = memory_service.process_user_memory(session, user.id, "I use FastAPI backend")
            session.rollback()

            rows = session.scalars(select(UserMemory).where(UserMemory.user_id == user.id)).all()
            conflict_log = session.scalar(
                select(LlmCallLog).where(
                    LlmCallLog.user_id == user.id,
                    LlmCallLog.agent_name == "memory_judge",
                )
            )

            self.assertEqual(actions[0].action, "ignore")
            self.assertEqual([memory.id for memory in rows], [old.id])
            self.assertIsNotNone(conflict_log)

    def test_memory_editor_pending_action_stays_pending(self) -> None:
        from app.llm.provider import MemoryOperation

        provider = ReviewFakeLlmProvider(
            [
                MemoryOperation(
                    action="pending",
                    content="user may prefer spreadsheet output",
                    category="format",
                    importance="medium",
                    sensitivity="low",
                    evidence="Maybe use a spreadsheet next time",
                    reason="useful but not certain",
                )
            ]
        )
        with (
            isolated_session() as session,
            patch.object(memory_service, "get_llm_provider", return_value=provider),
            patch("app.memory.embedding.get_embedding_provider", return_value=FakeEmbeddingProvider()),
        ):
            user = create_user(session, "memory-editor-pending@example.com", "Memory Editor Pending")

            action = memory_service.process_user_memory(session, user.id, "Maybe use a spreadsheet next time")[0]
            memory = session.get(UserMemory, action.memory_id)

            self.assertEqual(action.action, "pending")
            self.assertEqual(memory.status, "pending")
            self.assertEqual(memory.extra_metadata["proposed_action"], "pending")

    def test_sensitive_memory_operation_is_not_persisted_automatically(self) -> None:
        from app.llm.provider import MemoryOperation

        provider = ReviewFakeLlmProvider(
            [
                MemoryOperation(
                    action="pending",
                    content="user passport number is secret",
                    category="profile",
                    importance="high",
                    sensitivity="high",
                    evidence="My passport number is secret",
                    reason="sensitive personal data",
                )
            ]
        )
        with (
            isolated_session() as session,
            patch.object(memory_service, "get_llm_provider", return_value=provider),
            patch("app.memory.embedding.get_embedding_provider", return_value=FakeEmbeddingProvider()),
        ):
            user = create_user(session, "memory-sensitive@example.com", "Memory Sensitive")

            action = memory_service.process_user_memory(session, user.id, "My passport number is secret")[0]
            rows = session.scalars(select(UserMemory).where(UserMemory.user_id == user.id)).all()

            self.assertEqual(action.action, "ignore")
            self.assertEqual(rows, [])

    def test_low_sensitivity_secret_operation_is_blocked_by_policy_guard(self) -> None:
        from app.llm.provider import MemoryOperation

        provider = ReviewFakeLlmProvider(
            [
                MemoryOperation(
                    action="create",
                    content="user api key is sk-test-secret-value-1234567890",
                    category="profile",
                    importance="high",
                    sensitivity="low",
                    evidence="my api key is sk-test-secret-value-1234567890",
                    reason="LLM misclassified a secret as low sensitivity",
                )
            ]
        )
        with (
            isolated_session() as session,
            patch.object(memory_service, "get_llm_provider", return_value=provider),
            patch("app.memory.embedding.get_embedding_provider", return_value=FakeEmbeddingProvider()),
        ):
            user = create_user(session, "memory-secret-guard@example.com", "Memory Secret Guard")

            action = memory_service.process_user_memory(
                session,
                user.id,
                "my api key is sk-test-secret-value-1234567890",
            )[0]
            rows = session.scalars(select(UserMemory).where(UserMemory.user_id == user.id)).all()

            self.assertEqual(action.action, "ignore")
            self.assertEqual(action.reason, "sensitive memory requires explicit manual save")
            self.assertEqual(rows, [])

    def test_memory_editor_supersedes_existing_memory(self) -> None:
        from app.llm.provider import MemoryOperation

        with (
            isolated_session() as session,
            patch("app.memory.embedding.get_embedding_provider", return_value=FakeEmbeddingProvider()),
        ):
            user = create_user(session, "memory-editor-supersede@example.com", "Memory Editor Supersede")
            old = memory_service.create_manual_memory(session, user.id, "user prefers concise answers", category="response_detail")
            provider = ReviewFakeLlmProvider(
                [
                    MemoryOperation(
                        action="supersede",
                        target_memory_id=old.id,
                        content="user now prefers detailed answers",
                        category="response_detail",
                        importance="high",
                        sensitivity="low",
                        evidence="Actually, explain things in detail from now on",
                        reason="user changed response detail preference",
                    )
                ]
            )

            with patch.object(memory_service, "get_llm_provider", return_value=provider):
                action = memory_service.process_user_memory(
                    session,
                    user.id,
                    "Actually, explain things in detail from now on",
                )[0]

            session.refresh(old)
            new_memory = session.get(UserMemory, action.memory_id)

            self.assertEqual(action.action, "supersede")
            self.assertEqual(old.status, "superseded")
            self.assertEqual(old.superseded_by_id, new_memory.id)
            self.assertIsNotNone(old.invalid_at)
            self.assertEqual(new_memory.status, "active")

    def test_memory_editor_create_conflict_without_reviewer_becomes_pending(self) -> None:
        from app.llm.provider import MemoryOperation

        with (
            isolated_session() as session,
            patch("app.memory.embedding.get_embedding_provider", return_value=FakeEmbeddingProvider()),
        ):
            user = create_user(session, "memory-editor-create-conflict@example.com", "Memory Editor Create Conflict")
            old = memory_service.create_manual_memory(session, user.id, "user prefers concise answers", category="response_detail")
            provider = ReviewFakeLlmProvider(
                [
                    MemoryOperation(
                        action="create",
                        content="user now prefers detailed answers",
                        category="response_detail",
                        importance="high",
                        sensitivity="low",
                        evidence="Actually, explain things in detail from now on",
                        reason="user changed response detail preference",
                    )
                ]
            )

            with patch.object(memory_service, "get_llm_provider", return_value=provider):
                action = memory_service.process_user_memory(
                    session,
                    user.id,
                    "Actually, explain things in detail from now on",
                )[0]

            session.refresh(old)
            pending = session.get(UserMemory, action.memory_id)

            self.assertEqual(action.action, "pending")
            self.assertEqual(old.status, "active")
            self.assertIsNotNone(pending)
            self.assertEqual(pending.status, "pending")
            self.assertEqual(pending.canonical_key, "profile:response_detail")

    def test_memory_candidate_extractor_does_not_receive_existing_memories(self) -> None:
        from app.llm.provider import MemoryOperation

        provider = ReviewFakeLlmProvider([MemoryOperation(action="ignore", reason="nothing durable")])
        with (
            isolated_session() as session,
            patch.object(memory_service, "get_llm_provider", return_value=provider),
            patch("app.memory.embedding.get_embedding_provider", return_value=FakeEmbeddingProvider()),
        ):
            user = create_user(session, "memory-editor-context@example.com", "Memory Editor Context")
            first = memory_service.create_manual_memory(session, user.id, "user prefers Chinese answers", category="language")
            second = memory_service.create_manual_memory(session, user.id, "user works on a RAG project", category="project")
            ignored = memory_service.create_manual_memory(session, user.id, "old ignored memory", category="general")
            memory_service.update_user_memory(session, user.id, second.id, status="pending")
            memory_service.update_user_memory(session, user.id, ignored.id, status="ignored")

            memory_service.process_user_memory(session, user.id, "Thanks")

            self.assertEqual(provider.seen_existing_memories, [])

    def test_memory_candidate_extractor_receives_empty_grouped_context(self) -> None:
        from app.llm.provider import MemoryOperation

        provider = GroupedReviewFakeLlmProvider([MemoryOperation(action="ignore", reason="nothing durable")])
        with (
            isolated_session() as session,
            patch.object(memory_service, "get_llm_provider", return_value=provider),
            patch("app.memory.embedding.get_embedding_provider", return_value=FakeEmbeddingProvider()),
        ):
            user = create_user(session, "memory-editor-grouped-context@example.com", "Memory Editor Grouped Context")
            profile = memory_service.create_manual_memory(session, user.id, "user prefers Chinese answers", category="language")
            candidate = memory_service.create_manual_memory(session, user.id, "user works on a RAG project", category="project")
            pending = memory_service.create_manual_memory(session, user.id, "user may prefer spreadsheet output", category="format")
            memory_service.update_user_memory(session, user.id, pending.id, status="pending")

            memory_service.process_user_memory(session, user.id, "Thanks")

            self.assertEqual(provider.seen_profile_memories, [])
            self.assertEqual(provider.seen_candidate_memories, [])
            self.assertEqual(provider.seen_pending_memories, [])
            self.assertEqual(provider.seen_existing_memories, [])

    def test_memory_editor_context_keeps_old_profile_memories_outside_recent_window(self) -> None:
        with (
            isolated_session() as session,
            patch("app.memory.embedding.get_embedding_provider", return_value=FakeEmbeddingProvider()),
        ):
            user = create_user(session, "memory-editor-old-profile@example.com", "Memory Editor Old Profile")
            profile = memory_service.create_manual_memory(
                session,
                user.id,
                "user prefers Chinese answers",
                category="language",
            )
            for index in range(memory_service.MEMORY_EDITOR_CANDIDATE_LIMIT + 5):
                content = f"user works on project detail {index}"
                memory_service.create_memory_row(
                    session,
                    user.id,
                    content,
                    memory_service.normalize_memory_content(content),
                    memory_service.hash_content(memory_service.normalize_memory_content(content)),
                    "project",
                    memory_service.MemorySource(text="manual"),
                    memory_service.embed_memory_text(content),
                    kind="project",
                )

            context = memory_service.build_memory_editor_context(session, user.id, "new project detail")

            profile_ids = {memory["id"] for memory in context["profile_memories"]}
            existing_ids = {memory["id"] for memory in context["existing_memories"]}
            self.assertIn(profile.id, profile_ids)
            self.assertIn(profile.id, existing_ids)

    def test_conversation_summary_waits_for_sixteen_messages_at_the_normal_token_floor(self) -> None:
        summary_settings = SimpleNamespace(
            conversation_summary_trigger_tokens=10000,
            conversation_summary_min_tokens=1,
            conversation_summary_min_messages=16,
            conversation_summary_max_unprocessed=30,
        )
        with (
            isolated_session() as session,
            patch.object(memory_service, "get_settings", return_value=summary_settings),
        ):
            user = create_user(session, "summary-threshold@example.com", "Summary Threshold")
            conversation = Conversation(user_id=user.id, title="Summary threshold", search_scope="public")
            session.add(conversation)
            session.commit()
            session.refresh(conversation)

            started_at = datetime(2026, 7, 10, tzinfo=timezone.utc)
            for index in range(15):
                session.add(
                    Message(
                        conversation_id=conversation.id,
                        role="user" if index % 2 == 0 else "assistant",
                        content=f"short {index}",
                        created_at=started_at + timedelta(seconds=index),
                    )
                )
            session.commit()
            self.assertFalse(memory_service.should_update_conversation_summary(session, conversation.id))

            session.add(
                Message(
                    conversation_id=conversation.id,
                    role="assistant",
                    content="short 15",
                    created_at=started_at + timedelta(seconds=15),
                )
            )
            session.commit()
            self.assertTrue(memory_service.should_update_conversation_summary(session, conversation.id))

    def test_conversation_summary_uses_incremental_message_cursor(self) -> None:
        summary_settings = SimpleNamespace(
            conversation_summary_trigger_tokens=12,
            conversation_summary_min_tokens=1,
            conversation_summary_min_messages=16,
            conversation_summary_max_unprocessed=30,
            conversation_summary_max_tokens=1200,
            context_compression_target_ratio=0.9,
            context_compression_retry_limit=1,
        )
        with (
            isolated_session() as session,
            patch.object(memory_service, "get_llm_provider", return_value=DeltaSummaryProvider()),
            patch.object(memory_service, "get_settings", return_value=summary_settings),
        ):
            user = create_user(session, "summary-cursor@example.com", "Summary Cursor")
            conversation = Conversation(user_id=user.id, title="Summary cursor", search_scope="public")
            session.add(conversation)
            session.commit()
            session.refresh(conversation)

            started_at = datetime(2026, 7, 10, tzinfo=timezone.utc)
            for index in range(10):
                session.add(
                    Message(
                        conversation_id=conversation.id,
                        role="user" if index % 2 == 0 else "assistant",
                        content=f"message {index}",
                        created_at=started_at + timedelta(seconds=index),
                    )
                )
            session.commit()

            self.assertTrue(memory_service.should_update_conversation_summary(session, conversation.id))
            summary = memory_service.update_conversation_summary(
                session,
                conversation,
                user_message="fallback user should not be used",
                assistant_message="fallback assistant should not be used",
                user_id=user.id,
            )

            self.assertIn("message 0", summary)
            self.assertIn("message 9", summary)
            self.assertNotIn("fallback user should not be used", summary)
            self.assertEqual(conversation.summary_message_count, 10)
            self.assertFalse(memory_service.should_update_conversation_summary(session, conversation.id))

            for index in range(10, 13):
                session.add(
                    Message(
                        conversation_id=conversation.id,
                        role="user",
                        content=f"message {index}",
                        created_at=started_at + timedelta(seconds=index),
                    )
                )
            session.commit()
            self.assertFalse(memory_service.should_update_conversation_summary(session, conversation.id))

            session.add(
                Message(
                    conversation_id=conversation.id,
                    role="assistant",
                    content="message 13",
                    created_at=started_at + timedelta(seconds=13),
                )
            )
            session.commit()

            self.assertTrue(memory_service.should_update_conversation_summary(session, conversation.id))
            updated = memory_service.update_conversation_summary(
                session,
                conversation,
                user_message="unused user",
                assistant_message="unused assistant",
                user_id=user.id,
            )

            self.assertNotIn("message 9", updated)
            self.assertIn("message 10", updated)
            self.assertIn("message 13", updated)
            self.assertEqual(conversation.summary_message_count, 14)

    def test_conversation_summary_overflow_uses_dedicated_semantic_compaction(self) -> None:
        provider = RetryingConversationSummaryProvider()
        summary_settings = SimpleNamespace(
            conversation_summary_max_tokens=80,
            context_compression_retry_limit=1,
        )
        with (
            isolated_session() as session,
            patch.object(memory_service, "get_llm_provider", return_value=provider),
            patch.object(memory_service, "get_settings", return_value=summary_settings),
        ):
            user = create_user(session, "summary-retry@example.com", "Summary Retry")
            conversation = Conversation(user_id=user.id, title="Summary retry", search_scope="public")
            session.add(conversation)
            session.commit()
            session.refresh(conversation)
            session.add_all(
                [
                    Message(conversation_id=conversation.id, role="user", content="Continue the work."),
                    Message(conversation_id=conversation.id, role="assistant", content="Understood."),
                ]
            )
            session.commit()

            summary = memory_service.update_conversation_summary(
                session,
                conversation,
                user_message="unused user",
                assistant_message="unused assistant",
                user_id=user.id,
            )

        self.assertEqual(provider.update_calls, 1)
        self.assertEqual(provider.compaction_calls, 1)
        self.assertIn("Continue the production summary work", summary)
        self.assertIn("Run focused verification", summary)
        self.assertNotIn("Verbose background detail", summary)


if __name__ == "__main__":
    unittest.main()
