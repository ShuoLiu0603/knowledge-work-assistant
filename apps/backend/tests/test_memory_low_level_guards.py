from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import select

from app.db.models.user_memory import UserMemory
from app.llm.provider import MemoryOperation
from app.llm.structured_outputs import MemoryOperationOutput
from app.memory import context, editor, policy, retrieval, short_term
from app.memory.types import MemoryEmbedding, MemorySource
from tests.helpers import create_user, isolated_session


class MemoryPolicyGuardTests(unittest.TestCase):
    def test_structured_sensitivity_defaults_and_unknown_values_fail_closed(self) -> None:
        self.assertEqual(MemoryOperationOutput.model_validate({}).sensitivity, "high")
        self.assertEqual(
            MemoryOperationOutput.model_validate({"sensitivity": "unexpected"}).sensitivity,
            "high",
        )

    def test_sensitive_detector_covers_common_personal_data(self) -> None:
        sensitive_values = (
            "邮箱alice@example.com请联系我",
            "我的手机号是13812345678",
            "我的家庭住址是北京市朝阳区朝阳路10号",
            "我的病历显示诊断为高血压",
            "我的工资是每月30000元",
            "My account balance is 12000 USD",
        )

        for value in sensitive_values:
            with self.subTest(value=value):
                self.assertTrue(policy.has_sensitive_memory_content(value))

        self.assertFalse(policy.has_sensitive_memory_content("I work on payroll data pipelines"))
        self.assertFalse(policy.has_sensitive_memory_content("The health check endpoint is /health"))

    def test_do_not_remember_marker_disables_memory_for_the_whole_turn(self) -> None:
        self.assertTrue(policy.should_skip_memory_for_turn("Do not remember this temporary note"))
        self.assertTrue(policy.should_skip_memory_for_turn("不要记住这条临时信息"))

    def test_evidence_must_be_present_in_the_trusted_user_message(self) -> None:
        operation = MemoryOperation(
            action="create",
            content="user is the CFO",
            sensitivity="low",
            evidence="I am the CFO",
        )

        self.assertTrue(policy.is_safe_memory_operation(operation, user_message="Today I am the CFO"))
        self.assertFalse(policy.is_safe_memory_operation(operation, user_message="Hello"))
        self.assertFalse(policy.is_safe_memory_operation(operation))

    def test_grounded_evidence_cannot_auto_save_unrelated_content(self) -> None:
        operation = MemoryOperation(
            action="create",
            content="user is the CFO",
            kind="profile",
            category="current_role",
            sensitivity="low",
            evidence="I prefer blue dashboards",
        )
        fake_embedding = MemoryEmbedding(vector=[1.0, 0.0], model="fake", dimension=2)

        self.assertTrue(policy.is_evidence_grounded(operation.evidence, operation.evidence))
        self.assertFalse(policy.is_safe_memory_operation(operation, user_message=operation.evidence))

        with (
            isolated_session() as session,
            patch("app.memory.embedding.embed_memory_text", return_value=fake_embedding),
        ):
            user = create_user(session, "content-grounding@example.com")
            action = editor.process_memory_operation(
                session,
                user.id,
                operation,
                MemorySource(text=operation.evidence),
                user_message=operation.evidence,
            )
            memory = session.get(UserMemory, action.memory_id)

            self.assertEqual(action.action, "pending")
            self.assertIsNotNone(memory)
            self.assertEqual(memory.status, "pending")

    def test_editor_does_not_trust_memory_source_as_evidence(self) -> None:
        operation = MemoryOperation(
            action="create",
            content="user is the CFO",
            kind="profile",
            category="current_role",
            sensitivity="low",
            evidence="I am the CFO",
        )
        fake_embedding = MemoryEmbedding(vector=[1.0, 0.0], model="fake", dimension=2)

        with (
            isolated_session() as session,
            patch("app.memory.embedding.embed_memory_text", return_value=fake_embedding),
        ):
            user = create_user(session, "evidence-guard@example.com")
            action = editor.process_memory_operation(
                session,
                user.id,
                operation,
                MemorySource(text="I am the CFO"),
                user_message="Hello",
            )
            self.assertEqual(action.action, "ignore")
            self.assertEqual(action.reason, "memory evidence is not grounded in the user turn")
            self.assertIsNone(action.memory_id)
            self.assertEqual(session.scalars(select(UserMemory)).all(), [])

    def test_editor_skips_stale_revision_after_manual_memory_change(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "stale-memory-operation@example.com")
            memory = UserMemory(
                user_id=user.id,
                content="I prefer concise answers",
                normalized_content="i prefer concise answers",
                content_hash=policy.hash_content("i prefer concise answers"),
                status="active",
                kind="preference",
                category="response_detail",
                revision=2,
            )
            session.add(memory)
            session.commit()
            operation = MemoryOperation(
                action="update",
                target_memory_id=memory.id,
                content="I prefer detailed answers",
                kind="preference",
                category="response_detail",
                sensitivity="low",
                evidence="I prefer detailed answers",
                expected_revision=1,
            )

            action = editor.process_memory_operation(
                session,
                user.id,
                operation,
                MemorySource(text=operation.evidence),
                user_message=operation.evidence,
            )

            session.refresh(memory)
            self.assertEqual(action.action, "ignore")
            self.assertIn("stale operation", action.reason)
            self.assertEqual(memory.content, "I prefer concise answers")
            self.assertEqual(memory.revision, 2)

    def test_editor_auto_creates_when_evidence_is_grounded(self) -> None:
        operation = MemoryOperation(
            action="create",
            content="user prefers concise answers",
            category="response_detail",
            sensitivity="low",
            evidence="I prefer concise answers",
        )
        fake_embedding = MemoryEmbedding(vector=[1.0, 0.0], model="fake", dimension=2)

        with (
            isolated_session() as session,
            patch("app.memory.embedding.embed_memory_text", return_value=fake_embedding),
        ):
            user = create_user(session, "grounded-evidence@example.com")
            action = editor.process_memory_operation(
                session,
                user.id,
                operation,
                MemorySource(text="I prefer concise answers"),
                user_message="I prefer concise answers",
            )
            memory = session.get(UserMemory, action.memory_id)

            self.assertEqual(action.action, "create")
            self.assertEqual(memory.status, "active")

    def test_conflict_reviewer_cannot_persist_ungrounded_pending_memory(self) -> None:
        fake_embedding = MemoryEmbedding(vector=[1.0, 0.0], model="fake", dimension=2)
        original = MemoryOperation(
            action="create",
            content="user uses FastAPI backend",
            kind="project",
            category="project",
            canonical_key="project:backend_framework",
            sensitivity="low",
            evidence="I use FastAPI backend",
        )
        ungrounded_decision = MemoryOperation(
            action="pending",
            content="user is the CFO",
            category="current_role",
            sensitivity="low",
            evidence="I am the CFO",
        )

        with (
            isolated_session() as session,
            patch("app.memory.embedding.embed_memory_text", return_value=fake_embedding),
        ):
            user = create_user(session, "conflict-evidence@example.com")
            editor.process_memory_operation(
                session,
                user.id,
                MemoryOperation(
                    action="create",
                    content="user uses Django backend",
                    kind="project",
                    category="project",
                    canonical_key="project:backend_framework",
                    sensitivity="low",
                    evidence="I use Django backend",
                ),
                MemorySource(text="I use Django backend"),
                user_message="I use Django backend",
            )

            action = editor.process_memory_operation(
                session,
                user.id,
                original,
                MemorySource(text="I use FastAPI backend"),
                conflict_reviewer=lambda _operation, _memories: ungrounded_decision,
                user_message="I use FastAPI backend",
            )

            self.assertEqual(action.action, "ignore")
            self.assertEqual(action.reason, "memory evidence is not grounded in the user turn")
            self.assertEqual(len(session.scalars(select(UserMemory)).all()), 1)

    def test_invalid_conflict_target_fallback_rechecks_original_evidence(self) -> None:
        original = MemoryOperation(
            action="create",
            content="user uses FastAPI backend",
            sensitivity="low",
            evidence="fabricated original evidence",
        )
        decision = MemoryOperation(
            action="update",
            target_memory_id="not-a-candidate",
            content="user uses FastAPI backend",
            sensitivity="low",
            evidence="I use FastAPI backend",
        )

        with isolated_session() as session:
            user = create_user(session, "invalid-target-evidence@example.com")
            action = editor.review_and_apply_conflict_decision(
                session,
                user.id,
                original,
                MemorySource(text="I use FastAPI backend"),
                [],
                lambda _operation, _memories: decision,
                user_message="I use FastAPI backend",
            )

            self.assertEqual(action.action, "ignore")
            self.assertEqual(action.reason, "memory evidence is not grounded in the user turn")
            self.assertEqual(session.scalars(select(UserMemory)).all(), [])


