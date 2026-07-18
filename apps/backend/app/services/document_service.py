from __future__ import annotations

import hashlib
import re
from pathlib import Path

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security_levels import DEFAULT_SECURITY_LEVEL, validate_security_level
from app.db.models.document import Document, DocumentChunk
from app.db.models.knowledge_base import KnowledgeBase
from app.db.models.user import User
from app.schemas.document import DocumentChunkRead, DocumentRead, DocumentUploadResponse
from app.services.knowledge_base_service import ensure_kb_access, require_user
from app.services.audit_service import record_audit_event
from app.services.cleanup_service import create_external_cleanup_job, run_external_cleanup_job, to_cleanup_metadata
from app.storage.minio_client import remove_object, upload_bytes
from app.workers.document_tasks import process_document

ALLOWED_EXTENSIONS = {"pdf", "docx", "txt", "md", "csv"}


def create_uploaded_document(
    db: Session,
    user_id: str,
    kb_id: str,
    file_name: str,
    content_type: str | None,
    file_bytes: bytes,
    security_level: int = 1,
) -> DocumentUploadResponse:
    current_user = require_user(db, user_id)
    knowledge_base = require_document_manager(
        db,
        current_user,
        action="document.upload",
        resource_type="knowledge_base",
        resource_id=kb_id,
        detail="Insufficient permission to upload documents",
    )
    requested_security_level = document_upload_security_level(knowledge_base, security_level)
    if (
        knowledge_base.visibility != "private"
        and not current_user.is_admin
        and requested_security_level > current_user.security_level
    ):
        deny_document_action(
            db,
            current_user,
            action="document.upload",
            resource_type="knowledge_base",
            resource_id=kb_id,
            security_level=requested_security_level,
            detail="Cannot upload a document above your security level",
        )
    settings = get_settings()

    safe_name = sanitize_file_name(file_name)
    file_ext = Path(safe_name).suffix.lower().lstrip(".")
    if file_ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported file type",
        )

    max_size = settings.max_upload_size_mb * 1024 * 1024
    if len(file_bytes) > max_size:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds {settings.max_upload_size_mb} MB",
        )

    content_hash = hashlib.sha256(file_bytes).hexdigest()
    existing_document = db.scalar(
        select(Document).where(
            Document.knowledge_base_id == kb_id,
            Document.content_hash == content_hash,
            Document.status != "failed",
        )
    )
    if existing_document is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Document with the same content already exists in this knowledge base",
        )

    document = Document(
        knowledge_base_id=knowledge_base.id,
        uploader_id=user_id,
        file_name=safe_name,
        file_ext=file_ext,
        mime_type=content_type,
        file_size=len(file_bytes),
        object_key="pending",
        content_hash=content_hash,
        status="uploaded",
        security_level=requested_security_level,
    )
    db.add(document)
    db.flush()

    object_key = f"knowledge-bases/{kb_id}/documents/{document.id}/{safe_name}"
    document.object_key = object_key
    upload_bytes(object_key, file_bytes, content_type)

    db.add(document)
    db.commit()
    try:
        job = process_document.delay(document.id)
    except Exception as exc:
        mark_document_failed(db, document.id, f"Document indexing job enqueue failed: {exc}")
        try:
            remove_object(object_key)
        except Exception as cleanup_exc:
            record_audit_event(
                db,
                actor_user_id=user_id,
                action="document.upload_cleanup",
                resource_type="document",
                resource_id=document.id,
                outcome="failed",
                security_level=document.security_level,
                detail=str(cleanup_exc),
                metadata={"object_key": object_key},
            )
        record_audit_event(
            db,
            actor_user_id=user_id,
            action="document.upload",
            resource_type="document",
            resource_id=document.id,
            outcome="failed",
            security_level=document.security_level,
            detail=str(exc),
            metadata={
                "knowledge_base_id": kb_id,
                "file_name": document.file_name,
                "file_size": document.file_size,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Document indexing queue is not available",
        ) from exc
    record_audit_event(
        db,
        actor_user_id=user_id,
        action="document.upload",
        resource_type="document",
        resource_id=document.id,
        security_level=document.security_level,
        metadata={
            "knowledge_base_id": kb_id,
            "file_name": document.file_name,
            "file_size": document.file_size,
            "job_id": job.id,
        },
    )
    return DocumentUploadResponse(
        document_id=document.id,
        status=document.status,
        job_id=job.id,
        security_level=document.security_level,
    )


def list_documents(db: Session, user_id: str, kb_id: str) -> list[DocumentRead]:
    current_user = require_user(db, user_id)
    knowledge_base, _role = ensure_kb_access(db, user_id, kb_id, required_role="viewer")
    query = (
        select(Document)
        .where(Document.knowledge_base_id == kb_id)
        .order_by(Document.created_at.desc())
    )
    if knowledge_base.visibility != "private" and not current_user.is_admin:
        query = query.where(Document.security_level <= current_user.security_level)
    documents = db.scalars(query).all()
    return [to_document_read(document) for document in documents]


def get_document(db: Session, user_id: str, document_id: str, required_role: str = "viewer") -> Document:
    current_user = require_user(db, user_id)
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    knowledge_base, _role = ensure_kb_access(db, user_id, document.knowledge_base_id, required_role=required_role)
    if (
        knowledge_base.visibility != "private"
        and document.security_level > current_user.security_level
        and not current_user.is_admin
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return document


def get_document_detail(db: Session, user_id: str, document_id: str) -> DocumentRead:
    return to_document_read(get_document(db, user_id, document_id))


def delete_document(db: Session, user_id: str, document_id: str) -> None:
    current_user = require_user(db, user_id)
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    require_document_manager(
        db,
        current_user,
        action="document.delete",
        resource_type="document",
        resource_id=document_id,
        security_level=document.security_level,
        detail="Insufficient permission to delete documents",
        kb_id=document.knowledge_base_id,
    )
    object_key = document.object_key
    knowledge_base_id = document.knowledge_base_id
    file_name = document.file_name
    security_level = document.security_level

    cleanup_job = create_external_cleanup_job(
        db,
        actor_user_id=user_id,
        resource_type="document",
        resource_id=document_id,
        object_keys=[object_key],
        metadata={
            "knowledge_base_id": knowledge_base_id,
            "file_name": file_name,
            "security_level": security_level,
        },
    )
    db.delete(document)
    db.commit()
    cleanup_job = run_external_cleanup_job(db, cleanup_job.id)
    record_audit_event(
        db,
        actor_user_id=user_id,
        action="document.delete",
        resource_type="document",
        resource_id=document_id,
        security_level=security_level,
        metadata={
            "knowledge_base_id": knowledge_base_id,
            "file_name": file_name,
            **to_cleanup_metadata(cleanup_job),
        },
    )


def mark_document_failed(db: Session, document_id: str, error_message: str) -> None:
    document = db.get(Document, document_id)
    if document is None:
        return
    document.status = "failed"
    document.error_message = error_message
    document.chunk_count = 0
    db.add(document)
    db.commit()


def list_document_chunks(db: Session, user_id: str, document_id: str) -> list[DocumentChunkRead]:
    document = get_document(db, user_id, document_id)
    chunks = db.scalars(
        select(DocumentChunk)
        .where(DocumentChunk.document_id == document.id)
        .order_by(DocumentChunk.chunk_index)
    ).all()
    return [to_chunk_read(chunk) for chunk in chunks]


def sanitize_file_name(file_name: str) -> str:
    raw_name = Path(file_name or "upload.txt").name
    safe_name = re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+", "_", raw_name).strip("._")
    safe_name = re.sub(r"_+(\.[A-Za-z0-9]+)$", r"\1", safe_name)
    return safe_name or "upload.txt"


def require_document_manager(
    db: Session,
    user: User,
    *,
    action: str,
    resource_type: str,
    resource_id: str,
    detail: str,
    security_level: int | None = None,
    kb_id: str | None = None,
) -> KnowledgeBase:
    target_kb_id = kb_id or resource_id
    knowledge_base, _role = ensure_kb_access(db, user.id, target_kb_id, required_role="viewer")
    if knowledge_base.visibility == "public" and not user.is_admin:
        deny_document_action(
            db,
            user,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            security_level=security_level,
            detail="Only admins can manage public knowledge base documents",
        )
    if knowledge_base.visibility != "public":
        ensure_kb_access(db, user.id, target_kb_id, required_role="editor")
    return knowledge_base


def document_upload_security_level(knowledge_base: KnowledgeBase, requested_level: int) -> int:
    if knowledge_base.visibility == "private":
        return DEFAULT_SECURITY_LEVEL
    try:
        return validate_security_level(requested_level)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


def deny_document_action(
    db: Session,
    user: User,
    *,
    action: str,
    resource_type: str,
    resource_id: str,
    detail: str,
    security_level: int | None = None,
) -> None:
    record_audit_event(
        db,
        actor_user_id=user.id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        outcome="denied",
        security_level=security_level,
        detail=detail,
    )
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def to_document_read(document: Document) -> DocumentRead:
    return DocumentRead(
        id=document.id,
        knowledge_base_id=document.knowledge_base_id,
        uploader_id=document.uploader_id,
        file_name=document.file_name,
        file_ext=document.file_ext,
        mime_type=document.mime_type,
        file_size=document.file_size,
        status=document.status,
        error_message=document.error_message,
        chunk_count=document.chunk_count,
        security_level=document.security_level,
        content_hash=document.content_hash,
        created_at=document.created_at,
        updated_at=document.updated_at,
    )


def to_chunk_read(chunk: DocumentChunk) -> DocumentChunkRead:
    return DocumentChunkRead(
        id=chunk.id,
        document_id=chunk.document_id,
        knowledge_base_id=chunk.knowledge_base_id,
        chunk_index=chunk.chunk_index,
        content=chunk.content,
        token_count=chunk.token_count,
        title_path=chunk.title_path,
        page_number=chunk.page_number,
        section_name=chunk.section_name,
        security_level=chunk.security_level,
        metadata=chunk.extra_metadata,
        created_at=chunk.created_at,
    )
