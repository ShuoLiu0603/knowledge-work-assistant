from pydantic import BaseModel, Field

from app.schemas.retrieval_log import RetrievalLogRead


class AskKnowledgeBaseRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    top_k: int | None = Field(default=None, ge=1, le=10)
    search_scope: str = "single"
    department_id: str | None = None


class CitationRead(BaseModel):
    chunk_id: str
    document_id: str
    knowledge_base_id: str = ""
    file_name: str
    chunk_index: int
    score: float
    content_preview: str
    title_path: str | None
    page_number: int | None
    section_name: str | None
    security_level: int = 1
    rrf_score: float | None = None
    retrieval_routes: list[str] = Field(default_factory=list)


class AskKnowledgeBaseResponse(BaseModel):
    question: str
    answer: str
    citations: list[CitationRead]
    retrieval_log: RetrievalLogRead | None = None
