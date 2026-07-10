from __future__ import annotations

import unittest
from unittest.mock import patch

from app.rag.query_rewrite import (
    QueryRewriteOutput,
    fallback_query_plan,
    parse_query_rewrite,
    rewrite_query,
    structured_rewrite_with_langchain,
)


class QueryRewriteTests(unittest.TestCase):
    def test_pydantic_output_cleans_and_limits_sub_questions(self) -> None:
        output = QueryRewriteOutput.model_validate(
            {
                "rewritten_query": "  RAG answer flow   ",
                "sub_questions": [
                    " retrieval flow ",
                    "",
                    " memory flow ",
                    " citations ",
                    " ignored extra ",
                ],
            }
        )

        self.assertEqual(output.rewritten_query, "RAG answer flow")
        self.assertEqual(output.sub_questions, ["retrieval flow", "memory flow", "citations"])

    def test_pydantic_output_accepts_query_alias(self) -> None:
        output = QueryRewriteOutput.model_validate({"query": " RAG answer flow "})

        self.assertEqual(output.rewritten_query, "RAG answer flow")

    def test_pydantic_output_accepts_string_sub_questions(self) -> None:
        output = QueryRewriteOutput.model_validate(
            {
                "rewritten_query": "RAG answer flow",
                "sub_questions": "retrieval flow; memory flow\ncitations",
            }
        )

        self.assertEqual(output.sub_questions, ["retrieval flow", "memory flow", "citations"])

    def test_rewrite_query_uses_langchain_structured_output_first(self) -> None:
        output = QueryRewriteOutput(
            rewritten_query="RAG retrieval plan",
            sub_questions=["dense retrieval", "BM25 retrieval"],
        )

        with patch("app.rag.query_rewrite.structured_rewrite_with_langchain", return_value=output):
            plan = rewrite_query("How does retrieval work?")

        self.assertEqual(plan.rewritten_query, "RAG retrieval plan")
        self.assertEqual(plan.sub_questions, ["dense retrieval", "BM25 retrieval"])

    def test_structured_rewrite_uses_langchain_pydantic_schema(self) -> None:
        chat = FakeStructuredChat(
            {
                "rewritten_query": "RAG retrieval plan",
                "sub_questions": ["dense retrieval"],
            }
        )

        with patch("app.rag.query_rewrite.create_chat_model", return_value=chat):
            output = structured_rewrite_with_langchain("How does retrieval work?")

        self.assertIs(chat.schema, QueryRewriteOutput)
        self.assertEqual(output.rewritten_query, "RAG retrieval plan")
        self.assertEqual(output.sub_questions, ["dense retrieval"])

    def test_rewrite_query_falls_back_to_provider_json(self) -> None:
        provider = FakeProvider(
            '{"rewritten_query": "fallback query", "sub_questions": ["first sub query"]}'
        )

        with (
            patch("app.rag.query_rewrite.structured_rewrite_with_langchain", side_effect=RuntimeError("unsupported")),
            patch("app.rag.query_rewrite.get_llm_provider", return_value=provider),
        ):
            plan = rewrite_query("Please explain fallback?")

        self.assertEqual(plan.rewritten_query, "fallback query")
        self.assertEqual(plan.sub_questions, ["first sub query"])

    def test_rewrite_query_falls_back_to_original_query(self) -> None:
        with (
            patch("app.rag.query_rewrite.structured_rewrite_with_langchain", side_effect=RuntimeError("unsupported")),
            patch("app.rag.query_rewrite.get_llm_provider", side_effect=RuntimeError("no api key")),
        ):
            plan = rewrite_query("Please explain RAG?")

        self.assertEqual(plan, fallback_query_plan("Please explain RAG?"))

    def test_parse_query_rewrite_accepts_embedded_json(self) -> None:
        plan = parse_query_rewrite(
            'Result: {"rewritten_query": "travel policy", "sub_questions": ["hotel limit"]}',
            "original question",
        )

        self.assertEqual(plan.rewritten_query, "travel policy")
        self.assertEqual(plan.sub_questions, ["hotel limit"])


class FakeProvider:
    def __init__(self, response: str) -> None:
        self.response = response

    def complete(self, _messages, temperature: float = 0.1) -> str:
        return self.response


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
