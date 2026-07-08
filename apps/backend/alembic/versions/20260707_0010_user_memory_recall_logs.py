"""add user memory recall logs

Revision ID: 20260707_0010
Revises: 20260707_0009
Create Date: 2026-07-07
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260707_0010"
down_revision = "20260707_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_memory_recall_logs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), sa.ForeignKey("conversations.id", ondelete="SET NULL")),
        sa.Column("message_id", sa.String(length=36), sa.ForeignKey("messages.id", ondelete="SET NULL")),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("recall_mode", sa.String(length=40), nullable=False),
        sa.Column("requested_limit", sa.Integer(), nullable=False),
        sa.Column("recall_limit", sa.Integer(), nullable=False),
        sa.Column("active_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("selected_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("threshold", sa.Float()),
        sa.Column("candidates", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("selected_memory_ids", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_user_memory_recall_logs_user_id", "user_memory_recall_logs", ["user_id"])
    op.create_index("ix_user_memory_recall_logs_conversation_id", "user_memory_recall_logs", ["conversation_id"])
    op.create_index("ix_user_memory_recall_logs_message_id", "user_memory_recall_logs", ["message_id"])
    op.create_index("ix_user_memory_recall_logs_recall_mode", "user_memory_recall_logs", ["recall_mode"])


def downgrade() -> None:
    op.drop_index("ix_user_memory_recall_logs_recall_mode", table_name="user_memory_recall_logs")
    op.drop_index("ix_user_memory_recall_logs_message_id", table_name="user_memory_recall_logs")
    op.drop_index("ix_user_memory_recall_logs_conversation_id", table_name="user_memory_recall_logs")
    op.drop_index("ix_user_memory_recall_logs_user_id", table_name="user_memory_recall_logs")
    op.drop_table("user_memory_recall_logs")
