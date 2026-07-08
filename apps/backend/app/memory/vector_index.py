from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from qdrant_client import QdrantClient, models

from app.core.config import get_settings
from app.db.models.user_memory import UserMemory


@dataclass(frozen=True)
class MemoryVectorHit:
    memory_id: str
    score: float
    payload: dict[str, Any]


def get_qdrant_client(timeout: int = 10) -> QdrantClient:
    return QdrantClient(url=get_settings().qdrant_url, timeout=timeout)


def memory_collection_name() -> str:
    return get_settings().memory_qdrant_collection


def is_memory_vector_index_enabled() -> bool:
    return bool(get_settings().memory_vector_index_enabled)


def ensure_memory_collection(client: QdrantClient | None = None) -> None:
    if not is_memory_vector_index_enabled():
        return
    client = client or get_qdrant_client()
    settings = get_settings()
    collection_name = memory_collection_name()

    if not client.collection_exists(collection_name):
        client.create_collection(
            collection_name=collection_name,
            vectors_config=models.VectorParams(
                size=settings.embedding_dimension,
                distance=models.Distance.COSINE,
            ),
        )

    ensure_memory_payload_indexes(client)


def ensure_memory_payload_indexes(client: QdrantClient | None = None) -> None:
    if not is_memory_vector_index_enabled():
        return
    client = client or get_qdrant_client()
    field_schemas = {
        "user_id": models.PayloadSchemaType.KEYWORD,
        "status": models.PayloadSchemaType.KEYWORD,
        "kind": models.PayloadSchemaType.KEYWORD,
        "category": models.PayloadSchemaType.KEYWORD,
    }
    for field_name, field_schema in field_schemas.items():
        try:
            client.create_payload_index(
                collection_name=memory_collection_name(),
                field_name=field_name,
                field_schema=field_schema,
                wait=True,
            )
        except Exception:
            continue


def sync_memory_vector(memory: UserMemory) -> None:
    if not is_memory_vector_index_enabled():
        return
    if memory.status == "deleted":
        delete_memory_vector(memory.id)
        return
    if not memory.embedding:
        return
    client = get_qdrant_client()
    ensure_memory_collection(client)
    client.upsert(
        collection_name=memory_collection_name(),
        points=[
            models.PointStruct(
                id=memory.id,
                vector=memory.embedding,
                payload=memory_payload(memory),
            )
        ],
        wait=True,
    )


def try_sync_memory_vector(memory: UserMemory) -> None:
    try:
        sync_memory_vector(memory)
    except Exception:
        return


def delete_memory_vector(memory_id: str) -> None:
    if not is_memory_vector_index_enabled():
        return
    client = get_qdrant_client()
    collection_name = memory_collection_name()
    if not client.collection_exists(collection_name):
        return
    client.delete(
        collection_name=collection_name,
        points_selector=models.PointIdsList(points=[memory_id]),
        wait=True,
    )


def try_delete_memory_vector(memory_id: str) -> None:
    try:
        delete_memory_vector(memory_id)
    except Exception:
        return


def search_active_memories(
    user_id: str,
    query_vector: list[float],
    limit: int,
    score_threshold: float | None = None,
) -> list[MemoryVectorHit]:
    if not is_memory_vector_index_enabled():
        return []
    client = get_qdrant_client()
    ensure_memory_collection(client)
    result = client.query_points(
        collection_name=memory_collection_name(),
        query=query_vector,
        query_filter=models.Filter(
            must=[
                field_match("user_id", user_id),
                field_match("status", "active"),
            ],
        ),
        limit=limit,
        with_payload=True,
        with_vectors=False,
        score_threshold=score_threshold,
    )
    return [
        MemoryVectorHit(
            memory_id=str(point.id),
            score=float(point.score or 0),
            payload=dict(point.payload or {}),
        )
        for point in result.points
    ]


def memory_payload(memory: UserMemory) -> dict[str, Any]:
    return {
        "user_id": memory.user_id,
        "memory_id": memory.id,
        "status": memory.status,
        "kind": memory.kind,
        "category": memory.category,
        "content_hash": memory.content_hash,
        "source_conversation_id": memory.source_conversation_id,
        "source_message_id": memory.source_message_id,
        "embedding_model": memory.embedding_model,
        "embedding_dimension": memory.embedding_dimension,
    }


def field_match(field_name: str, value: str) -> models.FieldCondition:
    return models.FieldCondition(
        key=field_name,
        match=models.MatchValue(value=value),
    )
