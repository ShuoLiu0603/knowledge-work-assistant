from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import func, select

from app.db.models.conversation import Conversation, Message
from app.db.models.user_memory import UserMemory, UserMemoryUpdateJob
from app.memory.jobs import create_memory_update_job
from app.memory.types import MemoryAction
from app.services import memory_service
from app.workers.memory_tasks import (
    process_memory_update_job,
    reconcile_memory_vector_indexes,
    reconcile_user_memory_task,
)
from helpers import create_user, isolated_session


class MemoryTaskTests(unittest.TestCase):
    def test_memory_update_job_is_idempotent_for_the_same_user_message(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "memory-job-idempotent@example.com", "Memory Job Idempotent")
            conversation = Conversation(user_id=user.id, title="Idempotent", search_scope="accessible")
            session.add(conversation)
            session.commit()
            message = Message(conversation_id=conversation.id, role="user", content="remember this")
            session.add(message)
            session.commit()

            first = create_memory_update_job(
                session,
                user_id=user.id,
                text=message.content,
                conversation_id=conversation.id,
                message_id=message.id,
                assistant_text="ack",
            )
            second = create_memory_update_job(
                session,
                user_id=user.id,
                text=message.content,
                conversation_id=conversation.id,
                message_id=message.id,
                assistant_text="ack",
            )

            self.assertEqual(first.id, second.id)
            self.assertEqual(session.scalar(select(func.count(UserMemoryUpdateJob.id))), 1)

    def test_process_memory_update_job_marks_job_completed(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "memory-task@example.com", "Memory Task")
            job = UserMemoryUpdateJob(
                user_id=user.id,
                user_message="I prefer concise answers",
                assistant_message="Got it.",
            )
            session.add(job)
            session.commit()
            session.refresh(job)

            with (
                patch("app.workers.memory_tasks.init_db"),
                patch("app.workers.memory_tasks.SessionLocal", return_value=session),
                patch(
                    "app.workers.memory_tasks.process_user_memory",
                    return_value=[MemoryAction("create", "memory-id", "I prefer concise answers", "test")],
                ) as process_user_memory,
            ):
                result = process_memory_update_job(job.id)

            updated = session.get(UserMemoryUpdateJob, job.id)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(updated.status, "completed")
            self.assertEqual(updated.attempts, 1)
            self.assertEqual(updated.actions[0]["action"], "create")
            process_user_memory.assert_called_once()

    def test_process_memory_update_job_marks_job_failed(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "memory-task-failure@example.com", "Memory Task Failure")
            job = UserMemoryUpdateJob(
                user_id=user.id,
                user_message="I prefer concise answers",
                assistant_message="Got it.",
            )
            session.add(job)
            session.commit()
            session.refresh(job)

            with (
                patch("app.workers.memory_tasks.init_db"),
                patch("app.workers.memory_tasks.SessionLocal", return_value=session),
                patch("app.workers.memory_tasks.process_user_memory", side_effect=RuntimeError("llm unavailable")),
            ):
                result = process_memory_update_job(job.id)

            updated = session.get(UserMemoryUpdateJob, job.id)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(updated.status, "failed")
            self.assertEqual(updated.attempts, 1)
            self.assertIn("llm unavailable", updated.error_message)

    def test_process_memory_update_job_respects_persisted_memory_off_flag(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "memory-task-off@example.com", "Memory Task Off")
            conversation = Conversation(user_id=user.id, title="Memory off", search_scope="accessible")
            session.add(conversation)
            session.commit()
            message = Message(
                conversation_id=conversation.id,
                role="user",
                content="ordinary private request",
                memory_enabled=False,
            )
            session.add(message)
            session.commit()
            job = UserMemoryUpdateJob(
                user_id=user.id,
                conversation_id=conversation.id,
                message_id=message.id,
                user_message=message.content,
                status="queued",
            )
            session.add(job)
            session.commit()

            with (
                patch("app.workers.memory_tasks.init_db"),
                patch("app.workers.memory_tasks.SessionLocal", return_value=session),
                patch("app.workers.memory_tasks.process_user_memory") as process_user_memory,
            ):
                result = process_memory_update_job(job.id)

            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["actions"][0]["reason"], "source message disabled memory")
            process_user_memory.assert_not_called()

    def test_user_can_list_and_retry_memory_update_jobs(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "memory-task-retry@example.com", "Memory Task Retry")
            job = UserMemoryUpdateJob(
                user_id=user.id,
                user_message="I prefer concise answers",
                assistant_message="Got it.",
                status="failed",
                error_message="llm unavailable",
            )
            session.add(job)
            session.commit()
            session.refresh(job)

            with patch("app.memory.jobs.dispatch_memory_update_job") as dispatch:
                retried = memory_service.retry_user_memory_update_job(session, user.id, job.id)

            self.assertEqual(retried.status, "queued")
            self.assertEqual(retried.error_message, "")
            dispatch.assert_called_once_with(job.id)
            jobs = memory_service.list_user_memory_update_jobs(session, user.id, status="queued")
            self.assertEqual([item.id for item in jobs], [job.id])

    def test_retry_keeps_job_queued_when_dispatch_fails(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "memory-task-retry-dispatch@example.com", "Memory Task Retry Dispatch")
            job = UserMemoryUpdateJob(
                user_id=user.id,
                user_message="I prefer concise answers",
                assistant_message="Got it.",
                status="failed",
            )
            session.add(job)
            session.commit()
            session.refresh(job)

            with patch("app.memory.jobs.dispatch_memory_update_job", side_effect=RuntimeError("broker down")):
                retried = memory_service.retry_user_memory_update_job(session, user.id, job.id)

            self.assertEqual(retried.status, "queued")
            self.assertIn("broker down", retried.error_message)

    def test_retry_rejects_an_already_dispatched_queued_job(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "memory-task-dispatched@example.com", "Memory Task Dispatched")
            job = UserMemoryUpdateJob(
                user_id=user.id,
                user_message="already waiting in broker",
                status="queued",
                dispatched_at=datetime.now(timezone.utc),
            )
            session.add(job)
            session.commit()

            with self.assertRaises(HTTPException) as raised:
                memory_service.retry_user_memory_update_job(session, user.id, job.id)

            self.assertEqual(raised.exception.status_code, 409)

    def test_retry_rejects_an_older_job_when_a_newer_job_exists(self) -> None:
        now = datetime.now(timezone.utc)
        with isolated_session() as session:
            user = create_user(session, "memory-task-stale-retry@example.com", "Memory Task Stale Retry")
            older = UserMemoryUpdateJob(
                user_id=user.id,
                user_message="old preference",
                status="failed",
                created_at=now,
                updated_at=now,
            )
            newer = UserMemoryUpdateJob(
                user_id=user.id,
                user_message="new preference",
                status="completed",
                created_at=now + timedelta(seconds=1),
                updated_at=now + timedelta(seconds=1),
            )
            session.add_all([older, newer])
            session.commit()

            with self.assertRaises(HTTPException) as raised:
                memory_service.retry_user_memory_update_job(session, user.id, older.id)

            self.assertEqual(raised.exception.status_code, 409)
            self.assertIn("newer user job", raised.exception.detail)

    def test_reconcile_user_memory_task_returns_report(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "memory-task-reconcile@example.com", "Memory Task Reconcile")

            with (
                patch("app.workers.memory_tasks.init_db"),
                patch("app.workers.memory_tasks.SessionLocal", return_value=session),
                patch(
                    "app.workers.memory_tasks.reconcile_user_memories",
                    return_value={
                        "user_id": user.id,
                        "apply": False,
                        "scanned_count": 0,
                        "applied_count": 0,
                        "findings": [],
                    },
                ) as reconcile,
            ):
                result = reconcile_user_memory_task(user.id, apply=False)

            self.assertEqual(result["user_id"], user.id)
            self.assertEqual(result["findings"], [])
            reconcile.assert_called_once_with(session, user.id, apply=False, llm_review=False)

    def test_periodic_memory_vector_reconcile_repairs_all_users(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "memory-vector-reconcile@example.com", "Memory Vector Reconcile")
            memory = UserMemory(
                user_id=user.id,
                content="user prefers concise answers",
                normalized_content="user prefers concise answers",
                content_hash="hash",
                scope_id=user.id,
                embedding=[1.0, 0.0],
                embedding_model="fake",
                embedding_dimension=2,
            )
            session.add(memory)
            session.commit()
            user_id = user.id

            finding = SimpleNamespace(applied=True)
            with (
                patch("app.workers.memory_tasks.init_db"),
                patch("app.workers.memory_tasks.SessionLocal", return_value=session),
                patch(
                    "app.workers.memory_tasks.get_settings",
                    return_value=SimpleNamespace(memory_vector_index_enabled=True),
                ),
                patch(
                    "app.workers.memory_tasks.memory_reconcile.reconcile_vector_index",
                    return_value=[finding],
                ) as reconcile,
            ):
                result = reconcile_memory_vector_indexes()

            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["user_count"], 1)
            self.assertEqual(result["applied_count"], 1)
            reconcile.assert_called_once_with(session, user_id, apply=True)


if __name__ == "__main__":
    unittest.main()
