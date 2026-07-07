"""add conversation search targets

Revision ID: 20260705_0006
Revises: 20260705_0005
Create Date: 2026-07-05
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260705_0006"
down_revision = "20260705_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("conversations") as batch:
        batch.add_column(sa.Column("search_scope", sa.String(length=30), nullable=False, server_default="single"))
        batch.add_column(sa.Column("search_department_id", sa.String(length=36)))
        batch.create_index("ix_conversations_search_scope", ["search_scope"])
        batch.create_index("ix_conversations_search_department_id", ["search_department_id"])
        batch.create_foreign_key(
            "fk_conversations_search_department_id_departments",
            "departments",
            ["search_department_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.alter_column("knowledge_base_id", existing_type=sa.String(length=36), nullable=True)

    with op.batch_alter_table("agent_runs") as batch:
        batch.alter_column("knowledge_base_id", existing_type=sa.String(length=36), nullable=True)

    with op.batch_alter_table("retrieval_logs") as batch:
        batch.alter_column("knowledge_base_id", existing_type=sa.String(length=36), nullable=True)


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("DELETE FROM conversations WHERE knowledge_base_id IS NULL"))
    connection.execute(sa.text("DELETE FROM agent_runs WHERE knowledge_base_id IS NULL"))
    connection.execute(sa.text("DELETE FROM retrieval_logs WHERE knowledge_base_id IS NULL"))

    with op.batch_alter_table("retrieval_logs") as batch:
        batch.alter_column("knowledge_base_id", existing_type=sa.String(length=36), nullable=False)

    with op.batch_alter_table("agent_runs") as batch:
        batch.alter_column("knowledge_base_id", existing_type=sa.String(length=36), nullable=False)

    with op.batch_alter_table("conversations") as batch:
        batch.alter_column("knowledge_base_id", existing_type=sa.String(length=36), nullable=False)
        batch.drop_constraint("fk_conversations_search_department_id_departments", type_="foreignkey")
        batch.drop_index("ix_conversations_search_department_id")
        batch.drop_index("ix_conversations_search_scope")
        batch.drop_column("search_department_id")
        batch.drop_column("search_scope")