class MemoryContextGuardTests(unittest.TestCase):
    def test_recent_context_is_not_capped_to_eight_messages(self) -> None:
        result = context.format_memory_context(
            long_memories=[],
            short_memory=[{"role": "user", "content": f"message {index}"} for index in range(10)],
            conversation_summary=None,
            max_chars=3000,
        )

        self.assertIn("message 0", result)
        self.assertIn("message 9", result)

    def test_context_honors_small_character_and_fallback_token_budgets(self) -> None:
        char_limited = context.format_memory_context(
            long_memories=[{"content": "A" * 200}],
            short_memory=[{"role": "user", "content": "B" * 200}],
            conversation_summary="C" * 200,
            max_chars=90,
        )
        with patch("app.memory.context.load_tokenizer", return_value=None):
            token_limited = context.format_memory_context(
                long_memories=[{"content": "记忆" * 100}],
                short_memory=[{"role": "user", "content": "对话" * 100}],
                conversation_summary="摘要" * 100,
                max_chars=5000,
                max_tokens=30,
            )
            budget = context.TextBudget.from_limits(5000, 30)

        self.assertLessEqual(len(char_limited), 90)
        self.assertLessEqual(budget.count(token_limited), 30)

    def test_token_budget_is_not_overridden_by_character_limit(self) -> None:
        result = context.format_memory_context(
            long_memories=[{"content": "User is building an enterprise RAG system."}],
            short_memory=[{"role": "user", "content": "Keep the full recent message available."}],
            conversation_summary="The project uses FastAPI, Qdrant, and LangGraph.",
            max_chars=40,
            max_tokens=120,
        )
        budget = context.TextBudget.from_limits(max_chars=40, max_tokens=120)

        self.assertGreater(len(result), 40)
        self.assertLessEqual(budget.count(result), 120)

    def test_recent_context_keeps_newest_messages_and_preserves_their_order(self) -> None:
        result = context.format_memory_context(
            long_memories=[],
            short_memory=[
                {"role": "user", "content": f"message {index} " + ("x" * 30)}
                for index in range(10)
            ],
            conversation_summary=None,
            max_chars=300,
        )

        self.assertIn("message 9", result)
        if "message 8" in result:
            self.assertLess(result.index("message 8"), result.index("message 9"))


