from __future__ import annotations

import unittest
from unittest.mock import patch

from app.llm.provider import LlmMessage, OpenAICompatibleProvider, parse_memory_candidates, parse_memory_operations
from app.llm.structured_outputs import IntentOutput


class LlmProviderParsingTests(unittest.TestCase):
    def test_parse_memory_candidates_ignores_invalid_json_array(self) -> None:
        self.assertEqual(parse_memory_candidates("prefix [not valid json] suffix"), [])

    def test_parse_memory_candidates_accepts_embedded_json_array(self) -> None:
        candidates = parse_memory_candidates(
            'Here you go: [{"content": "User prefers concise answers", "confidence": 0.9}]'
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].content, "User prefers concise answers")
        self.assertEqual(candidates[0].confidence, 0.9)

    def test_parse_memory_candidates_accepts_structured_object(self) -> None:
        candidates = parse_memory_candidates(
            '{"candidates": [{"content": "User prefers tables", "category": "format", "confidence": 0.8}]}'
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].content, "User prefers tables")
        self.assertEqual(candidates[0].category, "format")

    def test_parse_memory_operations_accepts_embedded_json_array(self) -> None:
        operations = parse_memory_operations(
            'Result: [{"action": "create", "content": "User prefers concise answers", '
            '"confidence": 0.9, "importance": "high", "evidence": "I prefer concise answers"}]'
        )

        self.assertEqual(len(operations), 1)
        self.assertEqual(operations[0].action, "create")
        self.assertEqual(operations[0].importance, "high")
        self.assertEqual(operations[0].confidence, 0.9)

    def test_parse_memory_operations_accepts_structured_object(self) -> None:
        operations = parse_memory_operations(
            '{"operations": [{"action": "pending", "content": "User may prefer charts", "confidence": 0.7}]}'
        )

        self.assertEqual(len(operations), 1)
        self.assertEqual(operations[0].action, "pending")
        self.assertEqual(operations[0].content, "User may prefer charts")

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
