from datetime import datetime

from pydantic import BaseModel, Field


class UserMemoryCreate(BaseModel):
    content: str = Field(min_length=1, max_length=2000)
    category: str | None = Field(default=None, max_length=80)
    kind: str = Field(default="preference", max_length=40)


class UserMemoryUpdate(BaseModel):
    content: str | None = Field(default=None, min_length=1, max_length=2000)
    status: str | None = Field(default=None, max_length=30)
    category: str | None = Field(default=None, max_length=80)
    kind: str | None = Field(default=None, max_length=40)


class UserMemoryRead(BaseModel):
    id: str
    user_id: str
    content: str
    content_hash: str
    status: str
    kind: str
    category: str
    source_text: str
    merge_count: int
    touched_count: int
    superseded_by_id: str | None
    metadata: dict
    created_at: datetime
    updated_at: datetime
    last_touched_at: datetime
