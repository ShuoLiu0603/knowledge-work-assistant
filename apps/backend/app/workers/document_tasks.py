from __future__ import annotations

from sqlalchemy import delete

from app.core.config import get_settings
from app.db.models.document import Document, DocumentChunk
from app.db.session import SessionLocal, init_db
from app.rag.loaders import parse_document
from app.rag.splitters import split_blocks
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
            blocks = parse_document(file_bytes, document.file_name, document.file_ext)
            if not blocks:
                raise ValueError("No readable text was extracted from the document")

            document.status = "chunking"
            db.add(document)
            db.commit()

            chunks = split_blocks(
                blocks,
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
                chunk_row = DocumentChunk(
                    document_id=document.id,
                    knowledge_base_id=document.knowledge_base_id,
                    chunk_index=index,
                    content=chunk.content,
                    token_count=chunk.token_count,
                    title_path=chunk.title_path,
                    page_number=chunk.page_number,
                    section_name=chunk.section_name,
                    security_level=document.security_level,
                    extra_metadata=chunk.metadata,
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
