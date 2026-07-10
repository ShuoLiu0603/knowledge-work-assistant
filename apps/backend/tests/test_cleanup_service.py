from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi import HTTPException

from app.api.routes.admin import router as admin_router
from app.db.models.external_cleanup_job import ExternalCleanupJob
from app.services.cleanup_service import (
    claim_external_cleanup_job,
    create_external_cleanup_job,
    finish_external_cleanup_job,
    list_external_cleanup_jobs,
    retry_external_cleanup_job,
)
from app.workers.cleanup_tasks import list_recoverable_external_cleanup_job_ids
from helpers import create_user, isolated_session


class CleanupServiceTests(unittest.TestCase):
    def test_external_cleanup_admin_routes_require_admin_dependency(self) -> None:
        route_dependencies = {
            route.path: [dependency.call.__name__ for dependency in route.dependant.dependencies]
            for route in admin_router.routes
        }

        self.assertIn("require_admin", route_dependencies["/admin/external-cleanup-jobs"])
        self.assertIn("require_admin", route_dependencies["/admin/external-cleanup-jobs/{job_id}/retry"])

    def test_list_and_retry_external_cleanup_jobs(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "cleanup-admin@example.com", "Cleanup Admin")
            document_job = create_external_cleanup_job(
                session,
                actor_user_id=user.id,
                resource_type="document",
                resource_id="doc-1",
                object_keys=["objects/doc-1.md"],
            )
            create_external_cleanup_job(
                session,
                actor_user_id=user.id,
                resource_type="knowledge_base",
                resource_id="kb-1",
                object_keys=[],
            )
            session.commit()

            with patch("app.services.cleanup_service.delete_document_vectors", side_effect=RuntimeError("qdrant offline")):
                failed = retry_external_cleanup_job(session, document_job.id)

            failed_jobs = list_external_cleanup_jobs(session, status="failed")
            document_jobs = list_external_cleanup_jobs(session, resource_type="document")

            self.assertEqual([job.id for job in failed_jobs], [document_job.id])
            self.assertEqual([job.id for job in document_jobs], [document_job.id])
            self.assertEqual(failed.status, "failed")

            with (
                patch("app.services.cleanup_service.delete_document_vectors") as delete_vectors,
                patch("app.services.cleanup_service.remove_object") as remove_object,
            ):
                retried = retry_external_cleanup_job(session, document_job.id)

            self.assertEqual(retried.status, "completed")
            self.assertEqual(retried.attempts, 2)
            delete_vectors.assert_called_once_with("doc-1")
            remove_object.assert_called_once_with("objects/doc-1.md")

            with self.assertRaises(HTTPException) as completed_error:
                retry_external_cleanup_job(session, document_job.id)
            self.assertEqual(completed_error.exception.status_code, 409)

    def test_processing_cleanup_job_cannot_be_retried(self) -> None:
        with isolated_session() as session:
            job = ExternalCleanupJob(
                resource_type="document",
                resource_id="doc-processing",
                status="processing",
                object_keys=[],
                lease_token="active-owner",
                lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            )
            session.add(job)
            session.commit()

            with self.assertRaises(HTTPException) as processing_error:
                retry_external_cleanup_job(session, job.id)

            self.assertEqual(processing_error.exception.status_code, 409)

    def test_cleanup_job_claim_is_leased_and_fenced(self) -> None:
        now = datetime(2026, 7, 10, tzinfo=timezone.utc)
        with isolated_session() as session:
            job = ExternalCleanupJob(
                resource_type="user_memory",
                resource_id="memory-1",
                status="queued",
                object_keys=[],
            )
            session.add(job)
            session.commit()

            self.assertTrue(claim_external_cleanup_job(session, job.id, lease_token="owner", now=now))
            self.assertFalse(
                claim_external_cleanup_job(
                    session,
                    job.id,
                    lease_token="duplicate",
                    now=now + timedelta(seconds=1),
                )
            )
            self.assertIsNone(
                finish_external_cleanup_job(
                    session,
                    job.id,
                    lease_token="duplicate",
                    status="completed",
                    now=now + timedelta(seconds=2),
                )
            )
            self.assertEqual(session.get(ExternalCleanupJob, job.id).status, "processing")

    def test_cleanup_recovery_finds_queued_failed_and_expired_processing_jobs(self) -> None:
        now = datetime(2026, 7, 10, tzinfo=timezone.utc)
        with isolated_session() as session:
            queued = ExternalCleanupJob(
                resource_type="document",
                resource_id="queued",
                status="queued",
                updated_at=now - timedelta(minutes=20),
            )
            failed = ExternalCleanupJob(
                resource_type="document",
                resource_id="failed",
                status="failed",
                updated_at=now - timedelta(minutes=20),
            )
            expired = ExternalCleanupJob(
                resource_type="document",
                resource_id="expired",
                status="processing",
                lease_token="dead",
                lease_expires_at=now - timedelta(seconds=1),
                updated_at=now - timedelta(minutes=20),
            )
            active = ExternalCleanupJob(
                resource_type="document",
                resource_id="active",
                status="processing",
                lease_token="live",
                lease_expires_at=now + timedelta(minutes=5),
                updated_at=now,
            )
            session.add_all([queued, failed, expired, active])
            session.commit()

            recoverable = set(list_recoverable_external_cleanup_job_ids(session, now=now))

            self.assertEqual(recoverable, {queued.id, failed.id, expired.id})


if __name__ == "__main__":
    unittest.main()
