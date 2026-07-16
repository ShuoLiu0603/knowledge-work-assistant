from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from app.agents.runtime import build_agent_tools, build_system_prompt, run_agent_runtime
from app.agents.state import AgentRunState
from app.core.config import get_settings
from app.llm.token_counter import count_tokens
from app.rag.retrieval import RetrievedChunk


class ScriptedChatModel(BaseChatModel):
    responses: list[AIMessage]
    bound_tool_names: list[list[str]] = Field(default_factory=list)
    system_prompts: list[str] = Field(default_factory=list)
    message_snapshots: list[list[tuple[str, str]]] = Field(default_factory=list)
    logged_prompt_tokens: list[int] = Field(default_factory=list)

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
        self.message_snapshots.append([(message.type, str(message.content)) for message in messages])
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

        def record_log(_db, completion, *args, **kwargs):
            model.logged_prompt_tokens.append(completion.prompt_tokens)
            return next(logs)

        with (
            patch("app.agents.runtime.create_chat_model", return_value=model),
            patch("app.agents.runtime.create_llm_call_log", side_effect=record_log),
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
        self.assertEqual(
            model.logged_prompt_tokens[0],
            count_tokens(f"{model.system_prompts[0]}\n你好"),
        )

    def test_system_prompt_does_not_claim_deferred_memory_write_succeeded(self) -> None:
        state = AgentRunState(user_id="user", knowledge_base_id=None, input="其实我也喜欢踢足球")

        prompt = build_system_prompt(state)

        self.assertIn("Long-term memory persistence runs after the answer and may fail", prompt)
        self.assertIn("Do not claim that a new user fact has already been saved or updated", prompt)

    def test_system_prompt_requires_evidence_sufficiency_and_bounded_retries(self) -> None:
        state = AgentRunState(user_id="user", knowledge_base_id="kb-1", input="查询制度")

        model, _recall, _retrieve = self.run_script(state, [final_answer("直接回答。")])

        prompt = model.system_prompts[0]
        self.assertIn("If every factual component required for the answer is already supported", prompt)
        self.assertIn("Do not call a tool merely to increase confidence", prompt)
        self.assertIn("If that retry also makes no progress, stop using that tool", prompt)

    def test_recent_conversation_is_passed_as_typed_messages(self) -> None:
        state = AgentRunState(
            user_id="user",
            knowledge_base_id="kb-1",
            input="current question",
            short_term_memory=[
                {"role": "user", "content": "earlier question"},
                {"role": "assistant", "content": "earlier answer"},
            ],
            memory_context="Conversation summary:\nsummary only",
        )

        model, _recall, _retrieve = self.run_script(state, [final_answer("current answer")])

        self.assertEqual(
            model.message_snapshots[0],
            [
                ("system", model.system_prompts[0]),
                ("human", "earlier question"),
                ("ai", "earlier answer"),
                ("human", "current question"),
            ],
        )
        self.assertNotIn("earlier question", model.system_prompts[0])
        self.assertNotIn("earlier answer", model.system_prompts[0])

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

    def test_tool_message_is_a_receipt_and_full_rag_evidence_stays_in_system_context(self) -> None:
        state = AgentRunState(user_id="user", knowledge_base_id="kb-1", input="查询差旅制度")
        evidence_text = "差旅申请必须提前审批。"

        model, _recall, _retrieve = self.run_script(
            state,
            [tool_call("rag", "差旅审批制度", "rag-1"), final_answer("需要提前审批[1]。")],
            rag_results=[evidence("retrieval-1", [chunk("chunk-1", evidence_text)])],
        )

        tool_messages = [
            content
            for message_type, content in model.message_snapshots[1]
            if message_type == "tool"
        ]
        self.assertEqual(len(tool_messages), 1)
        self.assertNotIn(evidence_text, tool_messages[0])
        self.assertNotIn('"results"', tool_messages[0])
        self.assertIn('"retrieval_log_id": "retrieval-1"', tool_messages[0])
        self.assertIn(evidence_text, model.system_prompts[1])
        self.assertIn(evidence_text, state.tool_observations[0]["results"][0]["content"])

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
