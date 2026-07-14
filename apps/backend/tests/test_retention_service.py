from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from app.api.routes.admin import router as admin_router
from app.db.models.agent_run import AgentRun
from app.db.models.audit_log import AuditLog
from app.db.models.external_cleanup_job import ExternalCleanupJob
from app.db.models.llm_call_log import LlmCallLog
from app.db.models.retrieval_log import RetrievalLog
from app.db.models.user_memory import UserMemoryRecallLog, UserMemoryUpdateJob
from app.services.retention_service import apply_operational_retention
from app.workers.retention_tasks import serialize_retention_result
from helpers import create_user, isolated_session


RETENTION_SETTINGS = SimpleNamespace(
    llm_call_log_retention_days=90,
    retrieval_log_retention_days=90,
    agent_run_retention_days=90,
    memory_recall_log_retention_days=90,
    memory_update_job_retention_days=30,
    external_cleanup_job_retention_days=30,
)


class RetentionServiceTests(unittest.TestCase):
    def test_retention_admin_route_requires_admin_dependency(self) -> None:
        route_dependencies = {
            route.path: [dependency.call.__name__ for dependency in route.dependant.dependencies]
            for route in admin_router.routes
        }

        self.assertIn("require_admin", route_dependencies["/admin/retention/run"])

    def test_operational_retention_dry_run_counts_without_deleting(self) -> None:
        now = datetime(2026, 7, 10, tzinfo=timezone.utc)
        with isolated_session() as session, patch("app.services.retention_service.get_settings", return_value=RETENTION_SETTINGS):
            user = create_user(session, "retention-dry-run@example.com", "Retention Dry Run")
            seed_retention_rows(session, user.id, now)

            result = apply_operational_retention(session, dry_run=True, actor_user_id=user.id, now=now)

            self.assertTrue(result["dry_run"])
            self.assertEqual(
                result["deleted_counts"],
                {
                    "llm_call_logs": 1,
                    "retrieval_logs": 1,
                    "agent_runs": 1,
                    "memory_recall_logs": 1,
                    "memory_update_jobs": 1,
                    "external_cleanup_jobs": 1,
                },
            )
            self.assertEqual(len(session.query(LlmCallLog).all()), 2)
            self.assertEqual(len(session.query(RetrievalLog).all()), 2)
            self.assertEqual(len(session.query(AgentRun).all()), 2)
            self.assertEqual(len(session.query(UserMemoryRecallLog).all()), 2)
            self.assertEqual(len(session.query(UserMemoryUpdateJob).all()), 4)
            self.assertEqual(len(session.query(ExternalCleanupJob).all()), 3)
            self.assertEqual(len(session.query(AuditLog).filter(AuditLog.action == "retention.dry_run").all()), 1)

    def test_operational_retention_deletes_only_expired_operational_rows(self) -> None:
        now = datetime(2026, 7, 10, tzinfo=timezone.utc)
        with isolated_session() as session, patch("app.services.retention_service.get_settings", return_value=RETENTION_SETTINGS):
            user = create_user(session, "retention-apply@example.com", "Retention Apply")
            seed_retention_rows(session, user.id, now)
            old_audit = AuditLog(
                actor_user_id=user.id,
                action="memory.create",
                resource_type="user_memory",
                created_at=now - timedelta(days=365),
            )
            session.add(old_audit)
            session.commit()

            result = apply_operational_retention(session, dry_run=False, actor_user_id=user.id, now=now)

            self.assertFalse(result["dry_run"])
            self.assertEqual(result["deleted_counts"]["llm_call_logs"], 1)
            self.assertEqual(result["deleted_counts"]["retrieval_logs"], 1)
            self.assertEqual(result["deleted_counts"]["agent_runs"], 1)
            self.assertEqual(result["deleted_counts"]["memory_recall_logs"], 1)
            self.assertEqual(result["deleted_counts"]["memory_update_jobs"], 1)
            self.assertEqual(result["deleted_counts"]["external_cleanup_jobs"], 1)

            self.assertEqual([row.model_name for row in session.query(LlmCallLog).all()], ["new-model"])
            self.assertEqual([row.query for row in session.query(RetrievalLog).all()], ["new retrieval"])
            self.assertEqual([row.input for row in session.query(AgentRun).all()], ["new agent run"])
            self.assertEqual([row.query for row in session.query(UserMemoryRecallLog).all()], ["new recall"])

            remaining_memory_jobs = {row.status: row.user_message for row in session.query(UserMemoryUpdateJob).all()}
            self.assertEqual(remaining_memory_jobs["completed"], "new completed job")
            self.assertEqual(remaining_memory_jobs["queued"], "old queued job")
            self.assertEqual(remaining_memory_jobs["processing"], "old processing job")

            cleanup_jobs = session.query(ExternalCleanupJob).all()
            self.assertEqual({job.status for job in cleanup_jobs}, {"completed", "failed"})
            self.assertTrue(any(job.resource_id == "new-completed-cleanup" for job in cleanup_jobs))
            self.assertTrue(any(job.resource_id == "old-failed-cleanup" for job in cleanup_jobs))

            audit_actions = {row.action for row in session.query(AuditLog).all()}
            self.assertIn("memory.create", audit_actions)
            self.assertIn("retention.apply", audit_actions)

    def test_retention_can_be_disabled_per_target(self) -> None:
        disabled_settings = SimpleNamespace(
            **{
                **RETENTION_SETTINGS.__dict__,
                "llm_call_log_retention_days": 0,
            }
        )
        now = datetime(2026, 7, 10, tzinfo=timezone.utc)
        with isolated_session() as session, patch("app.services.retention_service.get_settings", return_value=disabled_settings):
            user = create_user(session, "retention-disabled@example.com", "Retention Disabled")
            seed_retention_rows(session, user.id, now)

            result = apply_operational_retention(session, dry_run=False, actor_user_id=user.id, now=now)

            self.assertEqual(result["deleted_counts"]["llm_call_logs"], 0)
            self.assertEqual(len(session.query(LlmCallLog).all()), 2)

    def test_retention_worker_result_is_json_safe(self) -> None:
        now = datetime(2026, 7, 10, tzinfo=timezone.utc)

        result = serialize_retention_result(
            {
                "generated_at": now,
                "dry_run": True,
                "deleted_counts": {"llm_call_logs": 1},
                "cutoffs": {"llm_call_logs": now - timedelta(days=90), "retrieval_logs": None},
            }
        )

        self.assertEqual(result["generated_at"], "2026-07-10T00:00:00+00:00")
        self.assertEqual(result["cutoffs"]["llm_call_logs"], "2026-04-11T00:00:00+00:00")
        self.assertIsNone(result["cutoffs"]["retrieval_logs"])


