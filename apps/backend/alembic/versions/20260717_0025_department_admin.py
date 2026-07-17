"""add department administrator

Revision ID: 20260717_0025
Revises: 20260713_0024
Create Date: 2026-07-17
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260717_0025"
down_revision = "20260713_0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("departments")}
    if "admin_user_id" not in columns:
        with op.batch_alter_table("departments") as batch:
            batch.add_column(sa.Column("admin_user_id", sa.String(length=36), nullable=True))

    inspector = sa.inspect(bind)
    unique_names = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("departments")
        if constraint.get("name")
    }
    index_names = {index["name"] for index in inspector.get_indexes("departments")}
    if "uq_departments_admin_user_id" not in unique_names | index_names:
        with op.batch_alter_table("departments") as batch:
            batch.create_unique_constraint("uq_departments_admin_user_id", ["admin_user_id"])

    inspector = sa.inspect(bind)
    foreign_key_names = {
        foreign_key["name"]
        for foreign_key in inspector.get_foreign_keys("departments")
        if foreign_key.get("name")
    }
    if "fk_departments_admin_user_id_users" not in foreign_key_names:
        with op.batch_alter_table("departments") as batch:
            batch.create_foreign_key(
                "fk_departments_admin_user_id_users",
                "users",
                ["admin_user_id"],
                ["id"],
                ondelete="SET NULL",
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    foreign_key_names = {
        foreign_key["name"]
        for foreign_key in inspector.get_foreign_keys("departments")
        if foreign_key.get("name")
    }
    if "fk_departments_admin_user_id_users" in foreign_key_names:
        with op.batch_alter_table("departments") as batch:
            batch.drop_constraint("fk_departments_admin_user_id_users", type_="foreignkey")

    inspector = sa.inspect(bind)
    unique_names = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("departments")
        if constraint.get("name")
    }
    index_names = {index["name"] for index in inspector.get_indexes("departments")}
    if "uq_departments_admin_user_id" in unique_names:
        with op.batch_alter_table("departments") as batch:
            batch.drop_constraint("uq_departments_admin_user_id", type_="unique")
    elif "uq_departments_admin_user_id" in index_names:
        op.drop_index("uq_departments_admin_user_id", table_name="departments")

    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("departments")}
    if "admin_user_id" in columns:
        with op.batch_alter_table("departments") as batch:
            batch.drop_column("admin_user_id")
