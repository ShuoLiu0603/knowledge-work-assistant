from datetime import datetime

from pydantic import BaseModel


class RetrievalLogRead(BaseModel):
    id: str
    user_id: str
    knowledge_base_id: str | None
    scope_type: str
    searched_knowledge_base_ids: list
    conversation_id: str | None
    message_id: str | None
    question: str
    rewritten_query: str
    sub_questions: list
    expanded_queries: list
    retrieval_routes: list
    candidates: list
    selected_chunks: list
    rrf_k: int
    reranker_enabled: bool
    compression_chars_saved: int
    created_at: datetime
