from datetime import datetime

from pydantic import BaseModel


class DocumentRead(BaseModel):
    id: str
    knowledge_base_id: str
    uploader_id: str | None
    file_name: str
    file_ext: str
    mime_type: str | None
    file_size: int
    status: str
    error_message: str | None
    chunk_count: int
    security_level: int
    content_hash: str
    created_at: datetime
    updated_at: datetime


class DocumentUploadResponse(BaseModel):
    document_id: str
    status: str
    job_id: str
    security_level: int


class DocumentChunkRead(BaseModel):
    id: str
    document_id: str
    knowledge_base_id: str
    chunk_index: int
    content: str
    token_count: int
    title_path: str | None
    page_number: int | None
    section_name: str | None
    security_level: int
    metadata: dict
    created_at: datetime
