"""persist per-message memory mode

Revision ID: 20260710_0019
Revises: 20260710_0018
Create Date: 2026-07-10
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260710_0019"
down_revision = "20260710_0018"
branch_labels = None
depends_on = None

NO_MEMORY_MARKERS = (
    "不要使用记忆",
    "别使用记忆",
    "不要读取记忆",
    "别读取记忆",
    "不用记忆",
    "临时模式",
    "临时对话",
    "no memory",
    "without memory",
    "temporary chat",
    "temporary mode",
    "do not use memory",
    "don't use memory",
    "dont use memory",
)


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column("memory_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    bind = op.get_bind()
    messages = sa.table(
        "messages",
        sa.column("id", sa.String()),
        sa.column("conversation_id", sa.String()),
        sa.column("role", sa.String()),
        sa.column("content", sa.Text()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("memory_enabled", sa.Boolean()),
    )
    rows = bind.execute(
        sa.select(
            messages.c.id,
            messages.c.conversation_id,
            messages.c.role,
            messages.c.content,
        ).order_by(
            messages.c.conversation_id.asc(),
            messages.c.created_at.asc(),
            messages.c.id.asc(),
        )
    ).mappings()
    skip_assistant_by_conversation: dict[str, bool] = {}
    for row in rows:
        conversation_id = row["conversation_id"]
        role = row["role"]
        content = " ".join(str(row["content"] or "").strip().lower().split())
        memory_enabled = True
        if role == "user":
            memory_enabled = not any(marker in content for marker in NO_MEMORY_MARKERS)
            skip_assistant_by_conversation[conversation_id] = not memory_enabled
        elif role == "assistant" and skip_assistant_by_conversation.get(conversation_id, False):
            memory_enabled = False
            skip_assistant_by_conversation[conversation_id] = False
        bind.execute(
            sa.update(messages)
            .where(messages.c.id == row["id"])
            .values(memory_enabled=memory_enabled)
        )


def downgrade() -> None:
    op.drop_column("messages", "memory_enabled")
