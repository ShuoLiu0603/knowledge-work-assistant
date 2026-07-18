from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import select

from app.db.models.conversation import Conversation, Message
from app.db.models.external_cleanup_job import ExternalCleanupJob
from app.llm.provider import LlmCompletion
from app.services import memory_service
from helpers import create_user, isolated_session


class FakeEmbeddingProvider:
    name = "fake"
    dimension = 2

    def embed_text(self, text: str) -> list[float]:
        return [1.0, 0.0] if "concise" in text.lower() else [0.5, 0.5]


def completion(content: str) -> LlmCompletion:
    return LlmCompletion(
        content=content,
        provider="test",
        model_name="test",
        prompt_tokens=1,
        completion_tokens=1,
        total_tokens=2,
        latency_ms=1,
        status="success",
    )


class AccumulatingSummaryProvider:
    def __init__(self) -> None:
        self.calls = 0

    def summarize_with_metadata(self, text: str) -> LlmCompletion:
        self.calls += 1
        previous = text.split("Existing summary:\n", 1)[1].split("\n\nNew messages", 1)[0]
        delta = text.split("New messages since previous summary:\n", 1)[1]
        prefix = "" if previous == "None" else previous + "\n"
        return completion(prefix + delta)


class MemoryOrchestrationRegressionTests(unittest.TestCase):
    def test_summary_excludes_no_memory_turn_and_its_assistant_reply(self) -> None:
        provider = AccumulatingSummaryProvider()
        with (
            isolated_session() as session,
            patch.object(memory_service, "get_llm_provider", return_value=provider),
        ):
            user = create_user(session, "summary-opt-out@example.com", "Summary Opt Out")
            conversation = Conversation(user_id=user.id, title="Summary opt out", search_scope="public")
            session.add(conversation)
            session.commit()
            session.add_all(
                [
                    Message(
                        conversation_id=conversation.id,
                        role="user",
                        content="PRIVATE_MARKER",
                        memory_enabled=False,
                        created_at=datetime.now(timezone.utc),
                    ),
                    Message(
                        conversation_id=conversation.id,
                        role="assistant",
                        content="PRIVATE_REPLY",
                        memory_enabled=False,
                        created_at=datetime.now(timezone.utc) + timedelta(seconds=1),
                    ),
                    Message(
                        conversation_id=conversation.id,
                        role="user",
                        content="NORMAL_MARKER",
                        created_at=datetime.now(timezone.utc) + timedelta(seconds=2),
                    ),
                    Message(
                        conversation_id=conversation.id,
                        role="assistant",
                        content="NORMAL_REPLY",
                        created_at=datetime.now(timezone.utc) + timedelta(seconds=3),
                    ),
                ]
            )
            session.commit()

            summary = memory_service.update_conversation_summary(
                session,
                conversation,
                user_message="unused",
                assistant_message="unused",
                user_id=user.id,
            )

            self.assertNotIn("PRIVATE_MARKER", summary)
            self.assertNotIn("PRIVATE_REPLY", summary)
            self.assertIn("NORMAL_MARKER", summary)
            self.assertIn("NORMAL_REPLY", summary)
            self.assertEqual(conversation.summary_message_count, 4)

    def test_summary_processes_every_over_budget_batch_before_advancing_cursor(self) -> None:
        provider = AccumulatingSummaryProvider()
        with (
            isolated_session() as session,
            patch.object(memory_service, "get_llm_provider", return_value=provider),
            patch.object(memory_service, "SUMMARY_DELTA_MAX_CHARS", 40),
        ):
            user = create_user(session, "summary-batches@example.com", "Summary Batches")
            conversation = Conversation(user_id=user.id, title="Summary batches", search_scope="public")
            session.add(conversation)
            session.commit()
            session.refresh(conversation)
            markers = [
                "FIRST_MARKER_123456789",
                "SECOND_MARKER_123456789",
                "TAIL_MARKER_123456789",
                "TAIL_REPLY_123456789",
            ]
            for index, marker in enumerate(markers):
                session.add(
                    Message(
                        conversation_id=conversation.id,
                        role="user" if index % 2 == 0 else "assistant",
                        content=marker,
                        created_at=datetime(2026, 7, 10, tzinfo=timezone.utc) + timedelta(seconds=index),
                    )
                )
            session.commit()

            summary = memory_service.update_conversation_summary(
                session,
                conversation,
                user_message="unused",
                assistant_message="unused",
                user_id=user.id,
            )

            self.assertGreater(provider.calls, 1)
            self.assertTrue(all(marker in summary for marker in markers))
            self.assertEqual(conversation.summary_message_count, len(markers))

    def test_summary_waits_for_a_complete_turn_before_advancing_cursor(self) -> None:
        provider = AccumulatingSummaryProvider()
        with (
            isolated_session() as session,
            patch.object(memory_service, "get_llm_provider", return_value=provider),
        ):
            user = create_user(session, "summary-stable-prefix@example.com", "Summary Stable Prefix")
            conversation = Conversation(user_id=user.id, title="Stable prefix", search_scope="public")
            session.add(conversation)
            session.commit()
            session.add(
                Message(
                    conversation_id=conversation.id,
                    role="user",
                    content="temporary mode: PRIVATE_PENDING",
                )
            )
            session.commit()

            summary = memory_service.update_conversation_summary(
                session,
                conversation,
                user_message="unused",
                assistant_message="unused",
                user_id=user.id,
            )

            self.assertEqual(summary, "")
            self.assertEqual(conversation.summary_message_count, 0)
            self.assertEqual(provider.calls, 0)

            session.add(
                Message(
                    conversation_id=conversation.id,
                    role="assistant",
                    content="PRIVATE_PENDING_REPLY",
                )
            )
            session.commit()
            memory_service.update_conversation_summary(
                session,
                conversation,
                user_message="unused",
                assistant_message="unused",
                user_id=user.id,
            )

            self.assertEqual(conversation.summary_message_count, 2)
            self.assertEqual(provider.calls, 0)

    def test_summary_skips_private_assistant_when_cursor_already_split_the_turn(self) -> None:
        provider = AccumulatingSummaryProvider()
        with (
            isolated_session() as session,
            patch.object(memory_service, "get_llm_provider", return_value=provider),
        ):
            user = create_user(session, "summary-split-private@example.com", "Summary Split Private")
            conversation = Conversation(
                user_id=user.id,
                title="Split private",
                search_scope="public",
                summary_message_count=1,
            )
            session.add(conversation)
            session.commit()
            session.add_all(
                [
                    Message(
                        conversation_id=conversation.id,
                        role="user",
                        content="temporary mode: PRIVATE_SPLIT",
                        created_at=datetime(2026, 7, 10, tzinfo=timezone.utc),
                    ),
                    Message(
                        conversation_id=conversation.id,
                        role="assistant",
                        content="PRIVATE_SPLIT_REPLY",
                        created_at=datetime(2026, 7, 10, tzinfo=timezone.utc) + timedelta(seconds=1),
                    ),
                ]
            )
            session.commit()

            summary = memory_service.update_conversation_summary(
                session,
                conversation,
                user_message="unused",
                assistant_message="unused",
                user_id=user.id,
            )

            self.assertNotIn("PRIVATE_SPLIT_REPLY", summary)
            self.assertEqual(conversation.summary_message_count, 2)
            self.assertEqual(provider.calls, 0)

    def test_purge_removes_embedding_with_the_memory_row(self) -> None:
        with (
            isolated_session() as session,
            patch("app.memory.embedding.get_embedding_provider", return_value=FakeEmbeddingProvider()),
        ):
            user = create_user(session, "purge-cleanup@example.com", "Purge Cleanup")
            memory = memory_service.create_manual_memory(session, user.id, "user prefers concise answers")

            memory_service.purge_user_memory(session, user.id, memory.id)

            self.assertIsNone(session.get(type(memory), memory.id))
            job = session.scalar(
                select(ExternalCleanupJob).where(
                    ExternalCleanupJob.resource_type == "user_memory",
                    ExternalCleanupJob.resource_id == memory.id,
                )
            )
            self.assertIsNone(job)


if __name__ == "__main__":
    unittest.main()
