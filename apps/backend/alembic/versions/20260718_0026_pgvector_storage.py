"""store document and memory embeddings in PostgreSQL

Revision ID: 20260718_0026
Revises: 20260717_0025
Create Date: 2026-07-18
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260718_0026"
down_revision = "20260717_0025"
branch_labels = None
depends_on = None

LEGACY_DOCUMENT_VECTOR_REFERENCE_COLUMN = "qdr" + "ant_point_id"


def has_column(bind, table_name: str, column_name: str) -> bool:
    return column_name in {column["name"] for column in sa.inspect(bind).get_columns(table_name)}


def has_index(bind, table_name: str, index_name: str) -> bool:
    return index_name in {index["name"] for index in sa.inspect(bind).get_indexes(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
        if not has_column(bind, "document_chunks", "embedding"):
            op.execute("ALTER TABLE document_chunks ADD COLUMN embedding vector")
        if not has_column(bind, "document_chunks", "embedding_model"):
            op.add_column(
                "document_chunks",
                sa.Column("embedding_model", sa.String(length=100), nullable=False, server_default=""),
            )
        if not has_column(bind, "document_chunks", "embedding_dimension"):
            op.add_column(
                "document_chunks",
                sa.Column("embedding_dimension", sa.Integer(), nullable=False, server_default="0"),
            )
        if has_column(bind, "user_memories", "embedding"):
            op.execute("ALTER TABLE user_memories ALTER COLUMN embedding DROP DEFAULT")
            op.execute(
                """
                ALTER TABLE user_memories
                ALTER COLUMN embedding TYPE vector
                USING CASE
                    WHEN embedding IS NULL OR embedding::text = '[]' THEN NULL
                    ELSE embedding::text::vector
                END
                """
            )
            op.execute("ALTER TABLE user_memories ALTER COLUMN embedding DROP NOT NULL")
    else:
        if has_column(bind, "user_memories", "embedding"):
            with op.batch_alter_table("user_memories") as batch:
                batch.alter_column("embedding", existing_type=sa.JSON(), nullable=True, server_default=None)
        if not has_column(bind, "document_chunks", "embedding"):
            with op.batch_alter_table("document_chunks") as batch:
                batch.add_column(sa.Column("embedding", sa.JSON(), nullable=True))
        if not has_column(bind, "document_chunks", "embedding_model"):
            with op.batch_alter_table("document_chunks") as batch:
                batch.add_column(sa.Column("embedding_model", sa.String(length=100), nullable=False, server_default=""))
        if not has_column(bind, "document_chunks", "embedding_dimension"):
            with op.batch_alter_table("document_chunks") as batch:
                batch.add_column(sa.Column("embedding_dimension", sa.Integer(), nullable=False, server_default="0"))

    if has_column(bind, "document_chunks", LEGACY_DOCUMENT_VECTOR_REFERENCE_COLUMN):
        with op.batch_alter_table("document_chunks") as batch:
            batch.drop_column(LEGACY_DOCUMENT_VECTOR_REFERENCE_COLUMN)

    if not has_index(bind, "document_chunks", "ix_document_chunks_dense_scope"):
        op.create_index(
            "ix_document_chunks_dense_scope",
            "document_chunks",
            ["knowledge_base_id", "security_level", "embedding_dimension"],
            postgresql_where=sa.text("embedding IS NOT NULL"),
            sqlite_where=sa.text("embedding IS NOT NULL"),
        )
    if not has_index(bind, "user_memories", "ix_user_memories_active_embedding"):
        op.create_index(
            "ix_user_memories_active_embedding",
            "user_memories",
            ["user_id", "status", "embedding_dimension"],
            postgresql_where=sa.text("embedding IS NOT NULL"),
            sqlite_where=sa.text("embedding IS NOT NULL"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    if has_index(bind, "user_memories", "ix_user_memories_active_embedding"):
        op.drop_index("ix_user_memories_active_embedding", table_name="user_memories")
    if has_index(bind, "document_chunks", "ix_document_chunks_dense_scope"):
        op.drop_index("ix_document_chunks_dense_scope", table_name="document_chunks")

    if dialect == "postgresql" and has_column(bind, "user_memories", "embedding"):
        op.execute(
            "ALTER TABLE user_memories ALTER COLUMN embedding TYPE jsonb "
            "USING COALESCE(to_jsonb(embedding), '[]'::jsonb)"
        )
        op.execute("ALTER TABLE user_memories ALTER COLUMN embedding SET DEFAULT '[]'::jsonb")
        op.execute("ALTER TABLE user_memories ALTER COLUMN embedding SET NOT NULL")

    with op.batch_alter_table("document_chunks") as batch:
        if has_column(bind, "document_chunks", "embedding_dimension"):
            batch.drop_column("embedding_dimension")
        if has_column(bind, "document_chunks", "embedding_model"):
            batch.drop_column("embedding_model")
        if has_column(bind, "document_chunks", "embedding"):
            batch.drop_column("embedding")
