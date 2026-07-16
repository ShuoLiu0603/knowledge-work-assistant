from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.llm.context_compression import compress_memory_context, compress_rag_evidence
from app.llm.provider import LlmCompletion
from app.llm.structured_outputs import (
    CompressedMemoryItemOutput,
    ExtractedEvidenceOutput,
    MemoryContextCompressionOutput,
    RagEvidenceCompressionOutput,
)
from app.rag.retrieval import RetrievedChunk


class FakeCompressionProvider:
    def __init__(self, outputs: list[object]) -> None:
        self.outputs = list(outputs)
        self.messages = []

    def complete_structured_with_metadata(self, messages, schema, temperature=None):
        self.messages.append(messages)
        output = self.outputs.pop(0)
        return output, fake_completion()


class ContextCompressionTests(unittest.TestCase):
    def test_memory_prompt_contains_target_and_preserves_protected_source(self) -> None:
        provider = FakeCompressionProvider(
            [
                MemoryContextCompressionOutput(
                    items=[
                        CompressedMemoryItemOutput(
                            content="User prefers Chinese answers.",
                            source_ids=["profile-1"],
                            section="profile",
                        )
                    ]
                )
            ]
        )
        settings = compression_settings(target_ratio=0.5)

        with patch("app.llm.context_compression.get_settings", return_value=settings):
            result = compress_memory_context(
                "How should you answer?",
                [
                    {
                        "id": "profile-1",
                        "section": "profile",
                        "content": "User prefers Chinese answers.",
                        "protected": True,
                    }
                ],
                max_tokens=200,
                provider=provider,
            )

        self.assertIsNotNone(result.content)
        self.assertIn("profile-1", result.content or "")
        self.assertIn("Target token budget: 100", provider.messages[0][1].content)
        self.assertFalse(result.fallback_used)

    def test_memory_compression_retries_when_first_output_exceeds_limit(self) -> None:
        provider = FakeCompressionProvider(
            [
                MemoryContextCompressionOutput(
                    items=[
                        CompressedMemoryItemOutput(
                            content="very long memory " * 100,
                            source_ids=["memory-1"],
                            section="long_term",
                        )
                    ]
                ),
                MemoryContextCompressionOutput(
                    items=[
                        CompressedMemoryItemOutput(
                            content="Short retained fact.",
                            source_ids=["memory-1"],
                            section="long_term",
                        )
                    ]
                ),
            ]
        )
        settings = compression_settings(retry_limit=1)

        with patch("app.llm.context_compression.get_settings", return_value=settings):
            result = compress_memory_context(
                "What matters?",
                [{"id": "memory-1", "section": "long_term", "content": "Source fact.", "protected": False}],
                max_tokens=100,
                provider=provider,
            )

        self.assertEqual(result.retry_count, 1)
        self.assertEqual(len(result.completions), 2)
        self.assertIn("Short retained fact.", result.content or "")

    def test_memory_compression_can_omit_recent_conversation_section(self) -> None:
        provider = FakeCompressionProvider(
            [
                MemoryContextCompressionOutput(
                    items=[
                        CompressedMemoryItemOutput(
                            content="User prefers Chinese answers.",
                            source_ids=["profile-1"],
                            section="profile",
                        )
                    ]
                )
            ]
        )

        with patch("app.llm.context_compression.get_settings", return_value=compression_settings()):
            result = compress_memory_context(
                "How should you answer?",
                [
                    {
                        "id": "profile-1",
                        "section": "profile",
                        "content": "User prefers Chinese answers.",
                        "protected": True,
                    }
                ],
                max_tokens=200,
                provider=provider,
            )

        self.assertNotIn("Recent conversation:", result.content or "")

    def test_rag_compression_accepts_only_verbatim_quotes(self) -> None:
        chunk = make_chunk("The policy applies only to full-time employees.")
        valid_provider = FakeCompressionProvider(
            [
                RagEvidenceCompressionOutput(
                    evidence=[
                        ExtractedEvidenceOutput(
                            chunk_id=chunk.chunk_id,
                            quotes=["The policy applies only to full-time employees."],
                        )
                    ]
                )
            ]
        )
        invalid_provider = FakeCompressionProvider(
            [
                RagEvidenceCompressionOutput(
                    evidence=[
                        ExtractedEvidenceOutput(
                            chunk_id=chunk.chunk_id,
                            quotes=["The policy applies to every employee."],
                        )
                    ]
                )
            ]
        )

        with patch("app.llm.context_compression.get_settings", return_value=compression_settings()):
            valid = compress_rag_evidence("Who is covered?", [chunk], 200, provider=valid_provider)
            invalid = compress_rag_evidence("Who is covered?", [chunk], 200, provider=invalid_provider)

        self.assertEqual(valid.chunks[0].content, chunk.content)
        self.assertFalse(valid.fallback_used)
        self.assertIsNone(invalid.chunks)
        self.assertTrue(invalid.fallback_used)


def compression_settings(target_ratio: float = 0.9, retry_limit: int = 0):
    return SimpleNamespace(
        context_compression_target_ratio=target_ratio,
        context_compression_retry_limit=retry_limit,
        llm_context_compression_temperature=0.0,
        llm_model="fake-model",
    )


def fake_completion() -> LlmCompletion:
    return LlmCompletion(
        content="{}",
        provider="fake",
        model_name="fake-model",
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
        latency_ms=1,
        status="success",
    )


def make_chunk(content: str) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id="chunk-1",
        document_id="document-1",
        knowledge_base_id="kb-1",
        chunk_index=0,
        content=content,
        score=0.9,
        file_name="policy.md",
        title_path=None,
        page_number=None,
        section_name=None,
        metadata={},
    )
