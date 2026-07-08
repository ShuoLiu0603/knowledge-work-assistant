from __future__ import annotations

import unittest
from unittest.mock import patch

from app.rag.embeddings import OpenAICompatibleEmbeddingProvider


class EmbeddingProviderTests(unittest.TestCase):
    def test_openai_compatible_provider_uses_langchain_embeddings(self) -> None:
        calls: list[dict] = []

        class FakeOpenAIEmbeddings:
            def __init__(self, **kwargs) -> None:
                calls.append({"init": kwargs})

            def embed_documents(self, texts: list[str]) -> list[list[float]]:
                calls.append({"texts": texts})
                return [[float(len(text)), 0.0, 1.0] for text in texts]

        provider = OpenAICompatibleEmbeddingProvider(
            name="openai_compatible",
            dimension=3,
            base_url="https://example.test/v1",
            api_key="test-key",
            model="embedding-model",
            batch_size=2,
        )

        with patch("langchain_openai.OpenAIEmbeddings", FakeOpenAIEmbeddings):
            vectors = provider.embed_texts(["alpha", "beta", "gamma"])

        self.assertEqual(vectors, [[5.0, 0.0, 1.0], [4.0, 0.0, 1.0], [5.0, 0.0, 1.0]])
        self.assertEqual(calls[0]["init"]["model"], "embedding-model")
        self.assertEqual(calls[0]["init"]["base_url"], "https://example.test/v1")
        self.assertFalse(calls[0]["init"]["tiktoken_enabled"])
        self.assertFalse(calls[0]["init"]["check_embedding_ctx_length"])
        self.assertEqual(calls[1]["texts"], ["alpha", "beta"])
        self.assertEqual(calls[2]["texts"], ["gamma"])


if __name__ == "__main__":
    unittest.main()
