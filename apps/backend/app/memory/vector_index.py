from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.config import get_settings
from app.db.models.user_memory import UserMemory


@dataclass(frozen=True)
class MemoryVectorHit:
    memory_id: str
    score: float
    payload: dict[str, Any]


def get_qdrant_client(timeout: int | None = None) -> Any:
    from qdrant_client import QdrantClient

    settings = get_settings()
    return QdrantClient(url=settings.qdrant_url, timeout=timeout or settings.qdrant_timeout_seconds)


def memory_collection_name() -> str:
    return get_settings().memory_qdrant_collection


def is_memory_vector_index_enabled() -> bool:
    return bool(get_settings().memory_vector_index_enabled)


def ensure_memory_collection(client: Any | None = None) -> None:
    if not is_memory_vector_index_enabled():
        return
    models = qdrant_models()
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


def ensure_memory_payload_indexes(client: Any | None = None) -> None:
    if not is_memory_vector_index_enabled():
        return
    models = qdrant_models()
    client = client or get_qdrant_client()
    field_schemas = {
        "user_id": models.PayloadSchemaType.KEYWORD,
        "status": models.PayloadSchemaType.KEYWORD,
        "kind": models.PayloadSchemaType.KEYWORD,
        "category": models.PayloadSchemaType.KEYWORD,
        "canonical_key": models.PayloadSchemaType.KEYWORD,
        "memory_layer": models.PayloadSchemaType.KEYWORD,
        "profile_slot": models.PayloadSchemaType.KEYWORD,
        "scope_type": models.PayloadSchemaType.KEYWORD,
        "scope_id": models.PayloadSchemaType.KEYWORD,
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
    if not should_index_memory(memory):
        delete_memory_vector(memory.id)
        return
    client = get_qdrant_client()
    ensure_memory_collection(client)
    models = qdrant_models()
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


def try_sync_memory_vector(memory: UserMemory) -> bool:
    try:
        sync_memory_vector(memory)
    except Exception:
        return False
    return True


def delete_memory_vector(memory_id: str) -> None:
    if not is_memory_vector_index_enabled():
        return
    models = qdrant_models()
    client = get_qdrant_client()
    collection_name = memory_collection_name()
    if not client.collection_exists(collection_name):
        return
    client.delete(
        collection_name=collection_name,
        points_selector=models.PointIdsList(points=[memory_id]),
        wait=True,
    )


def try_delete_memory_vector(memory_id: str) -> bool:
    try:
        delete_memory_vector(memory_id)
    except Exception:
        return False
    return True


def should_index_memory(memory: UserMemory) -> bool:
    return memory.status == "active" and bool(memory.embedding)


def get_memory_vector_payloads(memory_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not is_memory_vector_index_enabled() or not memory_ids:
        return {}
    client = get_qdrant_client()
    collection_name = memory_collection_name()
    if not client.collection_exists(collection_name):
        return {}
    points = client.retrieve(
        collection_name=collection_name,
        ids=list(dict.fromkeys(memory_ids)),
        with_payload=True,
        with_vectors=False,
    )
    return {str(point.id): dict(point.payload or {}) for point in points}


def search_active_memories(
    user_id: str,
    query_vector: list[float],
    limit: int,
) -> list[MemoryVectorHit]:
    if not is_memory_vector_index_enabled():
        return []
    client = get_qdrant_client()
    ensure_memory_collection(client)
    models = qdrant_models()
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
        "canonical_key": memory.canonical_key,
        "memory_layer": memory.memory_layer,
        "profile_slot": memory.profile_slot,
        "scope_type": memory.scope_type,
        "scope_id": memory.scope_id,
        "pinned": memory.pinned,
        "revision": memory.revision,
        "expires_at": memory.expires_at.isoformat() if memory.expires_at else None,
        "content_hash": memory.content_hash,
        "source_conversation_id": memory.source_conversation_id,
        "source_message_id": memory.source_message_id,
        "embedding_model": memory.embedding_model,
        "embedding_dimension": memory.embedding_dimension,
    }


def qdrant_models() -> Any:
    from qdrant_client import models

    return models


def field_match(field_name: str, value: str) -> Any:
    models = qdrant_models()
    return models.FieldCondition(
        key=field_name,
        match=models.MatchValue(value=value),
    )
