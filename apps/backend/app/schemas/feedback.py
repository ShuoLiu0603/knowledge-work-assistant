from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class FeedbackCreate(BaseModel):
    message_id: str
    rating: int = Field(ge=-1, le=1)
    reason: str | None = Field(default=None, max_length=1000)

    @field_validator("rating")
    @classmethod
    def validate_rating(cls, value: int) -> int:
        if value not in (-1, 1):
            raise ValueError("rating must be 1 or -1")
        return value


class FeedbackRead(BaseModel):
    id: str
    user_id: str
    message_id: str
    rating: int
    reason: str | None
    created_at: datetime
