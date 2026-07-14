from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from app.agents.runtime import build_agent_tools, run_agent_runtime
from app.agents.state import AgentRunState
from app.core.config import get_settings
from app.rag.retrieval import RetrievedChunk


class ScriptedChatModel(BaseChatModel):
    responses: list[AIMessage]
    bound_tool_names: list[list[str]] = Field(default_factory=list)
    system_prompts: list[str] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted-chat-model"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        self.bound_tool_names.append([tool.name for tool in tools])
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        if not self.responses:
            raise AssertionError("Scripted model received an unexpected call")
        self.system_prompts.append(str(messages[0].content))
        return ChatResult(generations=[ChatGeneration(message=self.responses.pop(0))])


def final_answer(content: str) -> AIMessage:
    return AIMessage(content=content)


def tool_call(name: str, query: str, call_id: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": name,
                "args": {"query": query},
                "id": call_id,
                "type": "tool_call",
            }
        ],
    )


def chunk(chunk_id: str, content: str) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        document_id=f"document-{chunk_id}",
        knowledge_base_id="kb-1",
        chunk_index=0,
        content=content,
        score=0.9,
        file_name=f"{chunk_id}.md",
        title_path=None,
        page_number=None,
        section_name=None,
        metadata={},
    )


def evidence(log_id: str, chunks: list[RetrievedChunk]) -> SimpleNamespace:
    return SimpleNamespace(
        chunks=chunks,
        citations=[],
        retrieval_log_id=log_id,
        searched_knowledge_base_ids=["kb-1"],
    )