class MemoryRetrievalFallbackTests(unittest.TestCase):
    def test_embedding_failure_keeps_ranked_candidates_without_a_relevance_threshold(self) -> None:
        active = [
            make_memory("sticky", "user prefers Chinese answers", category="language"),
            make_memory("related", "user is building an agentic RAG architecture", touched=2),
            make_memory("unrelated", "user booked a beach holiday", touched=3),
        ]

        result = retrieval.retrieve_relevant_memories_with_metadata(
            active,
            "agentic RAG architecture",
            limit=5,
            embed=raising_embed,
        )

        self.assertEqual([memory.id for memory in result.selected], ["sticky", "related", "unrelated"])
        self.assertEqual(result.recall_mode, "fallback_no_embedding")
        routes = {candidate.memory.id: candidate.route for candidate in result.candidates}
        self.assertEqual(routes["related"], "lexical_ranked")
        self.assertEqual(routes["unrelated"], "lexical_ranked")

    def test_editor_ranking_keeps_bounded_candidates_on_embedding_failure(self) -> None:
        memories = [
            make_memory("related", "agentic RAG architecture", touched=1),
            make_memory("unrelated", "beach holiday", touched=2),
        ]

        ranked = retrieval.rank_editor_context(memories, "RAG architecture", raising_embed)

        self.assertEqual([memory.id for memory in ranked], ["related", "unrelated"])

    def test_empty_embedding_uses_the_same_lexical_fallback(self) -> None:
        active = [
            make_memory("related", "agentic RAG architecture"),
            make_memory("unrelated", "beach holiday"),
        ]

        result = retrieval.retrieve_relevant_memories_with_metadata(
            active,
            "RAG architecture",
            limit=5,
            embed=lambda _query: [],
        )

        self.assertEqual([memory.id for memory in result.selected], ["related", "unrelated"])
        self.assertIn("empty vector", result.embedding_error)


