from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, false, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RetrievalLog(Base):
    __tablename__ = "retrieval_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    knowledge_base_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("knowledge_bases.id", ondelete="SET NULL"),
        index=True,
    )
    conversation_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("conversations.id", ondelete="SET NULL"),
        index=True,
    )
    message_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("messages.id", ondelete="SET NULL"),
        index=True,
    )
    scope_type: Mapped[str] = mapped_column(String(30), nullable=False, default="single")
    searched_knowledge_base_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    rewritten_query: Mapped[str] = mapped_column(Text, nullable=False)
    sub_questions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    expanded_queries: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    retrieval_routes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    candidates: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    selected_chunks: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    rrf_k: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    reranker_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    compression_chars_saved: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
