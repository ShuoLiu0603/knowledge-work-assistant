from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class DepartmentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    admin_user_id: str = Field(min_length=1, max_length=36)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("name cannot be empty")
        return normalized


class DepartmentAdminUpdate(BaseModel):
    admin_user_id: str = Field(min_length=1, max_length=36)


class DepartmentRead(BaseModel):
    id: str
    name: str
    description: str | None
    admin_user_id: str | None
    admin_username: str | None
    created_at: datetime
    updated_at: datetime
