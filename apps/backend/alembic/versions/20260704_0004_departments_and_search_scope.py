"""add departments and retrieval scopes

Revision ID: 20260704_0004
Revises: 20260703_0003
Create Date: 2026-07-04
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260704_0004"
down_revision = "20260703_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "departments",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("name", name="uq_departments_name"),
    )
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("department_id", sa.String(length=36)))
        batch.create_index("ix_users_department_id", ["department_id"])
        batch.create_foreign_key(
            "fk_users_department_id_departments",
            "departments",
            ["department_id"],
            ["id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table("knowledge_bases") as batch:
        batch.add_column(sa.Column("department_id", sa.String(length=36)))
        batch.create_index("ix_knowledge_bases_department_id", ["department_id"])
        batch.create_foreign_key(
            "fk_knowledge_bases_department_id_departments",
            "departments",
            ["department_id"],
            ["id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table("retrieval_logs") as batch:
        batch.add_column(sa.Column("scope_type", sa.String(length=30), nullable=False, server_default="single"))
        batch.add_column(sa.Column("searched_knowledge_base_ids", sa.JSON(), nullable=False, server_default=sa.text("'[]'")))


def downgrade() -> None:
    with op.batch_alter_table("retrieval_logs") as batch:
        batch.drop_column("searched_knowledge_base_ids")
        batch.drop_column("scope_type")

    with op.batch_alter_table("knowledge_bases") as batch:
        batch.drop_constraint("fk_knowledge_bases_department_id_departments", type_="foreignkey")
        batch.drop_index("ix_knowledge_bases_department_id")
        batch.drop_column("department_id")

    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("fk_users_department_id_departments", type_="foreignkey")
        batch.drop_index("ix_users_department_id")
        batch.drop_column("department_id")

    op.drop_table("departments")
