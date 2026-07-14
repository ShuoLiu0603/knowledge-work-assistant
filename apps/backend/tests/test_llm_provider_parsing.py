from __future__ import annotations

import unittest
from unittest.mock import patch

from pydantic import ValidationError

from langchain_core.messages import HumanMessage, ToolMessage

from app.llm.provider import (
    LlmMessage,
    OpenAICompatibleChatModel,
    OpenAICompatibleProvider,
    parse_memory_operations,
)
from app.llm.structured_outputs import MemoryClassificationOutput, MemoryOperationOutput
from app.schemas.memory import UserMemoryCreate, UserMemoryUpdate


class LlmProviderParsingTests(unittest.TestCase):
    def test_parse_memory_operations_accepts_embedded_json_array(self) -> None:
        operations = parse_memory_operations(
            'Result: [{"action": "create", "content": "User prefers concise answers", '
            '"importance": "high", "evidence": "I prefer concise answers"}]'
        )

        self.assertEqual(len(operations), 1)
        self.assertEqual(operations[0].action, "create")
        self.assertEqual(operations[0].importance, "high")

    def test_parse_memory_operations_accepts_structured_object(self) -> None:
        operations = parse_memory_operations(
            '{"operations": [{"action": "pending", "content": "User may prefer charts"}]}'
        )

        self.assertEqual(len(operations), 1)
        self.assertEqual(operations[0].action, "pending")
        self.assertEqual(operations[0].content, "User may prefer charts")

    def test_parse_memory_operations_accepts_single_operation_object(self) -> None:
        operations = parse_memory_operations(
            '{"action": "save", "content": "User prefers concise answers"}'
        )

        self.assertEqual(len(operations), 1)
        self.assertEqual(operations[0].action, "create")
        self.assertEqual(operations[0].content, "User prefers concise answers")

    def test_parse_memory_operations_preserves_unknown_action_for_editor(self) -> None:
        operations = parse_memory_operations(
            '{"action": "archive", "content": "User prefers concise answers"}'
        )

        self.assertEqual(len(operations), 1)
        self.assertEqual(operations[0].action, "archive")

    def test_legacy_project_kind_is_normalized_in_llm_output(self) -> None:
        output = MemoryOperationOutput(kind="project")

        self.assertEqual(output.kind, "profile")

    def test_manual_memory_schemas_reject_user_supplied_classification(self) -> None:
        for schema, payload in (
            (UserMemoryCreate, {"content": "User works on a RAG project", "kind": "profile"}),
            (UserMemoryCreate, {"content": "User prefers concise answers", "category": "response_detail"}),
            (UserMemoryUpdate, {"content": "User works on a RAG project", "kind": "profile"}),
            (UserMemoryUpdate, {"content": "User prefers concise answers", "category": "response_detail"}),
        ):
            with self.subTest(schema=schema.__name__, payload=payload), self.assertRaises(ValidationError):
                schema(**payload)

    def test_memory_classification_normalizes_unknown_values(self) -> None:
        output = MemoryClassificationOutput(kind="unknown", category="organization")

        self.assertEqual(output.kind, "preference")
        self.assertEqual(output.category, "general")

    def test_openai_provider_uses_langchain_structured_output(self) -> None:
        chat = FakeStructuredChat({"action": "create", "content": "User prefers concise answers"})

        with patch("app.llm.provider.create_chat_model", return_value=chat):
            output, completion = OpenAICompatibleProvider().complete_structured_with_metadata(
                [LlmMessage("user", "classify this memory operation")],
                MemoryOperationOutput,
                temperature=0,
            )

        self.assertIs(chat.schema, MemoryOperationOutput)
        self.assertEqual(output.action, "create")
        self.assertIn("concise", completion.content)

    def test_memory_classifier_uses_structured_output(self) -> None:
        chat = FakeStructuredChat({"kind": "profile", "category": "current_project"})

        with patch("app.llm.provider.create_chat_model", return_value=chat):
            classification = OpenAICompatibleProvider().classify_memory_with_metadata(
                "User works on an Agentic RAG project"
            )

        self.assertIs(chat.schema, MemoryClassificationOutput)
        self.assertEqual(classification.kind, "profile")
        self.assertEqual(classification.category, "current_project")

    def test_tool_follow_up_preserves_provider_reasoning_content(self) -> None:
        model = OpenAICompatibleChatModel(model="fake-model", api_key="test-key")
        result = model._create_chat_result(
            {
                "model": "fake-model",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "reasoning_content": "private reasoning state",
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {"name": "memory", "arguments": '{"query":"project"}'},
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
            }
        )
        assistant_message = result.generations[0].message

        payload = model._get_request_payload(
            [
                HumanMessage(content="What is my project?"),
                assistant_message,
                ToolMessage(content="Agentic RAG", tool_call_id="call-1"),
            ]
        )

        self.assertEqual(assistant_message.additional_kwargs["reasoning_content"], "private reasoning state")
        self.assertEqual(payload["messages"][1]["reasoning_content"], "private reasoning state")


class FakeStructuredChat:
    def __init__(self, response: dict) -> None:
        self.response = response
        self.schema = None

    def with_structured_output(self, schema):
        self.schema = schema
        return self

    def invoke(self, _messages):
        return self.response


if __name__ == "__main__":
    unittest.main()
