from __future__ import annotations

import unittest
from unittest.mock import patch

from app.db.models.user_memory import UserMemoryUpdateJob
from app.memory.types import MemoryAction
from app.services import memory_service
from app.workers.memory_tasks import process_memory_update_job
from helpers import create_user, isolated_session


class MemoryTaskTests(unittest.TestCase):
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
                patch("app.workers.memory_tasks.SessionLocal", return_value=session),
                patch("app.workers.memory_tasks.process_user_memory", side_effect=RuntimeError("llm unavailable")),
            ):
                result = process_memory_update_job(job.id)

            updated = session.get(UserMemoryUpdateJob, job.id)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(updated.status, "failed")
            self.assertEqual(updated.attempts, 1)
            self.assertIn("llm unavailable", updated.error_message)

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


if __name__ == "__main__":
    unittest.main()
