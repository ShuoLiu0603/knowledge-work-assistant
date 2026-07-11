from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.qa import CitationRead
from app.schemas.text_limits import validate_question_token_limit


class ConversationCreate(BaseModel):
    knowledge_base_id: str | None = None
    search_scope: str = "single"
    department_id: str | None = None
    title: str | None = Field(default=None, max_length=160)


class ConversationRead(BaseModel):
    id: str
    knowledge_base_id: str | None
    knowledge_base_name: str | None
    search_scope: str
    search_department_id: str | None
    target_label: str
    title: str
    summary: str | None
    created_at: datetime
    updated_at: datetime


class MessageRead(BaseModel):
    id: str
    conversation_id: str
    role: str
    content: str
    status: str
    memory_enabled: bool
    citations: list[CitationRead]
    agent_trace: list
    token_usage: dict
    error_message: str | None
    created_at: datetime


class StreamMessageRequest(BaseModel):
    question: str = Field(min_length=1, max_length=16000)
    top_k: int | None = Field(default=None, ge=1, le=10)
    memory_mode: Literal["auto", "normal", "off"] = "auto"

    @field_validator("question", mode="before")
    @classmethod
    def validate_question(cls, value: object) -> str:
        return validate_question_token_limit(value)
