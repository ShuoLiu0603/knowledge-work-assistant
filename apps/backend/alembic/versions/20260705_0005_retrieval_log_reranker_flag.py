"""add retrieval log reranker flag

Revision ID: 20260705_0005
Revises: 20260704_0004
Create Date: 2026-07-05
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260705_0005"
down_revision = "20260704_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("retrieval_logs")}
    if "reranker_enabled" in columns:
        return

    with op.batch_alter_table("retrieval_logs") as batch:
        batch.add_column(sa.Column("reranker_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("retrieval_logs")}
    if "reranker_enabled" not in columns:
        return

    with op.batch_alter_table("retrieval_logs") as batch:
        batch.drop_column("reranker_enabled")
