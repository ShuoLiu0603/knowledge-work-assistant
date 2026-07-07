from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class UserMemory(Base):
    __tablename__ = "user_memories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(30), index=True, nullable=False, default="active")
    kind: Mapped[str] = mapped_column(String(40), nullable=False, default="preference")
    category: Mapped[str] = mapped_column(String(80), index=True, nullable=False, default="general")
    source_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    embedding: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    merge_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    touched_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    superseded_by_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("user_memories.id", ondelete="SET NULL"))
    extra_metadata: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    last_touched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