def seed_retention_rows(session, user_id: str, now: datetime) -> None:
    old_100 = now - timedelta(days=100)
    old_40 = now - timedelta(days=40)
    new_10 = now - timedelta(days=10)
    session.add_all(
        [
            LlmCallLog(
                user_id=user_id,
                provider="local",
                model_name="old-model",
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
                status="success",
                created_at=old_100,
            ),
            LlmCallLog(
                user_id=user_id,
                provider="local",
                model_name="new-model",
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
                status="success",
                created_at=new_10,
            ),
            RetrievalLog(
                user_id=user_id,
                query="old retrieval",
                created_at=old_100,
            ),
            RetrievalLog(
                user_id=user_id,
                query="new retrieval",
                created_at=new_10,
            ),
            AgentRun(
                user_id=user_id,
                input="old agent run",
                created_at=old_100,
            ),
            AgentRun(
                user_id=user_id,
                input="new agent run",
                created_at=new_10,
            ),
            UserMemoryRecallLog(
                user_id=user_id,
                query="old recall",
                recall_mode="semantic",
                requested_limit=5,
                recall_limit=5,
                active_count=1,
                selected_count=1,
                created_at=old_100,
            ),
            UserMemoryRecallLog(
                user_id=user_id,
                query="new recall",
                recall_mode="semantic",
                requested_limit=5,
                recall_limit=5,
                active_count=1,
                selected_count=1,
                created_at=new_10,
            ),
            UserMemoryUpdateJob(
                user_id=user_id,
                user_message="old completed job",
                status="completed",
                created_at=old_40,
                updated_at=old_40,
            ),
            UserMemoryUpdateJob(
                user_id=user_id,
                user_message="new completed job",
                status="completed",
                created_at=new_10,
                updated_at=new_10,
            ),
            UserMemoryUpdateJob(
                user_id=user_id,
                user_message="old queued job",
                status="queued",
                created_at=old_40,
                updated_at=old_40,
            ),
            UserMemoryUpdateJob(
                user_id=user_id,
                user_message="old processing job",
                status="processing",
                created_at=old_40,
                updated_at=old_40,
            ),
            ExternalCleanupJob(
                actor_user_id=user_id,
                resource_type="document",
                resource_id="old-completed-cleanup",
                status="completed",
                object_keys=[],
                created_at=old_40,
                updated_at=old_40,
                completed_at=old_40,
            ),
            ExternalCleanupJob(
                actor_user_id=user_id,
                resource_type="document",
                resource_id="new-completed-cleanup",
                status="completed",
                object_keys=[],
                created_at=new_10,
                updated_at=new_10,
                completed_at=new_10,
            ),
            ExternalCleanupJob(
                actor_user_id=user_id,
                resource_type="document",
                resource_id="old-failed-cleanup",
                status="failed",
                object_keys=[],
                created_at=old_40,
                updated_at=old_40,
            ),
        ]
    )
    session.commit()


if __name__ == "__main__":
    unittest.main()