class AgentRuntimeTests(unittest.TestCase):
    def run_script(
        self,
        state: AgentRunState,
        responses: list[AIMessage],
        *,
        memories: list[dict] | None = None,
        rag_results: list[SimpleNamespace] | None = None,
        settings_updates: dict | None = None,
    ):
        model = ScriptedChatModel(responses=list(responses))
        settings = get_settings().model_copy(update=settings_updates or {})
        logs = iter(SimpleNamespace(id=f"llm-log-{index}") for index in range(1, 20))
        with (
            patch("app.agents.runtime.create_chat_model", return_value=model),
            patch("app.agents.runtime.create_llm_call_log", side_effect=lambda *args, **kwargs: next(logs)),
            patch("app.agents.runtime.recall_long_term_memory", return_value=memories or []) as recall,
            patch("app.agents.runtime.retrieve_rag_evidence", side_effect=rag_results or []) as retrieve,
            patch("app.agents.runtime.get_settings", return_value=settings),
        ):
            run_agent_runtime(None, state)
        return model, recall, retrieve

    def test_direct_model_response_finishes_without_tools(self) -> None:
        state = AgentRunState(user_id="user", knowledge_base_id="kb-1", input="你好")

        model, recall, retrieve = self.run_script(state, [final_answer("你好，有什么可以帮你？")])

        self.assertEqual(state.answer, "你好，有什么可以帮你？")
        self.assertEqual(state.model_call_count, 1)
        self.assertEqual(state.tool_call_count, 0)
        recall.assert_not_called()
        retrieve.assert_not_called()
        self.assertEqual(model.bound_tool_names[0], ["memory", "rag"])

    def test_memory_result_can_be_followed_by_a_final_answer(self) -> None:
        state = AgentRunState(user_id="user", knowledge_base_id="kb-1", input="你记得我的项目吗？")
        memories = [{"id": "memory-1", "category": "current_project", "content": "用户正在构建 Agentic RAG。"}]

        _model, recall, retrieve = self.run_script(
            state,
            [tool_call("memory", "当前项目", "memory-1"), final_answer("你正在构建 Agentic RAG。")],
            memories=memories,
        )

        recall.assert_called_once_with(None, state, "当前项目")
        retrieve.assert_not_called()
        self.assertEqual(state.memory_queries, ["当前项目"])
        self.assertEqual(state.tool_call_count, 1)

    def test_memory_miss_does_not_force_rag(self) -> None:
        state = AgentRunState(user_id="user", knowledge_base_id="kb-1", input="你记得我的项目吗？")

        _model, recall, retrieve = self.run_script(
            state,
            [tool_call("memory", "当前项目", "memory-1"), final_answer("保存的记忆中没有找到该项目。")],
        )

        recall.assert_called_once()
        retrieve.assert_not_called()
        self.assertIn("没有找到", state.answer)

    def test_agent_can_use_memory_and_multiple_distinct_rag_queries(self) -> None:
        state = AgentRunState(user_id="user", knowledge_base_id="kb-1", input="结合我的项目说明差旅和报销制度")
        first = chunk("chunk-1", "差旅需要提前审批。")
        second = chunk("chunk-2", "餐费按发票实报实销。")

        _model, recall, retrieve = self.run_script(
            state,
            [
                tool_call("memory", "当前项目", "memory-1"),
                tool_call("rag", "差旅审批制度", "rag-1"),
                tool_call("rag", "餐费报销标准", "rag-2"),
                final_answer("差旅需提前审批[1]，餐费按发票报销[2]。"),
            ],
            memories=[{"id": "memory-1", "category": "current_project", "content": "Agentic RAG"}],
            rag_results=[evidence("retrieval-1", [first]), evidence("retrieval-2", [second])],
        )

        recall.assert_called_once()
        self.assertEqual([call.args[3] for call in retrieve.call_args_list], ["差旅审批制度", "餐费报销标准"])
        self.assertEqual(state.rag_queries, ["差旅审批制度", "餐费报销标准"])
        self.assertEqual(state.retrieval_log_ids, ["retrieval-1", "retrieval-2"])
        self.assertEqual([citation.chunk_id for citation in state.citations], ["chunk-1", "chunk-2"])

    def test_repeated_rag_query_is_blocked_and_counts_toward_total_budget(self) -> None:
        state = AgentRunState(user_id="user", knowledge_base_id="kb-1", input="查询制度")

        _model, _recall, retrieve = self.run_script(
            state,
            [
                tool_call("rag", "差旅制度", "rag-1"),
                tool_call("rag", "差旅制度", "rag-2"),
                final_answer("只执行了一次有效检索。"),
            ],
            rag_results=[evidence("retrieval-1", [chunk("chunk-1", "差旅制度")])],
        )

        retrieve.assert_called_once()
        self.assertEqual(state.tool_call_count, 2)
        self.assertEqual(state.rag_tool_call_count, 1)
        self.assertEqual(state.tool_observations[-1]["status"], "duplicate")

    def test_last_model_call_has_no_tools_and_must_finish(self) -> None:
        state = AgentRunState(user_id="user", knowledge_base_id="kb-1", input="回顾信息")

        model, _recall, _retrieve = self.run_script(
            state,
            [
                tool_call("memory", "项目", "memory-1"),
                tool_call("memory", "偏好", "memory-2"),
                final_answer("已根据现有信息作答。"),
            ],
            settings_updates={"agent_max_model_calls": 3, "agent_max_memory_calls": 3},
        )

        self.assertEqual(state.model_call_count, 3)
        self.assertIn("No tools are available for this step", model.system_prompts[-1])
        self.assertEqual(state.answer, "已根据现有信息作答。")

    def test_memory_disabled_exposes_only_rag(self) -> None:
        state = AgentRunState(
            user_id="user",
            knowledge_base_id="kb-1",
            input="查询制度",
            memory_enabled=False,
        )

        self.assertEqual([item.name for item in build_agent_tools(None, state)], ["rag"])
        model, recall, _retrieve = self.run_script(state, [final_answer("直接回答。")])
        recall.assert_not_called()
        self.assertEqual(model.bound_tool_names[0], ["rag"])


if __name__ == "__main__":
    unittest.main()
