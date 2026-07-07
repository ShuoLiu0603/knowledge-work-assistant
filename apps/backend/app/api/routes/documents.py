from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.document import DocumentChunkRead, DocumentRead, DocumentUploadResponse
from app.services.document_service import (
    create_uploaded_document,
    delete_document,
    get_document_detail,
    list_document_chunks,
    list_documents,
)

router = APIRouter()


@router.post(
    "/knowledge-bases/{kb_id}/documents",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    kb_id: str,
    file: Annotated[UploadFile, File()],
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    security_level: Annotated[int, Form()] = 1,
) -> DocumentUploadResponse:
    file_bytes = await file.read()
    return create_uploaded_document(
        db=db,
        user_id=current_user.id,
        kb_id=kb_id,
        file_name=file.filename or "upload.txt",
        content_type=file.content_type,
        file_bytes=file_bytes,
        security_level=security_level,
    )


@router.get("/knowledge-bases/{kb_id}/documents", response_model=list[DocumentRead])
def list_kb_documents(
    kb_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[DocumentRead]:
    return list_documents(db, current_user.id, kb_id)


@router.get("/documents/{document_id}", response_model=DocumentRead)
def get_item(
    document_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> DocumentRead:
    return get_document_detail(db, current_user.id, document_id)


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item(
    document_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    delete_document(db, current_user.id, document_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/documents/{document_id}/chunks", response_model=list[DocumentChunkRead])
def list_chunks(
    document_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[DocumentChunkRead]:
    return list_document_chunks(db, current_user.id, document_id)
