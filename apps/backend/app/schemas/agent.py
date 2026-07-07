from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.qa import CitationRead


class AgentRunRequest(BaseModel):
    knowledge_base_id: str | None = None
    input: str = Field(min_length=1, max_length=2000)
    top_k: int | None = Field(default=None, ge=1, le=10)
    search_scope: str = "single"
    department_id: str | None = None


class AgentTraceStep(BaseModel):
    node: str
    action: str
    input: dict
    output: dict


class AgentRunRead(BaseModel):
    id: str
    user_id: str
    knowledge_base_id: str | None
    conversation_id: str | None
    message_id: str | None
    retrieval_log_id: str | None
    input: str
    intent: str
    status: str
    answer: str
    citations: list[CitationRead]
    trace: list[AgentTraceStep]
    state: dict
    error_message: str | None
    created_at: datetime
    updated_at: datetime
