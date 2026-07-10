from __future__ import annotations

import unittest
from unittest.mock import patch

from app.llm.provider import LlmMessage, OpenAICompatibleProvider, parse_memory_operations
from app.llm.structured_outputs import IntentOutput


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

    def test_openai_provider_uses_langchain_structured_output(self) -> None:
        chat = FakeStructuredChat({"intent": "summary"})

        with patch("app.llm.provider.create_chat_model", return_value=chat):
            output, completion = OpenAICompatibleProvider().complete_structured_with_metadata(
                [LlmMessage("user", "summarize this")],
                IntentOutput,
                temperature=0,
            )

        self.assertIs(chat.schema, IntentOutput)
        self.assertEqual(output.intent, "summary")
        self.assertIn("summary", completion.content)


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
