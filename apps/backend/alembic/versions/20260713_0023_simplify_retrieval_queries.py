"""simplify retrieval logs to the agent-provided query

Revision ID: 20260713_0023
Revises: 20260713_0022
Create Date: 2026-07-13
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260713_0023"
down_revision = "20260713_0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("retrieval_logs", "question", new_column_name="query")
    op.drop_column("retrieval_logs", "rewritten_query")
    op.drop_column("retrieval_logs", "sub_questions")
    op.drop_column("retrieval_logs", "expanded_queries")


def downgrade() -> None:
    op.add_column("retrieval_logs", sa.Column("expanded_queries", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("retrieval_logs", sa.Column("sub_questions", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("retrieval_logs", sa.Column("rewritten_query", sa.Text(), nullable=True))
    op.execute("UPDATE retrieval_logs SET rewritten_query = query")
    op.alter_column("retrieval_logs", "rewritten_query", nullable=False)
    op.alter_column("retrieval_logs", "query", new_column_name="question")
