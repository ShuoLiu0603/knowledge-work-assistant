from __future__ import annotations

from sqlalchemy import delete

from app.core.config import get_settings
from app.db.models.document import Document, DocumentChunk
from app.db.session import SessionLocal, init_db
from app.rag.loaders import load_documents
from app.rag.splitters import split_documents
from app.rag.vector_store import delete_document_vectors, upsert_document_chunks
from app.storage.minio_client import download_bytes
from app.workers.celery_app import celery_app


@celery_app.task(name="process_document")
def process_document(document_id: str) -> dict[str, int | str]:
    init_db()
    settings = get_settings()

    with SessionLocal() as db:
        document = db.get(Document, document_id)
        if document is None:
            return {"document_id": document_id, "status": "missing", "chunk_count": 0}

        try:
            document.status = "parsing"
            document.error_message = None
            db.add(document)
            db.commit()

            file_bytes = download_bytes(document.object_key)
            loaded_documents = load_documents(file_bytes, document.file_name, document.file_ext)
            if not loaded_documents:
                raise ValueError("No readable text was extracted from the document")

            document.status = "chunking"
            db.add(document)
            db.commit()

            chunks = split_documents(
                loaded_documents,
                chunk_size=settings.default_chunk_size,
                chunk_overlap=settings.default_chunk_overlap,
            )
            if not chunks:
                raise ValueError("No chunks were generated from the document")

            document.status = "embedding"
            document.chunk_count = 0
            db.add(document)
            db.commit()

            db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document.id))
            delete_document_vectors(document.id)
            chunk_rows: list[DocumentChunk] = []
            for index, chunk in enumerate(chunks):
                metadata = chunk.metadata
                chunk_row = DocumentChunk(
                    document_id=document.id,
                    knowledge_base_id=document.knowledge_base_id,
                    chunk_index=index,
                    content=chunk.page_content,
                    token_count=int(metadata.get("token_count") or 0),
                    title_path=metadata_text(metadata, "title_path"),
                    page_number=metadata_int(metadata, "page_number"),
                    section_name=metadata_text(metadata, "section_name"),
                    security_level=document.security_level,
                    extra_metadata=metadata,
                )
                db.add(chunk_row)
                chunk_rows.append(chunk_row)

            db.flush()
            upsert_document_chunks(document, chunk_rows)
            document.status = "indexed"
            document.chunk_count = len(chunks)
            document.error_message = None
            db.add(document)
            db.commit()
            return {"document_id": document.id, "status": document.status, "chunk_count": len(chunks)}
        except Exception as exc:
            db.rollback()
            document = db.get(Document, document_id)
            if document is None:
                return {"document_id": document_id, "status": "missing", "chunk_count": 0}
            document.status = "failed"
            document.error_message = str(exc)
            document.chunk_count = 0
            db.add(document)
            db.commit()
            try:
                delete_document_vectors(document.id)
            except Exception:
                pass
            return {"document_id": document.id, "status": document.status, "chunk_count": 0}


def metadata_text(metadata: dict, key: str) -> str | None:
    value = metadata.get(key)
    return value if isinstance(value, str) else None


def metadata_int(metadata: dict, key: str) -> int | None:
    value = metadata.get(key)
    return value if isinstance(value, int) else None
