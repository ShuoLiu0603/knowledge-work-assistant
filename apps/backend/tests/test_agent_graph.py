from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import select

from app.agents.graph import run_agent_graph
from app.agents.state import AgentGraphState
from app.db.models.user_memory import UserMemoryUpdateJob
from app.llm.provider import LlmCompletion
from app.schemas.knowledge_base import KnowledgeBaseCreate
from app.services.knowledge_base_service import create_knowledge_base
from helpers import create_user, isolated_session


class FakeAgentLlmProvider:
    provider_name = "openai_compatible"

    def classify_intent(self, text: str) -> str:
        return "rag"

    def extract_memory_candidates(self, text: str) -> list[str]:
        return []


class FakeSummaryLlmProvider(FakeAgentLlmProvider):
    def classify_intent(self, text: str) -> str:
        return "summary"

    def summarize_with_metadata(
        self,
        text: str,
        request_text: str = "",
        style_context: str = "",
    ) -> LlmCompletion:
        return LlmCompletion(
            content="最终摘要",
            provider="openai_compatible",
            model_name="fake-chat",
            prompt_tokens=5,
            completion_tokens=3,
            total_tokens=8,
            latency_ms=1,
            status="success",
        )


class AgentGraphTests(unittest.TestCase):
    def test_agent_graph_records_supervisor_and_rag_trace(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "agent-graph@example.com", "AgentGraph")
            kb = create_knowledge_base(
                session,
                user.id,
                KnowledgeBaseCreate(name="Agent Graph KB", visibility="private"),
            )
            state = AgentGraphState(
                user_id=user.id,
                knowledge_base_id=kb.id,
                input="What is the policy?",
            )

            with (
                patch("app.agents.supervisor.get_llm_provider", return_value=FakeAgentLlmProvider()),
                patch("app.services.memory_service.get_llm_provider", return_value=FakeAgentLlmProvider()),
                patch("app.agents.rag_agent.build_rag_answer") as build_rag_answer,
            ):
                build_rag_answer.return_value.answer = "No matching context."
                build_rag_answer.return_value.citations = []
                build_rag_answer.return_value.retrieval_log_id = "retrieval-log-id"
                build_rag_answer.return_value.llm_log_id = "llm-log-id"

                run_agent_graph(session, state)

            trace_nodes = [step["node"] for step in state.trace]
            graph_trace = state.trace[-1]

            self.assertEqual(state.status, "completed")
            self.assertIn("memory_agent", trace_nodes)
            self.assertIn("supervisor", trace_nodes)
            self.assertIn("rag_agent", trace_nodes)
            self.assertEqual(graph_trace["node"], "graph")
            self.assertIn(graph_trace["output"]["backend"], {"langgraph", "sequential"})
            self.assertEqual(state.retrieval_log_id, "retrieval-log-id")
            self.assertEqual(state.llm_log_id, "llm-log-id")

    def test_agent_graph_can_run_with_sequential_backend(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "agent-graph-sequential@example.com", "AgentGraphSequential")
            kb = create_knowledge_base(
                session,
                user.id,
                KnowledgeBaseCreate(name="Sequential Agent Graph KB", visibility="private"),
            )
            state = AgentGraphState(
                user_id=user.id,
                knowledge_base_id=kb.id,
                input="What is the policy?",
            )

            with (
                patch("app.agents.graph.get_settings", return_value=SimpleNamespace(agent_graph_backend="sequential")),
                patch("app.agents.supervisor.get_llm_provider", return_value=FakeAgentLlmProvider()),
                patch("app.services.memory_service.get_llm_provider", return_value=FakeAgentLlmProvider()),
                patch("app.agents.rag_agent.build_rag_answer") as build_rag_answer,
            ):
                build_rag_answer.return_value.answer = "No matching context."
                build_rag_answer.return_value.citations = []
                build_rag_answer.return_value.retrieval_log_id = "retrieval-log-id"
                build_rag_answer.return_value.llm_log_id = "llm-log-id"

                run_agent_graph(session, state)

            graph_trace = state.trace[-1]
            self.assertEqual(state.status, "completed")
            self.assertEqual(graph_trace["output"]["requested_backend"], "sequential")
            self.assertEqual(graph_trace["output"]["backend"], "sequential")

    def test_agent_graph_fails_when_langgraph_is_missing(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "agent-graph-missing@example.com", "AgentGraphMissing")
            kb = create_knowledge_base(
                session,
                user.id,
                KnowledgeBaseCreate(name="Missing Agent Graph KB", visibility="private"),
            )
            state = AgentGraphState(
                user_id=user.id,
                knowledge_base_id=kb.id,
                input="What is the policy?",
            )

            with (
                patch("app.agents.graph.get_settings", return_value=SimpleNamespace(agent_graph_backend="langgraph")),
                patch("app.agents.graph._run_langgraph_nodes", side_effect=ImportError("missing langgraph")),
            ):
                run_agent_graph(session, state)

            graph_trace = state.trace[-1]
            self.assertEqual(state.status, "failed")
            self.assertEqual(state.error_message, "missing langgraph")
            self.assertEqual(graph_trace["output"]["requested_backend"], "langgraph")
            self.assertEqual(graph_trace["output"]["backend"], "langgraph")
            self.assertEqual(graph_trace["output"]["status"], "failed")

    def test_langgraph_returned_state_is_copied_back_to_outer_state(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "agent-graph-returned-state@example.com", "AgentGraphReturnedState")
            state = AgentGraphState(
                user_id=user.id,
                knowledge_base_id=None,
                input="你好",
            )
            returned_state = AgentGraphState(
                user_id=user.id,
                knowledge_base_id=None,
                input="你好",
                intent="chat",
                answer="你好，有什么可以帮你？",
                memory_actions=[{"action": "ignore", "memory_id": None, "content": "", "reason": "test"}],
                trace=[{"node": "fake_langgraph", "action": "done", "input": {}, "output": {}}],
            )

            with (
                patch("app.agents.graph.get_settings", return_value=SimpleNamespace(agent_graph_backend="langgraph")),
                patch("app.agents.graph._run_langgraph_nodes", return_value=returned_state),
            ):
                run_agent_graph(session, state)

            self.assertEqual(state.intent, "chat")
            self.assertEqual(state.answer, "你好，有什么可以帮你？")
            self.assertEqual(state.memory_actions[0]["reason"], "test")
            self.assertEqual(state.trace[0]["node"], "fake_langgraph")
            self.assertEqual(state.trace[-1]["node"], "graph")

    def test_summary_agent_does_not_stream_intermediate_rag_answer(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "agent-graph-summary@example.com", "AgentGraphSummary")
            kb = create_knowledge_base(
                session,
                user.id,
                KnowledgeBaseCreate(name="Summary Agent Graph KB", visibility="private"),
            )
            tokens: list[str] = []
            state = AgentGraphState(
                user_id=user.id,
                knowledge_base_id=kb.id,
                input="请总结这份制度",
                token_callback=tokens.append,
            )

            with (
                patch("app.agents.graph.get_settings", return_value=SimpleNamespace(agent_graph_backend="sequential")),
                patch("app.agents.supervisor.get_llm_provider", return_value=FakeSummaryLlmProvider()),
                patch("app.services.memory_service.get_llm_provider", return_value=FakeAgentLlmProvider()),
                patch("app.agents.summary_agent.get_llm_provider", return_value=FakeSummaryLlmProvider()),
                patch(
                    "app.agents.summary_agent.retrieve_rag_evidence",
                    return_value=SimpleNamespace(
                        chunks=[],
                        citations=[],
                        retrieval_log_id="retrieval-log-id",
                        searched_knowledge_base_ids=[kb.id],
                    ),
                ),
                patch("app.agents.summary_agent.format_answer_context", return_value="RAG evidence"),
            ):
                run_agent_graph(session, state)

            self.assertEqual(tokens, ["最终摘要"])
            self.assertEqual(state.answer, "最终摘要")
            self.assertEqual(state.intent, "summary")

    def test_memory_update_failure_does_not_fail_completed_answer(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "agent-graph-memory-failure@example.com", "AgentGraphMemoryFailure")
            kb = create_knowledge_base(
                session,
                user.id,
                KnowledgeBaseCreate(name="Memory Failure KB", visibility="private"),
            )
            state = AgentGraphState(
                user_id=user.id,
                knowledge_base_id=kb.id,
                input="What is the policy?",
            )

            with (
                patch("app.agents.graph.get_settings", return_value=SimpleNamespace(agent_graph_backend="sequential")),
                patch("app.agents.supervisor.get_llm_provider", return_value=FakeAgentLlmProvider()),
                patch("app.agents.rag_agent.build_rag_answer") as build_rag_answer,
                patch("app.agents.memory_agent.process_user_memory", side_effect=RuntimeError("memory offline")),
            ):
                build_rag_answer.return_value.answer = "Grounded answer."
                build_rag_answer.return_value.citations = []
                build_rag_answer.return_value.retrieval_log_id = "retrieval-log-id"
                build_rag_answer.return_value.llm_log_id = "llm-log-id"

                run_agent_graph(session, state)

            self.assertEqual(state.status, "completed")
            self.assertEqual(state.answer, "Grounded answer.")
            self.assertEqual(state.memory_actions[0]["action"], "ignore")
            self.assertIn("memory update failed", state.memory_actions[0]["reason"])

    def test_async_memory_update_queues_after_answer_without_processing_inline(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "agent-graph-memory-async@example.com", "AgentGraphMemoryAsync")
            kb = create_knowledge_base(
                session,
                user.id,
                KnowledgeBaseCreate(name="Memory Async KB", visibility="private"),
            )
            state = AgentGraphState(
                user_id=user.id,
                knowledge_base_id=kb.id,
                input="What is the policy?",
                conversation_id="conversation-id",
                message_id="user-message-id",
            )

            with (
                patch("app.agents.graph.get_settings", return_value=SimpleNamespace(agent_graph_backend="sequential")),
                patch("app.agents.memory_agent.get_settings", return_value=SimpleNamespace(memory_update_mode="async")),
                patch("app.agents.supervisor.get_llm_provider", return_value=FakeAgentLlmProvider()),
                patch("app.agents.rag_agent.build_rag_answer") as build_rag_answer,
                patch("app.agents.memory_agent.process_user_memory") as process_user_memory,
                patch("app.agents.memory_agent.enqueue_memory_update") as enqueue_memory_update,
            ):
                build_rag_answer.return_value.answer = "Grounded answer."
                build_rag_answer.return_value.citations = []
                build_rag_answer.return_value.retrieval_log_id = "retrieval-log-id"
                build_rag_answer.return_value.llm_log_id = "llm-log-id"

                run_agent_graph(session, state)

            self.assertEqual(state.status, "completed")
            self.assertEqual(state.answer, "Grounded answer.")
            self.assertEqual(state.memory_actions[0]["action"], "queued")
            job_id = state.memory_actions[0]["job_id"]
            job = session.get(UserMemoryUpdateJob, job_id)
            self.assertIsNotNone(job)
            self.assertEqual(job.user_id, user.id)
            self.assertEqual(job.conversation_id, "conversation-id")
            self.assertEqual(job.message_id, "user-message-id")
            self.assertEqual(job.user_message, "What is the policy?")
            self.assertEqual(job.assistant_message, "Grounded answer.")
            self.assertEqual(job.status, "queued")
            process_user_memory.assert_not_called()
            enqueue_memory_update.assert_called_once_with(job_id)

    def test_no_memory_turn_skips_recall_and_memory_update(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "agent-graph-no-memory@example.com", "AgentGraphNoMemory")
            state = AgentGraphState(
                user_id=user.id,
                knowledge_base_id=None,
                input="Please answer without memory.",
            )

            with (
                patch("app.agents.graph.get_settings", return_value=SimpleNamespace(agent_graph_backend="sequential")),
                patch("app.agents.supervisor.get_llm_provider", return_value=FakeAgentLlmProvider()),
                patch("app.agents.rag_agent.build_rag_answer") as build_rag_answer,
                patch("app.agents.memory_agent.retrieve_relevant_memories") as retrieve_relevant_memories,
                patch("app.agents.memory_agent.process_user_memory") as process_user_memory,
                patch("app.agents.memory_agent.enqueue_memory_update") as enqueue_memory_update,
            ):
                build_rag_answer.return_value.answer = "Grounded answer."
                build_rag_answer.return_value.citations = []
                build_rag_answer.return_value.retrieval_log_id = None
                build_rag_answer.return_value.llm_log_id = None

                run_agent_graph(session, state)

            retrieve_relevant_memories.assert_not_called()
            process_user_memory.assert_not_called()
            enqueue_memory_update.assert_not_called()
            self.assertEqual(state.memory_actions[0]["action"], "ignore")
            self.assertEqual(state.memory_actions[0]["reason"], "user requested no memory for this turn")
            self.assertTrue(any(step["action"] == "load_context_skipped" for step in state.trace))
            self.assertTrue(any(step["action"] == "update_user_memories_skipped" for step in state.trace))

    def test_async_memory_update_keeps_queued_job_when_worker_dispatch_fails(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "agent-graph-memory-dispatch-failure@example.com", "AgentGraphMemoryDispatchFailure")
            state = AgentGraphState(
                user_id=user.id,
                knowledge_base_id=None,
                input="Hello",
            )

            with (
                patch("app.agents.graph.get_settings", return_value=SimpleNamespace(agent_graph_backend="sequential")),
                patch("app.agents.memory_agent.get_settings", return_value=SimpleNamespace(memory_update_mode="async")),
                patch("app.agents.supervisor.get_llm_provider", return_value=FakeAgentLlmProvider()),
                patch("app.agents.rag_agent.build_rag_answer") as build_rag_answer,
                patch("app.agents.memory_agent.enqueue_memory_update", side_effect=RuntimeError("broker down")),
            ):
                build_rag_answer.return_value.answer = "Hello."
                build_rag_answer.return_value.citations = []
                build_rag_answer.return_value.retrieval_log_id = None
                build_rag_answer.return_value.llm_log_id = None

                run_agent_graph(session, state)

            self.assertEqual(state.status, "completed")
            self.assertEqual(state.memory_actions[0]["action"], "queued")
            self.assertIn("worker dispatch failed", state.memory_actions[0]["reason"])
            job = session.scalar(select(UserMemoryUpdateJob).where(UserMemoryUpdateJob.user_id == user.id))
            self.assertIsNotNone(job)
            self.assertEqual(job.status, "queued")
            self.assertEqual(job.error_message, "worker dispatch failed: broker down")

    def test_disabled_memory_update_is_explicit_in_trace(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "agent-graph-memory-disabled@example.com", "AgentGraphMemoryDisabled")
            state = AgentGraphState(
                user_id=user.id,
                knowledge_base_id=None,
                input="Hello",
            )

            with (
                patch("app.agents.graph.get_settings", return_value=SimpleNamespace(agent_graph_backend="sequential")),
                patch("app.agents.memory_agent.get_settings", return_value=SimpleNamespace(memory_update_mode="disabled")),
                patch("app.agents.supervisor.get_llm_provider", return_value=FakeAgentLlmProvider()),
                patch("app.agents.rag_agent.build_rag_answer") as build_rag_answer,
                patch("app.agents.memory_agent.process_user_memory") as process_user_memory,
            ):
                build_rag_answer.return_value.answer = "Hello."
                build_rag_answer.return_value.citations = []
                build_rag_answer.return_value.retrieval_log_id = None
                build_rag_answer.return_value.llm_log_id = None

                run_agent_graph(session, state)

            self.assertEqual(state.status, "completed")
            self.assertEqual(state.memory_actions[0]["action"], "ignore")
            self.assertEqual(state.memory_actions[0]["reason"], "memory update disabled")
            self.assertTrue(any(step["action"] == "update_user_memories_disabled" for step in state.trace))
            process_user_memory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
