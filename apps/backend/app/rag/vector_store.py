from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib import error, parse, request

from app.core.config import get_settings
from app.db.models.document import Document, DocumentChunk
from app.rag.embeddings import get_embedding_provider


@dataclass(frozen=True)
class VectorSearchHit:
    point_id: str
    score: float
    payload: dict[str, Any]


def ensure_qdrant_collection() -> None:
    settings = get_settings()
    status, _body = qdrant_request("GET", f"/collections/{settings.qdrant_collection}", allow_404=True)
    if status == 200:
        ensure_payload_indexes()
        return

    payload = {
        "vectors": {
            "size": settings.embedding_dimension,
            "distance": "Cosine",
        }
    }
    qdrant_request("PUT", f"/collections/{settings.qdrant_collection}", payload)
    ensure_payload_indexes()


def ensure_payload_indexes() -> None:
    settings = get_settings()
    field_schemas = {
        "user_id": "keyword",
        "knowledge_base_id": "keyword",
        "document_id": "keyword",
        "file_name": "keyword",
        "security_level": "integer",
    }
    for field_name, field_schema in field_schemas.items():
        try:
            qdrant_request(
                "PUT",
                f"/collections/{settings.qdrant_collection}/index?wait=true",
                {"field_name": field_name, "field_schema": field_schema},
            )
        except RuntimeError:
            continue


def upsert_document_chunks(document: Document, chunks: list[DocumentChunk]) -> None:
    if not chunks:
        return

    ensure_qdrant_collection()
    provider = get_embedding_provider()
    vectors = provider.embed_texts([chunk.content for chunk in chunks])
    points = [
        {
            "id": chunk.qdrant_point_id,
            "vector": vector,
            "payload": chunk_payload(document, chunk),
        }
        for chunk, vector in zip(chunks, vectors, strict=True)
    ]
    settings = get_settings()
    qdrant_request(
        "PUT",
        f"/collections/{settings.qdrant_collection}/points?wait=true",
        {"points": points},
    )


def search_knowledge_base_chunks(
    owner_id: str,
    kb_id: str,
    query: str,
    limit: int,
    max_security_level: int,
) -> list[VectorSearchHit]:
    ensure_qdrant_collection()
    provider = get_embedding_provider()
    settings = get_settings()
    payload = {
        "vector": provider.embed_text(query),
        "filter": {
            "must": [
                {
                    "key": "user_id",
                    "match": {"value": owner_id},
                },
                {
                    "key": "knowledge_base_id",
                    "match": {"value": kb_id},
                }
            ],
            "should": [
                {
                    "key": "security_level",
                    "range": {"lte": max_security_level},
                },
                {
                    "is_empty": {"key": "security_level"},
                },
            ],
        },
        "limit": limit,
        "with_payload": True,
        "with_vector": False,
    }
    _status, body = qdrant_request(
        "POST",
        f"/collections/{settings.qdrant_collection}/points/search",
        payload,
    )
    return [
        VectorSearchHit(
            point_id=str(item["id"]),
            score=float(item.get("score", 0)),
            payload=item.get("payload") or {},
        )
        for item in body.get("result", [])
    ]


def delete_document_vectors(document_id: str) -> None:
    settings = get_settings()
    status, _body = qdrant_request("GET", f"/collections/{settings.qdrant_collection}", allow_404=True)
    if status == 404:
        return

    qdrant_request(
        "POST",
        f"/collections/{settings.qdrant_collection}/points/delete?wait=true",
        {
            "filter": {
                "must": [
                    {
                        "key": "document_id",
                        "match": {"value": document_id},
                    }
                ]
            }
        },
    )


def delete_knowledge_base_vectors(kb_id: str) -> None:
    settings = get_settings()
    status, _body = qdrant_request("GET", f"/collections/{settings.qdrant_collection}", allow_404=True)
    if status == 404:
        return

    qdrant_request(
        "POST",
        f"/collections/{settings.qdrant_collection}/points/delete?wait=true",
        {
            "filter": {
                "must": [
                    {
                        "key": "knowledge_base_id",
                        "match": {"value": kb_id},
                    }
                ]
            }
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


def qdrant_request(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    allow_404: bool = False,
    timeout: int = 10,
) -> tuple[int, dict[str, Any]]:
    settings = get_settings()
    url = f"{settings.qdrant_url.rstrip('/')}{path}"
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"} if payload is not None else {}
    req = request.Request(url, data=data, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=timeout) as response:
            response_body = response.read().decode("utf-8")
            return response.status, json.loads(response_body) if response_body else {}
    except error.HTTPError as exc:
        if allow_404 and exc.code == 404:
            return 404, {}
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Qdrant request failed: {method} {parse.urlsplit(url).path} {exc.code} {body}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Qdrant is not reachable: {exc.reason}") from exc
