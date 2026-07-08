"""add memory provenance and embedding metadata

Revision ID: 20260707_0007
Revises: 20260705_0006
Create Date: 2026-07-07
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260707_0007"
down_revision = "20260705_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("user_memories") as batch:
        batch.add_column(sa.Column("source_conversation_id", sa.String(length=36)))
        batch.add_column(sa.Column("source_message_id", sa.String(length=36)))
        batch.add_column(sa.Column("embedding_model", sa.String(length=100), nullable=False, server_default=""))
        batch.add_column(sa.Column("embedding_dimension", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("valid_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
        batch.add_column(sa.Column("invalid_at", sa.DateTime(timezone=True)))
        batch.create_index("ix_user_memories_source_conversation_id", ["source_conversation_id"])
        batch.create_index("ix_user_memories_source_message_id", ["source_message_id"])
        batch.create_foreign_key(
            "fk_user_memories_source_conversation_id_conversations",
            "conversations",
            ["source_conversation_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_foreign_key(
            "fk_user_memories_source_message_id_messages",
            "messages",
            ["source_message_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("user_memories") as batch:
        batch.drop_constraint("fk_user_memories_source_message_id_messages", type_="foreignkey")
        batch.drop_constraint("fk_user_memories_source_conversation_id_conversations", type_="foreignkey")
        batch.drop_index("ix_user_memories_source_message_id")
        batch.drop_index("ix_user_memories_source_conversation_id")
        batch.drop_column("invalid_at")
        batch.drop_column("valid_at")
        batch.drop_column("embedding_dimension")
        batch.drop_column("embedding_model")
        batch.drop_column("source_message_id")
        batch.drop_column("source_conversation_id")
