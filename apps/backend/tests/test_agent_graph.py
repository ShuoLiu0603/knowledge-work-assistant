from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.agents.graph import run_agent_graph
from app.agents.state import AgentGraphState
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

    def summarize_with_metadata(self, text: str) -> LlmCompletion:
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
                patch("app.agents.rag_agent.build_rag_answer") as build_rag_answer,
            ):
                build_rag_answer.side_effect = lambda *args, **kwargs: (
                    kwargs["on_token"]("中间RAG答案") if kwargs.get("on_token") else None
                ) or SimpleNamespace(
                    answer="中间RAG答案",
                    citations=[],
                    retrieval_log_id="retrieval-log-id",
                    llm_log_id="rag-llm-log-id",
                )

                run_agent_graph(session, state)

            self.assertEqual(tokens, [])
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


if __name__ == "__main__":
    unittest.main()
