"""add security levels

Revision ID: 20260703_0002
Revises: 20260703_0001
Create Date: 2026-07-03
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260703_0002"
down_revision = "20260703_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("security_level", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("documents", sa.Column("security_level", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("document_chunks", sa.Column("security_level", sa.Integer(), nullable=False, server_default="1"))
    op.create_index("ix_users_security_level", "users", ["security_level"])
    op.create_index("ix_documents_security_level", "documents", ["security_level"])
    op.create_index("ix_chunks_security_level", "document_chunks", ["security_level"])


def downgrade() -> None:
    op.drop_index("ix_chunks_security_level", table_name="document_chunks")
    op.drop_index("ix_documents_security_level", table_name="documents")
    op.drop_index("ix_users_security_level", table_name="users")
    op.drop_column("document_chunks", "security_level")
    op.drop_column("documents", "security_level")
    op.drop_column("users", "security_level")
