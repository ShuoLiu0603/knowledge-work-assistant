from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.config import get_settings

TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")


@dataclass(frozen=True)
class EmbeddingProvider:
    name: str
    dimension: int

    def embed_text(self, text: str) -> list[float]:
        raise NotImplementedError

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_text(text) for text in texts]


class OpenAICompatibleEmbeddingProvider(EmbeddingProvider):
    base_url: str | None
    api_key: str
    model: str
    batch_size: int

    def __init__(
        self,
        name: str,
        dimension: int,
        base_url: str | None,
        api_key: str,
        model: str,
        batch_size: int,
    ) -> None:
        super().__init__(name=name, dimension=dimension)
        object.__setattr__(self, "base_url", base_url)
        object.__setattr__(self, "api_key", api_key)
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "batch_size", batch_size)

    def embed_text(self, text: str) -> list[float]:
        return self.embed_texts([text])[0]

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if any(not isinstance(text, str) for text in texts):
            raise TypeError("Embedding input must be a list of strings")

        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("openai is required for embedding calls.") from exc

        client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=30,
        )

        vectors: list[list[float]] = []
        try:
            for batch in batched(texts, self.batch_size):
                response = client.embeddings.create(
                    model=self.model,
                    input=batch,
                    dimensions=self.dimension,
                    encoding_format="float",
                )
                rows = sorted(response.data, key=lambda item: item.index)
                vectors.extend([list(map(float, row.embedding)) for row in rows])
        except Exception as exc:
            raise RuntimeError(f"Embedding provider request failed: {exc}") from exc

        if len(vectors) != len(texts):
            raise RuntimeError("Embedding provider returned an unexpected vector count")
        for vector in vectors:
            if len(vector) != self.dimension:
                raise RuntimeError(
                    f"Embedding dimension mismatch: expected {self.dimension}, got {len(vector)}"
                )
        return vectors


def get_embedding_provider() -> EmbeddingProvider:
    settings = get_settings()
    if settings.embedding_provider != "openai_compatible":
        raise ValueError("Unsupported EMBEDDING_PROVIDER. Only openai_compatible is supported.")
    if not settings.embedding_api_key.strip():
        raise ValueError("EMBEDDING_API_KEY is required when EMBEDDING_PROVIDER=openai_compatible.")
    return OpenAICompatibleEmbeddingProvider(
        name=settings.embedding_provider,
        dimension=settings.embedding_dimension,
        base_url=settings.embedding_base_url.strip() or None,
        api_key=settings.embedding_api_key,
        model=settings.embedding_model,
        batch_size=settings.embedding_batch_size,
    )


def batched(values: list[str], batch_size: int) -> list[list[str]]:
    size = max(1, batch_size)
    return [values[index : index + size] for index in range(0, len(values), size)]


def tokenize(text: str) -> list[str]:
    normalized = text.lower()
    tokens = TOKEN_RE.findall(normalized)
    return tokens + char_ngrams(normalized, size=2)


def char_ngrams(text: str, size: int) -> list[str]:
    compact = "".join(ch for ch in text if not ch.isspace())
    if len(compact) < size:
        return []
    return [compact[index : index + size] for index in range(len(compact) - size + 1)]
