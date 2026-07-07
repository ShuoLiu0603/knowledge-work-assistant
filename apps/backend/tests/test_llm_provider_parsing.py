from __future__ import annotations

import unittest

from app.llm.provider import parse_memory_candidates, parse_memory_operations


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

    def test_parse_memory_operations_accepts_embedded_json_array(self) -> None:
        operations = parse_memory_operations(
            'Result: [{"action": "create", "content": "User prefers concise answers", '
            '"confidence": 0.9, "importance": "high", "evidence": "I prefer concise answers"}]'
        )

        self.assertEqual(len(operations), 1)
        self.assertEqual(operations[0].action, "create")
        self.assertEqual(operations[0].importance, "high")
        self.assertEqual(operations[0].confidence, 0.9)


if __name__ == "__main__":
    unittest.main()
