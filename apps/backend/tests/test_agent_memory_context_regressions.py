from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from app.agents.memory_agent import filter_memory_history, load_memory_context
from app.agents.state import AgentGraphState
from app.db.models.conversation import Conversation, Message
from helpers import create_user, isolated_session


def memory(memory_id: str, *, profile: bool) -> SimpleNamespace:
    return SimpleNamespace(
        id=memory_id,
        content=f"memory {memory_id}",
        category="preference" if profile else "project",
        kind="profile" if profile else "fact",
        status="active",
        memory_layer="profile" if profile else "semantic",
        canonical_key=None,
        profile_slot=None,
        scope_type="global",
        scope_id=None,
        pinned=profile,
        revision=1,
        extra_metadata={},
    )


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

    def test_load_context_uses_one_profile_aware_recall_and_filtered_history(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "memory-context@example.com", "Memory Context")
            conversation = Conversation(user_id=user.id, title="Context", search_scope="accessible")
            session.add(conversation)
            session.commit()
            session.refresh(conversation)
            state = AgentGraphState(
                user_id=user.id,
                knowledge_base_id=None,
                conversation_id=conversation.id,
                message_id="current-message",
                input="current question",
            )
            loaded = [memory("profile", profile=True), memory("semantic", profile=False)]
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
                patch("app.agents.memory_agent.retrieve_relevant_memories", return_value=loaded) as retrieve,
                patch("app.agents.memory_agent.build_memory_context_for_question", side_effect=build_context),
            ):
                load_memory_context(session, state)

            retrieve.assert_called_once()
            self.assertTrue(retrieve.call_args.kwargs["include_profile"])
            self.assertEqual(retrieve.call_args.kwargs["limit"], 25)
            self.assertEqual([item["id"] for item in state.profile_memories], ["profile"])
            self.assertEqual([item["id"] for item in state.long_term_memories], ["semantic"])
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
            state = AgentGraphState(
                user_id=caller.id,
                knowledge_base_id=None,
                conversation_id=conversation.id,
                input="question",
            )

            with self.assertRaises(HTTPException) as raised:
                load_memory_context(session, state)

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

            state = AgentGraphState(
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
                patch("app.agents.memory_agent.retrieve_relevant_memories", return_value=[]),
            ):
                load_memory_context(session, state)

            self.assertNotIn("PRIVATE_SECRET", state.memory_context)
            self.assertNotIn("PRIVATE_REPLY", state.memory_context)
            self.assertNotIn("CURRENT_QUESTION", state.memory_context)
            self.assertEqual(state.memory_context.count("NORMAL_QUESTION"), 1)
            self.assertEqual(state.memory_context.count("NORMAL_ANSWER"), 1)


if __name__ == "__main__":
    unittest.main()
