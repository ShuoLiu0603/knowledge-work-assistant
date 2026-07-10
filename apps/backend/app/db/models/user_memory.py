from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class UserMemory(Base):
    __tablename__ = "user_memories"
    __table_args__ = (
        Index(
            "uq_user_memories_active_profile_singleton",
            "user_id",
            "scope_type",
            "scope_id",
            "profile_slot",
            unique=True,
            sqlite_where=text(
                "status = 'active' AND memory_layer = 'profile' "
                "AND profile_slot IN ("
                "'response_detail', 'language', 'format', 'name', 'company', "
                "'team', 'current_role', 'current_project', 'current_stack', "
                "'backend_framework', 'frontend_framework'"
                ")"
            ),
            postgresql_where=text(
                "status = 'active' AND memory_layer = 'profile' "
                "AND profile_slot IN ("
                "'response_detail', 'language', 'format', 'name', 'company', "
                "'team', 'current_role', 'current_project', 'current_stack', "
                "'backend_framework', 'frontend_framework'"
                ")"
            ),
        ),
        Index(
            "uq_user_memories_active_canonical_key",
            "user_id",
            "scope_type",
            "scope_id",
            "canonical_key",
            unique=True,
            sqlite_where=text("status = 'active' AND canonical_key <> ''"),
            postgresql_where=text("status = 'active' AND canonical_key <> ''"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(30), index=True, nullable=False, default="active")
    kind: Mapped[str] = mapped_column(String(40), nullable=False, default="preference")
    category: Mapped[str] = mapped_column(String(80), index=True, nullable=False, default="general")
    canonical_key: Mapped[str] = mapped_column(String(160), index=True, nullable=False, default="")
    memory_layer: Mapped[str] = mapped_column(String(30), index=True, nullable=False, default="semantic")
    profile_slot: Mapped[str] = mapped_column(String(80), index=True, nullable=False, default="")
    scope_type: Mapped[str] = mapped_column(String(30), index=True, nullable=False, default="user")
    scope_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False, default="")
    pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    source_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_conversation_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("conversations.id", ondelete="SET NULL"),
        index=True,
    )
    source_message_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("messages.id", ondelete="SET NULL"),
        index=True,
    )
    embedding: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    embedding_model: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    embedding_dimension: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    merge_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    touched_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    superseded_by_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("user_memories.id", ondelete="SET NULL"))
    extra_metadata: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)
    valid_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    invalid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    last_touched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class UserMemoryEvent(Base):
    __tablename__ = "user_memory_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    memory_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("user_memories.id", ondelete="SET NULL"),
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(40), index=True, nullable=False)
    actor_type: Mapped[str] = mapped_column(String(30), nullable=False, default="system")
    actor_user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"), index=True)
    source: Mapped[str] = mapped_column(String(60), nullable=False, default="memory_service")
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    previous_status: Mapped[str | None] = mapped_column(String(30))
    new_status: Mapped[str | None] = mapped_column(String(30))
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
    )


class UserMemoryRecallLog(Base):
    __tablename__ = "user_memory_recall_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
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
    query: Mapped[str] = mapped_column(Text, nullable=False)
    recall_mode: Mapped[str] = mapped_column(String(40), index=True, nullable=False)
    requested_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    recall_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    active_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    selected_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    threshold: Mapped[float | None] = mapped_column(Float)
    candidates: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    selected_memory_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
    )


class UserMemoryUpdateJob(Base):
    __tablename__ = "user_memory_update_jobs"
    __table_args__ = (
        Index(
            "ix_user_memory_update_jobs_status_lease_expires_at",
            "status",
            "lease_expires_at",
        ),
        Index(
            "ix_user_memory_update_jobs_status_dispatched_at",
            "status",
            "dispatched_at",
        ),
        Index(
            "uq_user_memory_update_jobs_user_message_id",
            "user_id",
            "message_id",
            unique=True,
            sqlite_where=text("message_id IS NOT NULL"),
            postgresql_where=text("message_id IS NOT NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
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
    user_message: Mapped[str] = mapped_column(Text, nullable=False)
    assistant_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(30), index=True, nullable=False, default="queued")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    actions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    lease_token: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
