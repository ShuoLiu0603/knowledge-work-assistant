from __future__ import annotations

from app.memory.types import MemoryEmbedding
from app.rag.embeddings import get_embedding_provider


def embed_memory_text(text: str) -> MemoryEmbedding:
    provider = get_embedding_provider()
    vector = provider.embed_text(text)
    model = str(getattr(provider, "model", "") or provider.name)
    return MemoryEmbedding(vector=vector, model=model, dimension=provider.dimension)
