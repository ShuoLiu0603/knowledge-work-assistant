from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.db.models  # noqa: F401
from app.db.base import Base
from app.db.models.user_memory import UserMemory
from app.db.runtime_schema import ensure_runtime_schema
from helpers import create_user


class RuntimeSchemaTests(unittest.TestCase):
    def test_runtime_schema_migrates_legacy_project_memory_to_profile(self) -> None:
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
        try:
            Base.metadata.create_all(engine)
            now = datetime.now(timezone.utc)
            with SessionLocal() as session:
                user = create_user(session, "runtime-project-memory@example.com", "Runtime Project Memory")
                memory = UserMemory(
                    user_id=user.id,
                    content="user works on an agentic RAG project",
                    normalized_content="user works on an agentic rag project",
                    content_hash="runtime-project-memory-hash",
                    category="current_project",
                    kind="project",
                    canonical_key="project:current_project",
                    memory_layer="profile",
                    profile_slot="current_project",
                    scope_id=user.id,
                    pinned=True,
                    source_text="seed",
                    embedding=[],
                    embedding_model="",
                    embedding_dimension=0,
                    last_touched_at=now,
                )
                session.add(memory)
                session.commit()
                memory_id = memory.id

            ensure_runtime_schema(engine)

            with SessionLocal() as session:
                migrated = session.get(UserMemory, memory_id)
                self.assertIsNotNone(migrated)
                self.assertEqual(migrated.kind, "profile")
                self.assertEqual(migrated.category, "current_project")
                self.assertEqual(migrated.canonical_key, "project:current_project")
        finally:
            Base.metadata.drop_all(engine)
            engine.dispose()

    def test_runtime_schema_backfills_memory_governance_before_unique_indexes(self) -> None:
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
        try:
            Base.metadata.create_all(engine)
            with engine.begin() as connection:
                connection.execute(text("DROP INDEX IF EXISTS uq_user_memories_active_profile_singleton"))
                connection.execute(text("DROP INDEX IF EXISTS uq_user_memories_active_canonical_key"))

            now = datetime.now(timezone.utc)
            with SessionLocal() as session:
                user = create_user(session, "runtime-memory-schema@example.com", "Runtime Memory Schema")
                session.add_all(
                    [
                        UserMemory(
                            user_id=user.id,
                            content="user prefers concise answers",
                            normalized_content="user prefers concise answers",
                            content_hash="runtime-schema-hash-1",
                            category="response_detail",
                            kind="preference",
                            canonical_key="",
                            memory_layer="semantic",
                            profile_slot="",
                            scope_id="",
                            pinned=False,
                            source_text="seed",
                            embedding=[],
                            embedding_model="",
                            embedding_dimension=0,
                            last_touched_at=now - timedelta(minutes=1),
                        ),
                        UserMemory(
                            user_id=user.id,
                            content="user prefers detailed answers",
                            normalized_content="user prefers detailed answers",
                            content_hash="runtime-schema-hash-2",
                            category="response_detail",
                            kind="preference",
                            canonical_key="",
                            memory_layer="semantic",
                            profile_slot="",
                            scope_id="",
                            pinned=False,
                            source_text="seed",
                            embedding=[],
                            embedding_model="",
                            embedding_dimension=0,
                            last_touched_at=now,
                        ),
                    ]
                )
                session.commit()
                user_id = user.id

            ensure_runtime_schema(engine)

            with SessionLocal() as session:
                rows = session.scalars(
                    select(UserMemory)
                    .where(UserMemory.user_id == user_id)
                    .order_by(UserMemory.last_touched_at.desc())
                ).all()
                active = [memory for memory in rows if memory.status == "active"]
                superseded = [memory for memory in rows if memory.status == "superseded"]

                self.assertEqual(len(active), 1)
                self.assertEqual(len(superseded), 1)
                self.assertEqual(active[0].content, "user prefers detailed answers")
                self.assertEqual(active[0].memory_layer, "profile")
                self.assertEqual(active[0].profile_slot, "response_detail")
                self.assertEqual(active[0].canonical_key, "profile:response_detail")
                self.assertEqual(active[0].scope_id, user_id)
                self.assertTrue(active[0].pinned)
                self.assertEqual(superseded[0].superseded_by_id, active[0].id)

            indexes = {index["name"] for index in inspect(engine).get_indexes("user_memories")}
            self.assertIn("uq_user_memories_active_profile_singleton", indexes)
            self.assertIn("uq_user_memories_active_canonical_key", indexes)
        finally:
            Base.metadata.drop_all(engine)
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
