from __future__ import annotations

import unittest
from unittest.mock import patch

from app.agents.rag_agent import answer_with_rag
from app.agents.state import AgentGraphState
from app.llm.provider import LlmCompletion
from helpers import create_user, isolated_session


class FakeMemoryAnswerProvider:
    def answer_memory_question_with_metadata(self, question: str, memory_context: str, on_token=None) -> LlmCompletion:
        content = "我记得：用户偏好中文回答。"
        if on_token:
            on_token(content)
        return LlmCompletion(
            content=content,
            provider="openai_compatible",
            model_name="fake-chat",
            prompt_tokens=10,
            completion_tokens=8,
            total_tokens=18,
            latency_ms=1,
            status="success",
        )


class RagAgentMemoryAnswerTests(unittest.TestCase):
    def test_memory_intent_bypasses_knowledge_retrieval(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "rag-memory@example.com", "Rag Memory")
            state = AgentGraphState(
                user_id=user.id,
                knowledge_base_id="kb-id",
                intent="memory",
                input="你记得我什么？",
                memory_context="长期记忆:\n- 用户偏好中文回答\n\n会话摘要:\n无\n\n最近对话:\n- 无",
            )

            with (
                patch("app.agents.rag_agent.get_llm_provider", return_value=FakeMemoryAnswerProvider()),
                patch("app.agents.rag_agent.build_rag_answer") as build_rag_answer,
            ):
                answer_with_rag(session, state)

            build_rag_answer.assert_not_called()
            self.assertIn("用户偏好中文回答", state.answer)
            self.assertEqual(state.citations, [])
            self.assertIsNone(state.retrieval_log_id)
            self.assertIsNotNone(state.llm_log_id)

    def test_memory_wording_with_rag_intent_uses_knowledge_retrieval(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "rag-memory-wording@example.com", "Rag Memory Wording")
            state = AgentGraphState(
                user_id=user.id,
                knowledge_base_id="kb-id",
                intent="rag",
                input="你记得报销政策吗？",
                memory_context="长期记忆:\n- 用户偏好中文回答",
            )

            with (
                patch("app.agents.rag_agent.get_llm_provider", return_value=FakeMemoryAnswerProvider()),
                patch("app.agents.rag_agent.build_rag_answer") as build_rag_answer,
            ):
                build_rag_answer.return_value.answer = "报销政策回答。[1]"
                build_rag_answer.return_value.citations = []
                build_rag_answer.return_value.retrieval_log_id = "retrieval-log-id"
                build_rag_answer.return_value.llm_log_id = "llm-log-id"

                answer_with_rag(session, state)

            build_rag_answer.assert_called_once()
            self.assertEqual(state.answer, "报销政策回答。[1]")
            self.assertEqual(state.retrieval_log_id, "retrieval-log-id")


if __name__ == "__main__":
    unittest.main()