class ShortTermMemoryGuardTests(unittest.TestCase):
    def test_append_uses_one_transactional_pipeline(self) -> None:
        client = FakeRedis()
        settings = SimpleNamespace(short_memory_max_messages=3)

        with (
            patch("app.memory.short_term.get_redis_client", return_value=client),
            patch("app.memory.short_term.get_settings", return_value=settings),
        ):
            short_term.append_short_term_memory("user-1", "conversation-1", "user", " hello ")

        self.assertTrue(client.pipeline_transaction)
        self.assertTrue(client.pipeline_executed)
        self.assertEqual([operation[0] for operation in client.operations], ["lpush", "ltrim", "expire"])
        self.assertEqual(client.operations[1][1:], ("memory:short:user-1:conversation-1", 0, 2))
        payload = json.loads(client.operations[0][2])
        self.assertEqual(payload["content"], "hello")

    def test_read_filters_malformed_rows_and_clear_deletes_the_key(self) -> None:
        created_at = datetime.now(timezone.utc).isoformat()
        valid = json.dumps({"role": "assistant", "content": "answer", "created_at": created_at})
        client = FakeRedis(rows=["not-json", json.dumps([1, 2]), json.dumps({"role": "tool"}), valid])

        with patch("app.memory.short_term.get_redis_client", return_value=client):
            messages = short_term.get_short_term_memory("user-1", "conversation-1")
            cleared = short_term.clear_short_term_memory("user-1", "conversation-1")

        self.assertEqual(messages, [{"role": "assistant", "content": "answer", "created_at": created_at}])
        self.assertTrue(cleared)
        self.assertEqual(client.deleted_keys, ["memory:short:user-1:conversation-1"])


def make_memory(
    memory_id: str,
    content: str,
    *,
    category: str = "project",
    touched: float = 0,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=memory_id,
        content=content,
        category=category,
        kind="project",
        memory_layer="semantic",
        pinned=False,
        extra_metadata={},
        embedding=[1.0, 0.0],
        last_touched_at=touched,
    )


def raising_embed(_text: str) -> list[float]:
    raise RuntimeError("embedding unavailable")


class FakeRedis:
    def __init__(self, rows: list[str] | None = None) -> None:
        self.rows = rows or []
        self.operations: list[tuple] = []
        self.pipeline_transaction: bool | None = None
        self.pipeline_executed = False
        self.deleted_keys: list[str] = []

    def pipeline(self, transaction: bool):
        self.pipeline_transaction = transaction
        return self

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        return False

    def lpush(self, *args):
        self.operations.append(("lpush", *args))
        return self

    def ltrim(self, *args):
        self.operations.append(("ltrim", *args))
        return self

    def expire(self, *args):
        self.operations.append(("expire", *args))
        return self

    def execute(self):
        self.pipeline_executed = True
        return [1, 1, 1]

    def lrange(self, _key: str, _start: int, _end: int) -> list[str]:
        return self.rows

    def delete(self, key: str) -> int:
        self.deleted_keys.append(key)
        return 1


if __name__ == "__main__":
    unittest.main()
