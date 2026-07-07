from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import select

from app.db.models.user_memory import UserMemory
from app.services import memory_service
from helpers import create_user, isolated_session


class FakeLlmProvider:
    def extract_memory_candidates(self, text: str) -> list[str]:
        lowered = text.lower()
        if "concise" in lowered:
            return ["concise answers"]
        if "more detailed" in lowered:
            return ["more detailed answers"]
        if "detailed" in lowered:
            return ["detailed answers"]
        return []

    def summarize(self, text: str) -> str:
        return f"summary: {text[:80]}"


class StructuredFakeLlmProvider:
    def extract_memory_candidates_with_metadata(self, text: str):
        from app.llm.provider import LlmCompletion, MemoryCandidate, MemoryExtraction

        if "temporary" in text.lower():
            candidates = [
                MemoryCandidate(
                    content="temporary low confidence detail",
                    category="general",
                    confidence=0.6,
                    sensitivity="low",
                )
            ]
        else:
            candidates = [
                MemoryCandidate(
                    content="user prefers concise answers",
                    category="response_detail",
                    confidence=0.9,
                    sensitivity="low",
                )
            ]
        return MemoryExtraction(
            candidates=candidates,
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


class PromotingFakeLlmProvider(StructuredFakeLlmProvider):
    def extract_memory_candidates_with_metadata(self, text: str):
        from app.llm.provider import LlmCompletion, MemoryCandidate, MemoryExtraction

        confidence = 0.9 if "sure" in text.lower() else 0.6
        return MemoryExtraction(
            candidates=[
                MemoryCandidate(
                    content="temporary low confidence detail",
                    category="general",
                    confidence=confidence,
                    sensitivity="low",
                )
            ],
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


class ReviewFakeLlmProvider:
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


class TrackingEmbeddingProvider(FakeEmbeddingProvider):
    def __init__(self) -> None:
        self.calls: list[str] = []

    def embed_text(self, text: str) -> list[float]:
        self.calls.append(text)
        return super().embed_text(text)


class MemoryServiceTests(unittest.TestCase):
    def test_memory_actions_cover_dedupe_merge_supersede_and_ignore(self) -> None:
        with (
            isolated_session() as session,
            patch.object(memory_service, "get_llm_provider", return_value=FakeLlmProvider()),
            patch.object(memory_service, "get_embedding_provider", return_value=FakeEmbeddingProvider()),
        ):
            user = create_user(session, "memory@example.com", "Memory")

            create_action = memory_service.process_user_memory(session, user.id, "I prefer concise answers")[0]
            touch_action = memory_service.process_user_memory(session, user.id, "I prefer concise answers")[0]
            supersede_action = memory_service.process_user_memory(session, user.id, "I prefer detailed answers")[0]
            merge_action = memory_service.process_user_memory(session, user.id, "I prefer more detailed answers")[0]
            ignore_action = memory_service.process_user_memory(session, user.id, "Do not remember this temporary note")[0]

            self.assertEqual(create_action.action, "create")
            self.assertEqual(touch_action.action, "touch")
            self.assertEqual(supersede_action.action, "supersede")
            self.assertEqual(merge_action.action, "merge")
            self.assertEqual(ignore_action.action, "ignore")
            self.assertEqual(ignore_action.reason, "user asked not to remember")

            rows = session.scalars(select(UserMemory).where(UserMemory.user_id == user.id)).all()
            active = [memory for memory in rows if memory.status == "active"]
            superseded = [memory for memory in rows if memory.status == "superseded"]

            self.assertEqual(len(active), 1)
            self.assertEqual(len(superseded), 1)
            self.assertIn("detailed answers", active[0].content)
            self.assertGreaterEqual(active[0].merge_count, 1)
            self.assertEqual(superseded[0].content, "concise answers")
            self.assertEqual(superseded[0].touched_count, 1)
            self.assertEqual(superseded[0].superseded_by_id, active[0].id)

            recalled = memory_service.retrieve_relevant_memories(session, user.id, "answer with detailed context")
            self.assertEqual(recalled[0].id, active[0].id)

    def test_memory_recall_query_uses_standard_relevance_retrieval(self) -> None:
        provider = TrackingEmbeddingProvider()
        with isolated_session() as session, patch.object(memory_service, "get_embedding_provider", return_value=provider):
            user = create_user(session, "memory-recall@example.com", "Memory Recall")
            first = memory_service.create_manual_memory(session, user.id, "用户偏好中文回答", category="language")
            second = memory_service.create_manual_memory(session, user.id, "用户当前项目是 RAG 平台", category="general")

            provider.calls.clear()
            recalled = memory_service.retrieve_relevant_memories(session, user.id, "你记得我什么？")

            self.assertEqual({memory.id for memory in recalled}, {first.id, second.id})
            self.assertIn("你记得我什么？", provider.calls)
            context = memory_service.build_memory_context_for_question(session, user.id, "我的偏好是什么？")
            self.assertIn("用户偏好中文回答", context)

    def test_memory_status_updates_are_limited_to_known_states(self) -> None:
        with (
            isolated_session() as session,
            patch.object(memory_service, "get_embedding_provider", return_value=FakeEmbeddingProvider()),
        ):
            user = create_user(session, "memory-status@example.com", "Memory Status")
            memory = memory_service.create_manual_memory(session, user.id, "Use concise answers")

            updated = memory_service.update_user_memory(session, user.id, memory.id, status="ignored")
            self.assertEqual(updated.status, "ignored")

            with self.assertRaises(HTTPException) as update_error:
                memory_service.update_user_memory(session, user.id, memory.id, status="archived")
            self.assertEqual(update_error.exception.status_code, 400)

            session.refresh(memory)
            self.assertEqual(memory.status, "ignored")

            with self.assertRaises(HTTPException) as list_error:
                memory_service.list_user_memories(session, user.id, status="archived")
            self.assertEqual(list_error.exception.status_code, 400)

    def test_low_confidence_memory_candidate_is_pending_and_not_loaded(self) -> None:
        with (
            isolated_session() as session,
            patch.object(memory_service, "get_llm_provider", return_value=StructuredFakeLlmProvider()),
            patch.object(memory_service, "get_embedding_provider", return_value=FakeEmbeddingProvider()),
        ):
            user = create_user(session, "memory-pending@example.com", "Memory Pending")

            action = memory_service.process_user_memory(session, user.id, "Remember this temporary detail")[0]

            self.assertEqual(action.action, "pending")
            pending = session.get(UserMemory, action.memory_id)
            self.assertIsNotNone(pending)
            self.assertEqual(pending.status, "pending")

            context = memory_service.build_memory_context_for_question(session, user.id, "What do you know?")
            self.assertNotIn("temporary low confidence detail", context)

    def test_structured_memory_candidate_can_create_active_memory(self) -> None:
        with (
            isolated_session() as session,
            patch.object(memory_service, "get_llm_provider", return_value=StructuredFakeLlmProvider()),
            patch.object(memory_service, "get_embedding_provider", return_value=FakeEmbeddingProvider()),
        ):
            user = create_user(session, "memory-structured@example.com", "Memory Structured")

            action = memory_service.process_user_memory(session, user.id, "I prefer concise answers")[0]

            self.assertEqual(action.action, "create")
            memory = session.get(UserMemory, action.memory_id)
            self.assertIsNotNone(memory)
            self.assertEqual(memory.status, "active")
            self.assertEqual(memory.category, "response_detail")
            self.assertEqual(memory.extra_metadata["confidence"], 0.9)

    def test_pending_memory_is_deduplicated_and_can_be_promoted(self) -> None:
        with (
            isolated_session() as session,
            patch.object(memory_service, "get_llm_provider", return_value=PromotingFakeLlmProvider()),
            patch.object(memory_service, "get_embedding_provider", return_value=FakeEmbeddingProvider()),
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

    def test_memory_editor_can_create_active_memory(self) -> None:
        from app.llm.provider import MemoryOperation

        provider = ReviewFakeLlmProvider(
            [
                MemoryOperation(
                    action="create",
                    content="user prefers concise technical answers",
                    category="response_detail",
                    confidence=0.92,
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
            patch.object(memory_service, "get_embedding_provider", return_value=FakeEmbeddingProvider()),
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
            self.assertIn("Assistant:\nGot it.", memory.source_text)
            self.assertEqual(memory.extra_metadata["decision"], "auto_create")
            self.assertEqual(provider.seen_assistant_message, "Got it.")

    def test_memory_editor_uncertain_create_becomes_pending(self) -> None:
        from app.llm.provider import MemoryOperation

        provider = ReviewFakeLlmProvider(
            [
                MemoryOperation(
                    action="create",
                    content="user may prefer spreadsheet output",
                    category="format",
                    confidence=0.7,
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
            patch.object(memory_service, "get_embedding_provider", return_value=FakeEmbeddingProvider()),
        ):
            user = create_user(session, "memory-editor-pending@example.com", "Memory Editor Pending")

            action = memory_service.process_user_memory(session, user.id, "Maybe use a spreadsheet next time")[0]
            memory = session.get(UserMemory, action.memory_id)

            self.assertEqual(action.action, "pending")
            self.assertEqual(memory.status, "pending")
            self.assertEqual(memory.extra_metadata["proposed_action"], "create")

    def test_memory_editor_supersedes_existing_memory(self) -> None:
        from app.llm.provider import MemoryOperation

        with (
            isolated_session() as session,
            patch.object(memory_service, "get_embedding_provider", return_value=FakeEmbeddingProvider()),
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
                        confidence=0.9,
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
            self.assertEqual(new_memory.status, "active")

    def test_memory_editor_receives_all_existing_user_memories(self) -> None:
        from app.llm.provider import MemoryOperation

        provider = ReviewFakeLlmProvider([MemoryOperation(action="ignore", reason="nothing durable")])
        with (
            isolated_session() as session,
            patch.object(memory_service, "get_llm_provider", return_value=provider),
            patch.object(memory_service, "get_embedding_provider", return_value=FakeEmbeddingProvider()),
        ):
            user = create_user(session, "memory-editor-context@example.com", "Memory Editor Context")
            first = memory_service.create_manual_memory(session, user.id, "user prefers Chinese answers", category="language")
            second = memory_service.create_manual_memory(session, user.id, "user works on a RAG project", category="project")
            memory_service.update_user_memory(session, user.id, second.id, status="pending")

            memory_service.process_user_memory(session, user.id, "Thanks")

            seen_ids = {memory["id"] for memory in provider.seen_existing_memories}
            self.assertEqual(seen_ids, {first.id, second.id})


if __name__ == "__main__":
    unittest.main()
