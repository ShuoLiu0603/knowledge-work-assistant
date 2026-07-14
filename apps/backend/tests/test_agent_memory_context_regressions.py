from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi import HTTPException

from app.agents.memory_agent import filter_memory_history, load_core_memory_context
from app.agents.state import AgentRunState
from app.db.models.conversation import Conversation, Message
from helpers import create_user, isolated_session


class AgentMemoryContextRegressionTests(unittest.TestCase):
    def test_history_excludes_temporary_pair_and_only_the_latest_current_turn(self) -> None:
        messages = [
            {"role": "user", "content": "repeat"},
            {"role": "assistant", "content": "old answer"},
            {"role": "user", "content": "temporary mode: secret"},
            {"role": "assistant", "content": "secret answer"},
            {"role": "user", "content": "repeat"},
        ]

        filtered, metadata = filter_memory_history(
            messages,
            current_input="repeat",
            current_message_id="current-message",
        )

        self.assertEqual(
            filtered,
            [
                {"role": "user", "content": "repeat"},
                {"role": "assistant", "content": "old answer"},
            ],
        )
        self.assertEqual(metadata["private_turn_message_count"], 2)
        self.assertTrue(metadata["current_turn_removed"])

    def test_load_context_injects_core_profile_without_semantic_recall(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "memory-context@example.com", "Memory Context")
            conversation = Conversation(user_id=user.id, title="Context", search_scope="accessible")
            session.add(conversation)
            session.commit()
            session.refresh(conversation)
            state = AgentRunState(
                user_id=user.id,
                knowledge_base_id=None,
                conversation_id=conversation.id,
                message_id="current-message",
                input="current question",
            )
            loaded = [
                {
                    "id": "profile",
                    "content": "User prefers Chinese answers",
                    "category": "language",
                    "kind": "preference",
                    "memory_layer": "profile",
                }
            ]
            captured: dict = {}

            def build_context(*args, **kwargs) -> str:
                captured.update(kwargs)
                return "context"

            with (
                patch(
                    "app.agents.memory_agent.get_short_term_memory",
                    return_value=[
                        {"role": "user", "content": "older question"},
                        {"role": "assistant", "content": "older answer"},
                        {"role": "user", "content": "current question"},
                    ],
                ),
                patch("app.agents.memory_agent.list_core_profile_context", return_value=loaded),
                patch("app.agents.memory_agent.retrieve_relevant_memories") as retrieve,
                patch("app.agents.memory_agent.build_memory_context_for_question", side_effect=build_context),
            ):
                load_core_memory_context(session, state)

            retrieve.assert_not_called()
            self.assertEqual([item["id"] for item in state.profile_memories], ["profile"])
            self.assertEqual(state.long_term_memories, [])
            self.assertEqual(
                captured["preloaded_short_memory"],
                [
                    {"role": "user", "content": "older question"},
                    {"role": "assistant", "content": "older answer"},
                ],
            )
            self.assertTrue(state.trace[-1]["output"]["current_turn_removed"])

    def test_load_context_rejects_another_users_conversation(self) -> None:
        with isolated_session() as session:
            owner = create_user(session, "context-owner@example.com", "Context Owner")
            caller = create_user(session, "context-caller@example.com", "Context Caller")
            conversation = Conversation(user_id=owner.id, title="Private", search_scope="accessible")
            session.add(conversation)
            session.commit()
            session.refresh(conversation)
            state = AgentRunState(
                user_id=caller.id,
                knowledge_base_id=None,
                conversation_id=conversation.id,
                input="question",
            )

            with self.assertRaises(HTTPException) as raised:
                load_core_memory_context(session, state)

            self.assertEqual(raised.exception.status_code, 404)

    def test_db_gap_fill_does_not_reintroduce_private_turns_or_duplicate_recent_history(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "private-gap@example.com", "Private Gap")
            conversation = Conversation(user_id=user.id, title="Private gap", search_scope="accessible")
            session.add(conversation)
            session.commit()
            session.refresh(conversation)

            started_at = datetime(2026, 7, 10, tzinfo=timezone.utc)
            messages = [
                Message(
                    conversation_id=conversation.id,
                    role="user",
                    content="NORMAL_QUESTION",
                    created_at=started_at,
                ),
                Message(
                    conversation_id=conversation.id,
                    role="assistant",
                    content="NORMAL_ANSWER",
                    created_at=started_at + timedelta(seconds=1),
                ),
                Message(
                    conversation_id=conversation.id,
                    role="user",
                    content="PRIVATE_SECRET",
                    memory_enabled=False,
                    created_at=started_at + timedelta(seconds=2),
                ),
                Message(
                    conversation_id=conversation.id,
                    role="assistant",
                    content="PRIVATE_REPLY",
                    memory_enabled=False,
                    created_at=started_at + timedelta(seconds=3),
                ),
                Message(
                    conversation_id=conversation.id,
                    role="user",
                    content="CURRENT_QUESTION",
                    created_at=started_at + timedelta(seconds=4),
                ),
            ]
            session.add_all(messages)
            session.commit()
            current = messages[-1]

            state = AgentRunState(
                user_id=user.id,
                knowledge_base_id=None,
                conversation_id=conversation.id,
                message_id=current.id,
                input=current.content,
            )
            redis_window = [
                {"role": "user", "content": "NORMAL_QUESTION"},
                {"role": "assistant", "content": "NORMAL_ANSWER"},
                {"role": "user", "content": "CURRENT_QUESTION"},
            ]

            with (
                patch("app.agents.memory_agent.get_short_term_memory", return_value=redis_window),
                patch("app.agents.memory_agent.list_core_profile_context", return_value=[]),
            ):
                load_core_memory_context(session, state)

            self.assertNotIn("PRIVATE_SECRET", state.memory_context)
            self.assertNotIn("PRIVATE_REPLY", state.memory_context)
            self.assertNotIn("CURRENT_QUESTION", state.memory_context)
            self.assertEqual(state.memory_context.count("NORMAL_QUESTION"), 1)
            self.assertEqual(state.memory_context.count("NORMAL_ANSWER"), 1)


if __name__ == "__main__":
    unittest.main()
