from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.config import get_settings
from app.db.models.document import Document, DocumentChunk
from app.rag.embeddings import get_embedding_provider


@dataclass(frozen=True)
class VectorSearchHit:
    point_id: str
    score: float
    payload: dict[str, Any]


def get_qdrant_client(timeout: int | None = None) -> Any:
    from qdrant_client import QdrantClient

    settings = get_settings()
    return QdrantClient(url=settings.qdrant_url, timeout=timeout or settings.qdrant_timeout_seconds)


def ensure_qdrant_collection(client: Any | None = None) -> None:
    models = qdrant_models()
    client = client or get_qdrant_client()
    settings = get_settings()
    collection_name = settings.qdrant_collection

    if not client.collection_exists(collection_name):
        client.create_collection(
            collection_name=collection_name,
            vectors_config=models.VectorParams(
                size=settings.embedding_dimension,
                distance=models.Distance.COSINE,
            ),
        )

    ensure_payload_indexes(client)


def ensure_payload_indexes(client: Any | None = None) -> None:
    models = qdrant_models()
    client = client or get_qdrant_client()
    collection_name = get_settings().qdrant_collection
    field_schemas = {
        "user_id": models.PayloadSchemaType.KEYWORD,
        "knowledge_base_id": models.PayloadSchemaType.KEYWORD,
        "document_id": models.PayloadSchemaType.KEYWORD,
        "file_name": models.PayloadSchemaType.KEYWORD,
        "security_level": models.PayloadSchemaType.INTEGER,
    }
    for field_name, field_schema in field_schemas.items():
        try:
            client.create_payload_index(
                collection_name=collection_name,
                field_name=field_name,
                field_schema=field_schema,
                wait=True,
            )
        except Exception:
            continue


def upsert_document_chunks(document: Document, chunks: list[DocumentChunk]) -> None:
    if not chunks:
        return

    client = get_qdrant_client()
    ensure_qdrant_collection(client)
    provider = get_embedding_provider()
    vectors = provider.embed_texts([embedding_text(document, chunk) for chunk in chunks])
    models = qdrant_models()
    points = [
        models.PointStruct(
            id=chunk.qdrant_point_id,
            vector=vector,
            payload=chunk_payload(document, chunk),
        )
        for chunk, vector in zip(chunks, vectors, strict=True)
    ]
    client.upsert(
        collection_name=get_settings().qdrant_collection,
        points=points,
        wait=True,
    )


def search_knowledge_base_chunks(
    owner_id: str,
    kb_id: str,
    query: str,
    limit: int,
    max_security_level: int,
) -> list[VectorSearchHit]:
    client = get_qdrant_client()
    ensure_qdrant_collection(client)
    provider = get_embedding_provider()
    models = qdrant_models()
    result = client.query_points(
        collection_name=get_settings().qdrant_collection,
        query=provider.embed_text(query),
        query_filter=search_filter(owner_id, kb_id, max_security_level),
        limit=limit,
        with_payload=True,
        with_vectors=False,
    )
    return [
        VectorSearchHit(
            point_id=str(point.id),
            score=float(point.score or 0),
            payload=dict(point.payload or {}),
        )
        for point in result.points
    ]


def delete_document_vectors(document_id: str) -> None:
    delete_vectors_by_filter(match_filter("document_id", document_id))


def delete_knowledge_base_vectors(kb_id: str) -> None:
    delete_vectors_by_filter(match_filter("knowledge_base_id", kb_id))


def delete_vectors_by_filter(points_filter: Any) -> None:
    client = get_qdrant_client()
    collection_name = get_settings().qdrant_collection
    if not client.collection_exists(collection_name):
        return
    client.delete(
        collection_name=collection_name,
        points_selector=points_filter,
        wait=True,
    )


def search_filter(owner_id: str, kb_id: str, max_security_level: int) -> Any:
    models = qdrant_models()
    return models.Filter(
        must=[
            field_match("user_id", owner_id),
            field_match("knowledge_base_id", kb_id),
        ],
        should=[
            models.FieldCondition(
                key="security_level",
                range=models.Range(lte=max_security_level),
            ),
            models.IsEmptyCondition(is_empty=models.PayloadField(key="security_level")),
        ],
    )


def match_filter(field_name: str, value: str) -> Any:
    models = qdrant_models()
    return models.Filter(must=[field_match(field_name, value)])


def field_match(field_name: str, value: str) -> Any:
    models = qdrant_models()
    return models.FieldCondition(
        key=field_name,
        match=models.MatchValue(value=value),
    )


def qdrant_models() -> Any:
    try:
        from qdrant_client import models
    except ModuleNotFoundError:
        return fallback_qdrant_models()

    return models


class FallbackQdrantModel:
    def __init__(self, **kwargs: Any) -> None:
        self._values = kwargs

    def model_dump(self, exclude_none: bool = False) -> dict[str, Any]:
        return {
            key: dump_fallback_value(value, exclude_none=exclude_none)
            for key, value in self._values.items()
            if not (exclude_none and value is None)
        }


class FallbackRange(FallbackQdrantModel):
    def __init__(self, lte: int | float | None = None) -> None:
        super().__init__(lte=float(lte) if lte is not None else None)


def dump_fallback_value(value: Any, *, exclude_none: bool) -> Any:
    if isinstance(value, FallbackQdrantModel):
        return value.model_dump(exclude_none=exclude_none)
    if isinstance(value, list):
        return [dump_fallback_value(item, exclude_none=exclude_none) for item in value]
    return value


def fallback_qdrant_models() -> Any:
    class PayloadSchemaType:
        KEYWORD = "keyword"
        INTEGER = "integer"

    class Distance:
        COSINE = "Cosine"

    return type(
        "FallbackQdrantModels",
        (),
        {
            "Distance": Distance,
            "PayloadSchemaType": PayloadSchemaType,
            "VectorParams": FallbackQdrantModel,
            "PointStruct": FallbackQdrantModel,
            "Filter": FallbackQdrantModel,
            "FieldCondition": FallbackQdrantModel,
            "MatchValue": FallbackQdrantModel,
            "Range": FallbackRange,
            "IsEmptyCondition": FallbackQdrantModel,
            "PayloadField": FallbackQdrantModel,
        },
    )


def chunk_payload(document: Document, chunk: DocumentChunk) -> dict[str, Any]:
    return {
        "user_id": document.knowledge_base.owner_id,
        "knowledge_base_id": chunk.knowledge_base_id,
        "document_id": chunk.document_id,
        "chunk_id": chunk.id,
        "chunk_index": chunk.chunk_index,
        "content": chunk.content,
        "file_name": document.file_name,
        "file_ext": document.file_ext,
        "security_level": chunk.security_level,
        "title_path": chunk.title_path,
        "page_number": chunk.page_number,
        "section_name": chunk.section_name,
        "metadata": chunk.extra_metadata,
    }


def embedding_text(document: Document, chunk: DocumentChunk) -> str:
    metadata_lines = [
        ("file_name", document.file_name),
        ("title_path", chunk.title_path),
        ("section_name", chunk.section_name),
    ]
    lines = [f"{key}: {value}" for key, value in metadata_lines if value]
    lines.append(f"content: {chunk.content}")
    return "\n".join(lines)
