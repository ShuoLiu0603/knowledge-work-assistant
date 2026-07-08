from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import select

from app.db.models.conversation import Conversation, Message
from app.db.models.user_memory import UserMemory, UserMemoryEvent, UserMemoryRecallLog, UserMemoryUpdateJob
from app.memory import context as memory_context
from app.memory.vector_index import MemoryVectorHit
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


class DeltaSummaryProvider:
    def summarize(self, text: str) -> str:
        return text.split("New messages since previous summary:\n", 1)[-1]


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
            patch("app.memory.embedding.get_embedding_provider", return_value=FakeEmbeddingProvider()),
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
            self.assertIn("merge", event_types)
            self.assertEqual({event.user_id for event in events}, {user.id})

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

    def test_memory_context_is_budgeted(self) -> None:
        context = memory_service.format_memory_context(
            long_memories=[{"content": f"memory {index} " + ("A" * 120)} for index in range(10)],
            short_memory=[{"role": "user", "content": "B" * 120} for _ in range(5)],
            conversation_summary="C" * 600,
            max_long_memories=10,
            max_chars=500,
        )

        self.assertLessEqual(len(context), 500)
        self.assertIn("长期记忆", context)
        self.assertIn("会话摘要", context)
        self.assertIn("最近对话", context)

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
        self.assertIn("长期记忆", context)
        self.assertIn("会话摘要", context)
        self.assertIn("最近对话", context)

    def test_memory_context_prioritizes_sticky_memories_under_budget(self) -> None:
        context = memory_context.format_memory_context(
            long_memories=[
                {
                    "content": "low priority general memory " * 40,
                    "category": "general",
                    "kind": "preference",
                    "metadata": {"confidence": 0.1},
                },
                {
                    "content": "用户偏好中文回答",
                    "category": "language",
                    "kind": "preference",
                    "metadata": {"confidence": 0.9},
                },
            ],
            short_memory=[],
            conversation_summary=None,
            max_long_memories=2,
            max_chars=350,
        )

        self.assertIn("用户偏好中文回答", context)
        self.assertNotIn("low priority general memory", context)

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
            self.assertIn("- 无", context)
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
            self.assertEqual(recall_log.candidates[0]["route"], "semantic")
            self.assertIsNotNone(recall_log.candidates[0]["score"])

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
            self.assertIsNotNone(search.call_args.kwargs["score_threshold"])
            recall_log = session.scalar(
                select(UserMemoryRecallLog)
                .where(UserMemoryRecallLog.user_id == user.id, UserMemoryRecallLog.query == "agentic RAG architecture")
                .order_by(UserMemoryRecallLog.created_at.desc(), UserMemoryRecallLog.id.desc())
            )
            self.assertIsNotNone(recall_log)
            self.assertEqual(recall_log.recall_mode, "vector")
            self.assertEqual(recall_log.selected_memory_ids, [memory.id])
            self.assertEqual(recall_log.candidates[0]["route"], "vector")
            self.assertEqual(recall_log.candidates[0]["score"], 0.91)
            self.assertIsNotNone(recall_log.threshold)

    def test_vector_memory_recall_filters_low_score_hits(self) -> None:
        with (
            isolated_session() as session,
            patch("app.memory.embedding.get_embedding_provider", return_value=FakeEmbeddingProvider()),
        ):
            user = create_user(session, "memory-vector-threshold@example.com", "Memory Vector Threshold")
            memory = memory_service.create_manual_memory(session, user.id, "user works on payroll data", category="project")

            with patch(
                "app.memory.vector_index.search_active_memories",
                return_value=[MemoryVectorHit(memory_id=memory.id, score=0.01, payload={})],
            ):
                recalled = memory_service.retrieve_relevant_memories(session, user.id, "vacation policy")

            self.assertEqual(recalled, [])
            recall_log = session.scalar(
                select(UserMemoryRecallLog)
                .where(UserMemoryRecallLog.user_id == user.id, UserMemoryRecallLog.query == "vacation policy")
                .order_by(UserMemoryRecallLog.created_at.desc(), UserMemoryRecallLog.id.desc())
            )
            self.assertIsNotNone(recall_log)
            self.assertEqual(recall_log.recall_mode, "vector")
            self.assertEqual(recall_log.selected_memory_ids, [])
            self.assertEqual(recall_log.candidates[0]["route"], "below_threshold")

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
            self.assertEqual(event.payload["content"], "Use concise answers")

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
            self.assertNotIn(other_memory.id, [item.id for item in export["memories"]])
            self.assertTrue(any(event.memory_id == memory.id for event in export["events"]))
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
                                "route": "semantic",
                                "score": 0.82,
                                "selected": True,
                            },
                            {
                                "memory_id": "memory-b",
                                "category": "format",
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

    def test_low_confidence_memory_candidate_is_pending_and_not_loaded(self) -> None:
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
            self.assertNotIn("temporary low confidence detail", context)

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
            self.assertEqual(memory.extra_metadata["confidence"], 0.9)

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
            patch("app.memory.embedding.get_embedding_provider", return_value=FakeEmbeddingProvider()),
        ):
            user = create_user(session, "memory-editor-pending@example.com", "Memory Editor Pending")

            action = memory_service.process_user_memory(session, user.id, "Maybe use a spreadsheet next time")[0]
            memory = session.get(UserMemory, action.memory_id)

            self.assertEqual(action.action, "pending")
            self.assertEqual(memory.status, "pending")
            self.assertEqual(memory.extra_metadata["proposed_action"], "create")

    def test_sensitive_memory_operation_is_not_persisted_automatically(self) -> None:
        from app.llm.provider import MemoryOperation

        provider = ReviewFakeLlmProvider(
            [
                MemoryOperation(
                    action="pending",
                    content="user passport number is secret",
                    category="profile",
                    confidence=0.95,
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
            self.assertIsNotNone(old.invalid_at)
            self.assertEqual(new_memory.status, "active")

    def test_memory_editor_receives_active_and_pending_user_memories(self) -> None:
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

            seen_ids = {memory["id"] for memory in provider.seen_existing_memories}
            self.assertEqual(seen_ids, {first.id, second.id})

    def test_conversation_summary_uses_incremental_message_cursor(self) -> None:
        with isolated_session() as session, patch.object(memory_service, "get_llm_provider", return_value=DeltaSummaryProvider()):
            user = create_user(session, "summary-cursor@example.com", "Summary Cursor")
            conversation = Conversation(user_id=user.id, title="Summary cursor", search_scope="public")
            session.add(conversation)
            session.commit()
            session.refresh(conversation)

            for index in range(10):
                session.add(
                    Message(
                        conversation_id=conversation.id,
                        role="user" if index % 2 == 0 else "assistant",
                        content=f"message {index}",
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
                session.add(Message(conversation_id=conversation.id, role="user", content=f"message {index}"))
            session.commit()
            self.assertFalse(memory_service.should_update_conversation_summary(session, conversation.id))

            session.add(Message(conversation_id=conversation.id, role="assistant", content="message 13"))
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


if __name__ == "__main__":
    unittest.main()
