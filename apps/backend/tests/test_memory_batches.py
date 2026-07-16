from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.agents.memory_agent import load_core_memory_context, recall_long_term_memory
from app.agents.state import AgentRunState
from app.core.config import get_settings
from app.memory.context import select_memories_by_batches
from app.services.memory_service import build_memory_context_for_question


def memory_dict(memory_id: str) -> dict:
    return {
        "id": memory_id,
        "content": f"memory {memory_id}",
        "category": "general",
        "kind": "fact",
        "metadata": {},
    }


def memory_row(memory_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=memory_id,
        content=f"memory {memory_id}",
        category="general",
        kind="fact",
        status="active",
        memory_layer="semantic",
        canonical_key="",
        profile_slot="",
        scope_type="user",
        scope_id="",
        pinned=False,
        revision=1,
        extra_metadata={},
    )


class MemoryBatchSelectionTests(unittest.TestCase):
    def test_two_batches_share_the_context_limit(self) -> None:
        memories = [memory_dict(f"m{index}") for index in range(1, 13)]

        selected = select_memories_by_batches(
            memories,
            [
                [f"m{index}" for index in range(1, 7)],
                [f"m{index}" for index in range(7, 13)],
            ],
            limit=10,
        )

        selected_ids = [memory["id"] for memory in selected]
        self.assertEqual(selected_ids[::2], ["m1", "m2", "m3", "m4", "m5"])
        self.assertEqual(selected_ids[1::2], ["m7", "m8", "m9", "m10", "m11"])

    def test_three_batches_are_interleaved_before_filling_remaining_slots(self) -> None:
        memories = [memory_dict(f"m{index}") for index in range(1, 13)]

        selected = select_memories_by_batches(
            memories,
            [
                ["m1", "m2", "m3", "m4"],
                ["m5", "m6", "m7", "m8"],
                ["m9", "m10", "m11", "m12"],
            ],
            limit=10,
        )

        self.assertEqual(
            [memory["id"] for memory in selected],
            ["m1", "m5", "m9", "m2", "m6", "m10", "m3", "m7", "m11", "m4"],
        )

    def test_recall_records_only_new_memories_in_each_batch(self) -> None:
        state = AgentRunState(
            user_id="user",
            knowledge_base_id=None,
            input="question",
            memory_enabled=True,
        )
        first = [memory_row("m1"), memory_row("m2")]
        second = [memory_row("m2"), memory_row("m3")]
        third = [memory_row("m3")]

        with (
            patch(
                "app.agents.memory_agent.retrieve_relevant_memories",
                side_effect=[first, second, third],
            ),
            patch(
                "app.agents.memory_agent.build_memory_context_for_question",
                return_value="context",
            ) as build_context,
        ):
            recall_long_term_memory(None, state, "first query")
            recall_long_term_memory(None, state, "second query")
            recall_long_term_memory(None, state, "third query")

        self.assertEqual([memory["id"] for memory in state.long_term_memories], ["m1", "m2", "m3"])
        self.assertEqual(state.memory_batches, [["m1", "m2"], ["m3"], []])
        self.assertEqual(
            build_context.call_args.kwargs["preloaded_memory_batches"],
            [["m1", "m2"], ["m3"], []],
        )

    def test_loading_a_new_turn_clears_existing_batches(self) -> None:
        state = AgentRunState(
            user_id="user",
            knowledge_base_id=None,
            input="question",
            memory_enabled=False,
            memory_batches=[["stale-memory"]],
        )

        load_core_memory_context(None, state)

        self.assertEqual(state.memory_batches, [])

    def test_context_builder_uses_memories_from_both_batches(self) -> None:
        memories = [memory_dict(f"m{index}") for index in range(1, 13)]
        settings = get_settings().model_copy(
            update={
                "memory_context_max_long_memories": 10,
                "memory_context_max_tokens": 1600,
            }
        )

        with patch("app.services.memory_service.get_settings", return_value=settings):
            context = build_memory_context_for_question(
                None,
                "user",
                "recall project facts",
                preloaded_short_memory=[],
                preloaded_long_memories=memories,
                preloaded_memory_batches=[
                    [f"m{index}" for index in range(1, 7)],
                    [f"m{index}" for index in range(7, 13)],
                ],
                preloaded_profile_memories=[],
            )

        self.assertIn("memory m5", context)
        self.assertIn("memory m11", context)
        self.assertNotIn("memory m6", context)
        self.assertNotIn("memory m12", context)


if __name__ == "__main__":
    unittest.main()
