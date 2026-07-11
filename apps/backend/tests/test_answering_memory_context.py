from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.llm.provider import LlmCompletion
from app.rag.answering import generate_grounded_answer
from app.rag.retrieval import RetrievedChunk


class FakeAnswerProvider:
    provider_name = "openai_compatible"

    def __init__(self) -> None:
        self.contexts: list[str] = []

    def answer_question_with_metadata(
        self,
        question: str,
        context: str,
        memory_context: str = "",
        on_token=None,
    ) -> LlmCompletion:
        self.contexts.append(context)
        if context.strip():
            content = "按你的偏好，我会保持简洁。\n\n酒店报销上限是 600 CNY。[1]"
        else:
            content = f"没有在当前可访问的知识库中检索到足够依据，无法基于知识库回答。问题：{question}"
        if on_token:
            on_token(content)
        return LlmCompletion(
            content=content,
            provider=self.provider_name,
            model_name="fake-chat",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
            latency_ms=1,
            status="success",
        )


class AnsweringMemoryContextTests(unittest.TestCase):
    def test_memory_context_reaches_grounded_answer(self) -> None:
        chunk = RetrievedChunk(
            chunk_id="chunk-1",
            document_id="doc-1",
            knowledge_base_id="kb-1",
            chunk_index=0,
            content="The hotel reimbursement limit is 600 CNY.",
            score=0.9,
            file_name="policy.md",
            title_path=None,
            page_number=None,
            section_name=None,
            metadata={},
        )
        provider = FakeAnswerProvider()

        with patch("app.rag.answering.get_llm_provider", return_value=provider):
            result = generate_grounded_answer(
                "What is the hotel reimbursement limit?",
                [chunk],
                memory_context="Long-term memory:\n- User prefers concise answers.",
            )

        self.assertIn("简洁", result.answer)
        self.assertIn("[1]", result.answer)
        self.assertIsNotNone(result.completion)
        self.assertEqual(result.completion.provider, "openai_compatible")
        self.assertIn("hotel reimbursement limit", provider.contexts[0])

    def test_no_context_answer_still_uses_llm_without_fabricating(self) -> None:
        provider = FakeAnswerProvider()

        with patch("app.rag.answering.get_llm_provider", return_value=provider):
            result = generate_grounded_answer(
                "采购合同归档期限是多少？",
                [],
                memory_context="用户偏好回答简洁。",
            )

        self.assertIn("当前可访问的知识库", result.answer)
        self.assertIn("检索到足够依据", result.answer)
        self.assertIsNotNone(result.completion)
        self.assertEqual(provider.contexts, [""])
        self.assertIn("采购合同归档期限是多少？", result.answer)

    def test_used_chunks_match_context_limit(self) -> None:
        chunks = [
            RetrievedChunk(
                chunk_id="chunk-1",
                document_id="doc-1",
                knowledge_base_id="kb-1",
                chunk_index=0,
                content="A" * 80,
                score=0.9,
                file_name="policy.md",
                title_path=None,
                page_number=None,
                section_name=None,
                metadata={},
            ),
            RetrievedChunk(
                chunk_id="chunk-2",
                document_id="doc-1",
                knowledge_base_id="kb-1",
                chunk_index=1,
                content="B" * 80,
                score=0.8,
                file_name="policy.md",
                title_path=None,
                page_number=None,
                section_name=None,
                metadata={},
            ),
        ]
        provider = FakeAnswerProvider()

        with (
            patch("app.rag.answering.get_llm_provider", return_value=provider),
            patch(
                "app.rag.answering.get_settings",
                return_value=SimpleNamespace(
                    rag_context_max_tokens=130,
                ),
            ),
            patch("app.rag.answering.count_tokens", side_effect=len),
        ):
            result = generate_grounded_answer("What is covered?", chunks)

        self.assertEqual([chunk.chunk_id for chunk in result.used_chunks], ["chunk-1"])
        self.assertEqual(result.used_chunks[0].content, "A" * 80)
        self.assertIn("chunk #0", provider.contexts[0])
        self.assertNotIn("chunk #1", provider.contexts[0])


if __name__ == "__main__":
    unittest.main()
