"""add user memory events

Revision ID: 20260707_0009
Revises: 20260707_0008
Create Date: 2026-07-07
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260707_0009"
down_revision = "20260707_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_memory_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("memory_id", sa.String(length=36), sa.ForeignKey("user_memories.id", ondelete="SET NULL")),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("actor_type", sa.String(length=30), nullable=False, server_default="system"),
        sa.Column("actor_user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("source", sa.String(length=60), nullable=False, server_default="memory_service"),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("previous_status", sa.String(length=30)),
        sa.Column("new_status", sa.String(length=30)),
        sa.Column("payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_user_memory_events_user_id", "user_memory_events", ["user_id"])
    op.create_index("ix_user_memory_events_memory_id", "user_memory_events", ["memory_id"])
    op.create_index("ix_user_memory_events_event_type", "user_memory_events", ["event_type"])
    op.create_index("ix_user_memory_events_actor_user_id", "user_memory_events", ["actor_user_id"])


def downgrade() -> None:
    op.drop_index("ix_user_memory_events_actor_user_id", table_name="user_memory_events")
    op.drop_index("ix_user_memory_events_event_type", table_name="user_memory_events")
    op.drop_index("ix_user_memory_events_memory_id", table_name="user_memory_events")
    op.drop_index("ix_user_memory_events_user_id", table_name="user_memory_events")
    op.drop_table("user_memory_events")
