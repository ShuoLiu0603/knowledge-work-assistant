from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from qdrant_client import QdrantClient, models

from app.core.config import get_settings
from app.db.models.document import Document, DocumentChunk
from app.rag.embeddings import get_embedding_provider


@dataclass(frozen=True)
class VectorSearchHit:
    point_id: str
    score: float
    payload: dict[str, Any]


def get_qdrant_client(timeout: int = 10) -> QdrantClient:
    return QdrantClient(url=get_settings().qdrant_url, timeout=timeout)


def ensure_qdrant_collection(client: QdrantClient | None = None) -> None:
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


def ensure_payload_indexes(client: QdrantClient | None = None) -> None:
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


def delete_vectors_by_filter(points_filter: models.Filter) -> None:
    client = get_qdrant_client()
    collection_name = get_settings().qdrant_collection
    if not client.collection_exists(collection_name):
        return
    client.delete(
        collection_name=collection_name,
        points_selector=points_filter,
        wait=True,
    )


def search_filter(owner_id: str, kb_id: str, max_security_level: int) -> models.Filter:
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


def match_filter(field_name: str, value: str) -> models.Filter:
    return models.Filter(must=[field_match(field_name, value)])


def field_match(field_name: str, value: str) -> models.FieldCondition:
    return models.FieldCondition(
        key=field_name,
        match=models.MatchValue(value=value),
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
