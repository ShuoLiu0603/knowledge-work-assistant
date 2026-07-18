from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

from sqlalchemy import JSON, Float, bindparam, cast, text
from sqlalchemy.engine import Engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.types import TypeDecorator, UserDefinedType


class _PostgresVector(UserDefinedType):
    cache_ok = True

    def get_col_spec(self, **_kw: Any) -> str:
        return "VECTOR"


class PgVector(TypeDecorator[list[float] | None]):
    """Store embeddings as pgvector in PostgreSQL and JSON in SQLite tests."""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(_PostgresVector())
        return dialect.type_descriptor(JSON())

    def process_bind_param(self, value: object, dialect):
        vector = normalize_vector(value)
        if vector is None:
            return None
        if dialect.name == "postgresql":
            return vector_literal(vector)
        return vector

    def process_result_value(self, value: object, _dialect):
        return normalize_vector(value)


def ensure_pgvector_extension(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))


def supports_pgvector(bind: Any) -> bool:
    engine = getattr(bind, "bind", bind)
    return getattr(getattr(engine, "dialect", None), "name", None) == "postgresql"


def cosine_distance(column: ColumnElement, query_vector: Sequence[float]) -> ColumnElement:
    parameter = bindparam(None, value=list(query_vector), type_=PgVector())
    return column.op("<=>", return_type=Float())(cast(parameter, _PostgresVector()))


def cosine_similarity(left: Sequence[float] | None, right: Sequence[float] | None) -> float | None:
    if not left or not right or len(left) != len(right):
        return None
    left_values = [float(value) for value in left]
    right_values = [float(value) for value in right]
    denominator = math.sqrt(sum(value * value for value in left_values)) * math.sqrt(
        sum(value * value for value in right_values)
    )
    if denominator == 0:
        return None
    return sum(left_value * right_value for left_value, right_value in zip(left_values, right_values, strict=True)) / denominator


def normalize_vector(value: object) -> list[float] | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped or stripped == "[]":
            return []
        if not (stripped.startswith("[") and stripped.endswith("]")):
            raise ValueError("pgvector values must use bracket notation")
        stripped = stripped[1:-1].strip()
        return [] if not stripped else [float(item) for item in stripped.split(",")]
    to_list = getattr(value, "tolist", None)
    if callable(to_list):
        value = to_list()
    if not isinstance(value, Sequence) or isinstance(value, (bytes, bytearray)):
        raise TypeError("Embedding values must be a sequence of numbers")
    return [float(item) for item in value]


def vector_literal(vector: Sequence[float]) -> str:
    return "[" + ",".join(format(float(value), ".12g") for value in vector) + "]"
