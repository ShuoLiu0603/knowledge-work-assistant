from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from sqlalchemy import UniqueConstraint


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "apps" / "backend"


def main() -> int:
    database_path = Path(tempfile.mkdtemp(prefix="agentic-rag-migration-")) / "verify.sqlite"
    database_url = f"sqlite+pysqlite:///{database_path.as_posix()}"

    os.environ["DATABASE_URL"] = database_url
    os.environ["AUTO_CREATE_TABLES"] = "false"
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))

    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "alembic"))
    command.upgrade(config, "head")

    from app.db.base import Base
    import app.db.models  # noqa: F401

    engine = create_engine(database_url)
    inspector = inspect(engine)
    errors = compare_metadata_to_database(Base.metadata.tables, inspector)
    engine.dispose()

    if errors:
        print("Migration verification failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print("Migration verification passed.")
    return 0


def compare_metadata_to_database(model_tables, inspector) -> list[str]:
    errors: list[str] = []
    database_tables = set(inspector.get_table_names())
    expected_tables = set(model_tables)
    missing_tables = sorted(expected_tables - database_tables)
    for table_name in missing_tables:
        errors.append(f"missing table: {table_name}")

    for table_name in sorted(expected_tables & database_tables):
        database_columns = {column["name"] for column in inspector.get_columns(table_name)}
        expected_columns = {column.name for column in model_tables[table_name].columns}
        missing_columns = sorted(expected_columns - database_columns)
        for column_name in missing_columns:
            errors.append(f"missing column: {table_name}.{column_name}")
        extra_columns = sorted(database_columns - expected_columns)
        for column_name in extra_columns:
            errors.append(f"extra column: {table_name}.{column_name}")

        expected_unique = expected_unique_constraints(model_tables[table_name])
        database_unique = database_unique_constraints(inspector, table_name)
        for columns in sorted(expected_unique - database_unique):
            errors.append(f"missing unique constraint: {table_name}({', '.join(columns)})")

    return errors


def expected_unique_constraints(table) -> set[tuple[str, ...]]:
    constraints = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    constraints.update((column.name,) for column in table.columns if column.unique)
    return constraints


def database_unique_constraints(inspector, table_name: str) -> set[tuple[str, ...]]:
    constraints = {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints(table_name)
        if constraint.get("column_names")
    }
    constraints.update(
        tuple(index["column_names"])
        for index in inspector.get_indexes(table_name)
        if index.get("unique") and index.get("column_names")
    )
    return constraints


if __name__ == "__main__":
    raise SystemExit(main())
