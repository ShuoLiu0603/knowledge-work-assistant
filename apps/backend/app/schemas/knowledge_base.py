from datetime import datetime

from pydantic import BaseModel, Field, field_validator

KB_VISIBILITIES = {"private", "department", "public"}


class KnowledgeBaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    visibility: str = "private"
    department_id: str | None = None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("name cannot be empty")
        return normalized

    @field_validator("visibility")
    @classmethod
    def validate_visibility(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in KB_VISIBILITIES:
            raise ValueError("visibility must be private, department, or public")
        return normalized


class KnowledgeBaseUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    visibility: str | None = None
    department_id: str | None = None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        if value is None:
            return value
        normalized = value.strip()
        if not normalized:
            raise ValueError("name cannot be empty")
        return normalized

    @field_validator("visibility")
    @classmethod
    def validate_visibility(cls, value: str | None) -> str | None:
        if value is None:
            return value
        normalized = value.strip().lower()
        if normalized not in KB_VISIBILITIES:
            raise ValueError("visibility must be private, department, or public")
        return normalized


class KnowledgeBaseRead(BaseModel):
    id: str
    owner_id: str
    department_id: str | None
    department_name: str | None
    name: str
    description: str | None
    visibility: str
    role: str
    created_at: datetime
    updated_at: datetime
