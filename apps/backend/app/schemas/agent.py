from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.qa import CitationRead
from app.schemas.text_limits import validate_question_token_limit


class AgentRunRequest(BaseModel):
    knowledge_base_id: str | None = None
    input: str = Field(min_length=1, max_length=16000)
    top_k: int | None = Field(default=None, ge=1, le=10)
    search_scope: Literal["single", "department", "public", "accessible", "all"] = "single"
    department_id: str | None = None

    @field_validator("input", mode="before")
    @classmethod
    def validate_input(cls, value: object) -> str:
        return validate_question_token_limit(value)


class AgentTraceStep(BaseModel):
    node: str
    action: str = ""
    input: dict = Field(default_factory=dict)
    output: dict = Field(default_factory=dict)


class AgentRunRead(BaseModel):
    id: str
    user_id: str
    knowledge_base_id: str | None
    conversation_id: str | None
    message_id: str | None
    retrieval_log_id: str | None
    searched_knowledge_base_ids: list[str]
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
