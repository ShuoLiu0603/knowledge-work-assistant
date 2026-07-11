from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import ValidationError

from app.schemas.agent import AgentRunRequest
from app.schemas.conversation import StreamMessageRequest
from app.schemas.qa import AskKnowledgeBaseRequest


class QuestionTokenLimitTests(unittest.TestCase):
    def test_question_is_preserved_when_within_token_limit(self) -> None:
        with (
            patch("app.schemas.text_limits.get_settings", return_value=SimpleNamespace(question_max_tokens=10)),
            patch("app.schemas.text_limits.count_tokens", return_value=10),
        ):
            request = AskKnowledgeBaseRequest(question="  complete question  ")

        self.assertEqual(request.question, "complete question")

    def test_all_question_entry_points_reject_token_overflow(self) -> None:
        with (
            patch("app.schemas.text_limits.get_settings", return_value=SimpleNamespace(question_max_tokens=10)),
            patch("app.schemas.text_limits.count_tokens", return_value=11),
        ):
            for schema, field_name in (
                (AskKnowledgeBaseRequest, "question"),
                (StreamMessageRequest, "question"),
                (AgentRunRequest, "input"),
            ):
                with self.subTest(schema=schema.__name__), self.assertRaises(ValidationError):
                    schema(**{field_name: "oversized question"})


if __name__ == "__main__":
    unittest.main()
