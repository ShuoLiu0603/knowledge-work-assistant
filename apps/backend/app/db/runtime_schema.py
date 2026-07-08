from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.engine.reflection import Inspector

from app.db.base import Base


def ensure_runtime_schema(engine: Engine) -> None:
    inspector = inspect(engine)
    dialect = engine.dialect.name

    Base.metadata.create_all(
        bind=engine,
        tables=[
            Base.metadata.tables["departments"],
            Base.metadata.tables["user_memory_events"],
            Base.metadata.tables["user_memory_recall_logs"],
            Base.metadata.tables["user_memory_update_jobs"],
        ],
    )
    add_column_if_missing(engine, inspector, "users", "department_id", "department_id VARCHAR(36)")
    add_column_if_missing(engine, inspector, "users", "security_level", "security_level INTEGER NOT NULL DEFAULT 1")
    add_column_if_missing(engine, inspector, "knowledge_bases", "department_id", "department_id VARCHAR(36)")
    add_column_if_missing(engine, inspector, "documents", "security_level", "security_level INTEGER NOT NULL DEFAULT 1")
    add_column_if_missing(engine, inspector, "conversations", "summary", "summary TEXT")
    add_column_if_missing(
        engine,
        inspector,
        "conversations",
        "summary_message_count",
        "summary_message_count INTEGER NOT NULL DEFAULT 0",
    )
    add_column_if_missing(engine, inspector, "conversations", "search_scope", "search_scope VARCHAR(30) NOT NULL DEFAULT 'single'")
    add_column_if_missing(engine, inspector, "conversations", "search_department_id", "search_department_id VARCHAR(36)")
    make_column_nullable_if_supported(engine, inspector, "conversations", "knowledge_base_id")
    make_column_nullable_if_supported(engine, inspector, "agent_runs", "knowledge_base_id")
    make_column_nullable_if_supported(engine, inspector, "retrieval_logs", "knowledge_base_id")

    json_type = "JSONB" if dialect == "postgresql" else "TEXT"
    add_column_if_missing(engine, inspector, "messages", "agent_trace", f"agent_trace {json_type}")
    add_column_if_missing(engine, inspector, "messages", "token_usage", f"token_usage {json_type}")

    content_tsv_type = "TSVECTOR" if dialect == "postgresql" else "TEXT"
    add_column_if_missing(engine, inspector, "document_chunks", "content_tsv", f"content_tsv {content_tsv_type}")
    add_column_if_missing(engine, inspector, "document_chunks", "qdrant_point_id", "qdrant_point_id VARCHAR(36)")
    add_column_if_missing(
        engine,
        inspector,
        "document_chunks",
        "security_level",
        "security_level INTEGER NOT NULL DEFAULT 1",
    )
    add_column_if_missing(
        engine,
        inspector,
        "retrieval_logs",
        "scope_type",
        "scope_type VARCHAR(30) NOT NULL DEFAULT 'single'",
    )
    add_column_if_missing(
        engine,
        inspector,
        "retrieval_logs",
        "searched_knowledge_base_ids",
        f"searched_knowledge_base_ids {json_type}",
    )
    reranker_enabled_type = "BOOLEAN" if dialect == "postgresql" else "INTEGER"
    reranker_enabled_default = "false" if dialect == "postgresql" else "0"
    add_column_if_missing(
        engine,
        inspector,
        "retrieval_logs",
        "reranker_enabled",
        f"reranker_enabled {reranker_enabled_type} NOT NULL DEFAULT {reranker_enabled_default}",
    )

    with engine.begin() as connection:
        connection.execute(text("UPDATE document_chunks SET qdrant_point_id = id WHERE qdrant_point_id IS NULL"))


def add_column_if_missing(
    engine: Engine,
    inspector: Inspector,
    table_name: str,
    column_name: str,
    ddl_fragment: str,
) -> None:
    columns = {column["name"] for column in inspector.get_columns(table_name)}
    if column_name in columns:
        return
    with engine.begin() as connection:
        connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {ddl_fragment}"))


def make_column_nullable_if_supported(
    engine: Engine,
    inspector: Inspector,
    table_name: str,
    column_name: str,
) -> None:
    columns = {column["name"]: column for column in inspector.get_columns(table_name)}
    column = columns.get(column_name)
    if not column or column.get("nullable"):
        return
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as connection:
        connection.execute(text(f"ALTER TABLE {table_name} ALTER COLUMN {column_name} DROP NOT NULL"))
