from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models.document import Document, DocumentChunk
from app.db.models.knowledge_base import KnowledgeBase
from app.db.pgvector import cosine_distance, cosine_similarity, supports_pgvector
from app.rag.embeddings import get_embedding_provider


@dataclass(frozen=True)
class VectorSearchHit:
    chunk_id: str
    score: float
    payload: dict[str, Any]


def upsert_document_chunks(db: Session, document: Document, chunks: list[DocumentChunk]) -> None:
    if not chunks:
        return

    provider = get_embedding_provider()
    vectors = provider.embed_texts([embedding_text(document, chunk) for chunk in chunks])
    settings = get_settings()
    for chunk, vector in zip(chunks, vectors, strict=True):
        if len(vector) != provider.dimension:
            raise RuntimeError(f"Embedding dimension mismatch: expected {provider.dimension}, got {len(vector)}")
        chunk.embedding = vector
        chunk.embedding_model = settings.embedding_model
        chunk.embedding_dimension = len(vector)
        db.add(chunk)


def search_knowledge_base_chunks(
    db: Session,
    owner_id: str,
    kb_id: str,
    query: str,
    limit: int,
    max_security_level: int,
) -> list[VectorSearchHit]:
    if limit <= 0 or not query.strip():
        return []

    settings = get_settings()
    query_vector = get_embedding_provider().embed_text(query)
    if not query_vector:
        return []
    if supports_pgvector(db):
        return search_pgvector_chunks(
            db,
            owner_id,
            kb_id,
            query_vector,
            settings.embedding_model,
            limit,
            max_security_level,
        )
    return search_local_chunks(
        db,
        owner_id,
        kb_id,
        query_vector,
        settings.embedding_model,
        limit,
        max_security_level,
    )


def search_pgvector_chunks(
    db: Session,
    owner_id: str,
    kb_id: str,
    query_vector: list[float],
    embedding_model: str,
    limit: int,
    max_security_level: int,
) -> list[VectorSearchHit]:
    distance = cosine_distance(DocumentChunk.embedding, query_vector)
    rows = db.execute(
        scoped_dense_query(owner_id, kb_id, len(query_vector), embedding_model, max_security_level)
        .add_columns((1.0 - distance).label("score"))
        .order_by(distance.asc(), DocumentChunk.id.asc())
        .limit(limit)
    ).all()
    return [
        VectorSearchHit(
            chunk_id=chunk.id,
            score=float(score or 0),
            payload=chunk_payload(document, chunk),
        )
        for chunk, document, score in rows
    ]


def search_local_chunks(
    db: Session,
    owner_id: str,
    kb_id: str,
    query_vector: list[float],
    embedding_model: str,
    limit: int,
    max_security_level: int,
) -> list[VectorSearchHit]:
    scored = [
        (cosine_similarity(query_vector, chunk.embedding), chunk, document)
        for chunk, document in db.execute(
            scoped_dense_query(owner_id, kb_id, len(query_vector), embedding_model, max_security_level)
        ).all()
    ]
    ranked = sorted(
        (item for item in scored if item[0] is not None),
        key=lambda item: (-float(item[0]), item[1].id),
    )[:limit]
    return [
        VectorSearchHit(
            chunk_id=chunk.id,
            score=float(score),
            payload=chunk_payload(document, chunk),
        )
        for score, chunk, document in ranked
    ]


def scoped_dense_query(
    owner_id: str,
    kb_id: str,
    embedding_dimension: int,
    embedding_model: str,
    max_security_level: int,
):
    return (
        select(DocumentChunk, Document)
        .join(Document, Document.id == DocumentChunk.document_id)
        .join(KnowledgeBase, KnowledgeBase.id == DocumentChunk.knowledge_base_id)
        .where(
            KnowledgeBase.owner_id == owner_id,
            DocumentChunk.knowledge_base_id == kb_id,
            DocumentChunk.security_level <= max_security_level,
            Document.status == "indexed",
            DocumentChunk.embedding.is_not(None),
            DocumentChunk.embedding_dimension == embedding_dimension,
            DocumentChunk.embedding_model == embedding_model,
        )
    )


def chunk_payload(document: Document, chunk: DocumentChunk) -> dict[str, Any]:
    return {
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
