from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import select

from app.db.models.audit_log import AuditLog
from app.db.models.document import Document
from app.db.models.external_cleanup_job import ExternalCleanupJob
from app.schemas.department import DepartmentCreate
from app.schemas.knowledge_base import KnowledgeBaseCreate
from app.services.department_service import create_department
from app.services.document_service import create_uploaded_document, delete_document, list_documents, sanitize_file_name
from app.services.knowledge_base_service import create_knowledge_base
from app.services.cleanup_service import run_external_cleanup_job
from helpers import create_user, isolated_session


class DocumentServiceTests(unittest.TestCase):
    def test_upload_rejects_duplicate_content_in_same_knowledge_base(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "document-owner@example.com", "Document Owner")
            make_admin(session, user)
            kb = create_knowledge_base(
                session,
                user.id,
                KnowledgeBaseCreate(name="Policy KB", visibility="private"),
            )
            payload = b"# Policy\n\nSame content."

            with (
                patch("app.services.document_service.upload_bytes"),
                patch(
                    "app.services.document_service.process_document.delay",
                    return_value=SimpleNamespace(id="job-1"),
                ) as delay,
            ):
                first = create_uploaded_document(
                    session,
                    user.id,
                    kb.id,
                    "policy.md",
                    "text/markdown",
                    payload,
                )

            self.assertEqual(first.status, "uploaded")
            self.assertEqual(delay.call_count, 1)

            with (
                patch("app.services.document_service.upload_bytes") as upload,
                patch("app.services.document_service.process_document.delay") as duplicate_delay,
                self.assertRaises(HTTPException) as error,
            ):
                create_uploaded_document(
                    session,
                    user.id,
                    kb.id,
                    "policy-copy.md",
                    "text/markdown",
                    payload,
                )

            self.assertEqual(error.exception.status_code, 409)
            upload.assert_not_called()
            duplicate_delay.assert_not_called()
            self.assertEqual(len(list_documents(session, user.id, kb.id)), 1)

    def test_same_content_can_exist_in_different_knowledge_bases(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "multi-kb@example.com", "Multi KB")
            make_admin(session, user)
            first_kb = create_knowledge_base(
                session,
                user.id,
                KnowledgeBaseCreate(name="First KB", visibility="private"),
            )
            second_kb = create_knowledge_base(
                session,
                user.id,
                KnowledgeBaseCreate(name="Second KB", visibility="private"),
            )
            payload = b"# Shared policy"

            with (
                patch("app.services.document_service.upload_bytes"),
                patch(
                    "app.services.document_service.process_document.delay",
                    return_value=SimpleNamespace(id="job-1"),
                ),
            ):
                first = create_uploaded_document(session, user.id, first_kb.id, "policy.md", "text/markdown", payload)
                second = create_uploaded_document(session, user.id, second_kb.id, "policy.md", "text/markdown", payload)

            self.assertNotEqual(first.document_id, second.document_id)
            self.assertEqual(len(list_documents(session, user.id, first_kb.id)), 1)
            self.assertEqual(len(list_documents(session, user.id, second_kb.id)), 1)

    def test_upload_marks_document_failed_when_enqueue_fails_and_allows_retry(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "enqueue-failure@example.com", "Enqueue Failure")
            kb = create_knowledge_base(
                session,
                user.id,
                KnowledgeBaseCreate(name="Enqueue Failure KB", visibility="private"),
            )
            payload = b"# Retryable"

            with (
                patch("app.services.document_service.upload_bytes"),
                patch("app.services.document_service.remove_object") as remove_object,
                patch("app.services.document_service.process_document.delay", side_effect=RuntimeError("redis offline")),
                self.assertRaises(HTTPException) as error,
            ):
                create_uploaded_document(session, user.id, kb.id, "retry.md", "text/markdown", payload)

            self.assertEqual(error.exception.status_code, 503)
            failed = session.scalar(select(Document).where(Document.knowledge_base_id == kb.id))
            self.assertIsNotNone(failed)
            self.assertEqual(failed.status, "failed")
            self.assertIn("enqueue failed", failed.error_message)
            remove_object.assert_called_once()

            with (
                patch("app.services.document_service.upload_bytes"),
                patch(
                    "app.services.document_service.process_document.delay",
                    return_value=SimpleNamespace(id="job-2"),
                ),
            ):
                retried = create_uploaded_document(session, user.id, kb.id, "retry.md", "text/markdown", payload)

            self.assertEqual(retried.status, "uploaded")

    def test_private_knowledge_base_owner_can_upload_document(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "clearance@example.com", "Clearance")
            kb = create_knowledge_base(
                session,
                user.id,
                KnowledgeBaseCreate(name="Clearance KB", visibility="private"),
            )

            with (
                patch("app.services.document_service.upload_bytes"),
                patch(
                    "app.services.document_service.process_document.delay",
                    return_value=SimpleNamespace(id="job-1"),
                ),
            ):
                uploaded = create_uploaded_document(
                    session,
                    user.id,
                    kb.id,
                    "personal.md",
                    "text/markdown",
                    b"# Personal",
                    security_level=1,
                )

            self.assertEqual(uploaded.status, "uploaded")
            self.assertEqual(uploaded.security_level, 1)
            self.assertEqual(len(list_documents(session, user.id, kb.id)), 1)

    def test_private_knowledge_base_ignores_requested_security_level(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "clearance-limit@example.com", "Clearance Limit")
            user.security_level = 1
            session.add(user)
            session.commit()
            kb = create_knowledge_base(
                session,
                user.id,
                KnowledgeBaseCreate(name="Clearance Limit KB", visibility="private"),
            )

            with (
                patch("app.services.document_service.upload_bytes"),
                patch(
                    "app.services.document_service.process_document.delay",
                    return_value=SimpleNamespace(id="job-1"),
                ),
            ):
                uploaded = create_uploaded_document(
                    session,
                    user.id,
                    kb.id,
                    "secret.md",
                    "text/markdown",
                    b"# Secret",
                    security_level=3,
                )

            self.assertEqual(uploaded.security_level, 1)
            documents = list_documents(session, user.id, kb.id)
            self.assertEqual([document.security_level for document in documents], [1])

    def test_private_knowledge_base_owner_can_read_existing_high_security_document(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "private-owner@example.com", "Private Owner")
            user.security_level = 1
            session.add(user)
            session.commit()
            kb = create_knowledge_base(
                session,
                user.id,
                KnowledgeBaseCreate(name="Private Legacy KB", visibility="private"),
            )
            document = Document(
                knowledge_base_id=kb.id,
                uploader_id=user.id,
                file_name="legacy.md",
                file_ext="md",
                mime_type="text/markdown",
                file_size=10,
                object_key="legacy",
                content_hash="legacy-high",
                status="indexed",
                security_level=5,
            )
            session.add(document)
            session.commit()

            documents = list_documents(session, user.id, kb.id)
            self.assertEqual([item.id for item in documents], [document.id])

    def test_delete_document_records_audit_from_captured_fields(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "delete-doc-owner@example.com", "Delete Doc Owner")
            kb = create_knowledge_base(
                session,
                user.id,
                KnowledgeBaseCreate(name="Delete Doc KB", visibility="private"),
            )
            document = Document(
                knowledge_base_id=kb.id,
                uploader_id=user.id,
                file_name="delete-me.md",
                file_ext="md",
                mime_type="text/markdown",
                file_size=10,
                object_key="objects/delete-me.md",
                content_hash="delete-doc-hash",
                status="indexed",
                security_level=5,
            )
            session.add(document)
            session.commit()
            document_id = document.id

            with (
                patch("app.services.cleanup_service.remove_object") as remove_object,
                patch("app.services.cleanup_service.delete_document_vectors") as delete_vectors,
            ):
                delete_document(session, user.id, document_id)

            self.assertIsNone(session.get(Document, document_id))
            remove_object.assert_called_once_with("objects/delete-me.md")
            delete_vectors.assert_called_once_with(document_id)
            audit_log = session.scalar(select(AuditLog).where(AuditLog.action == "document.delete"))
            self.assertIsNotNone(audit_log)
            self.assertEqual(audit_log.resource_id, document_id)
            self.assertEqual(audit_log.security_level, 5)
            self.assertEqual(audit_log.extra_metadata["knowledge_base_id"], kb.id)
            self.assertEqual(audit_log.extra_metadata["file_name"], "delete-me.md")
            self.assertEqual(audit_log.extra_metadata["cleanup_status"], "completed")

    def test_delete_document_records_retryable_cleanup_job_when_cleanup_fails(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "delete-cleanup@example.com", "Delete Cleanup")
            kb = create_knowledge_base(
                session,
                user.id,
                KnowledgeBaseCreate(name="Delete Cleanup KB", visibility="private"),
            )
            document = Document(
                knowledge_base_id=kb.id,
                uploader_id=user.id,
                file_name="cleanup.md",
                file_ext="md",
                mime_type="text/markdown",
                file_size=10,
                object_key="objects/cleanup.md",
                content_hash="cleanup-hash",
                status="indexed",
            )
            session.add(document)
            session.commit()
            document_id = document.id

            with (
                patch("app.services.cleanup_service.delete_document_vectors", side_effect=RuntimeError("qdrant offline")),
                patch("app.services.cleanup_service.remove_object") as remove_object,
            ):
                delete_document(session, user.id, document_id)

            self.assertIsNone(session.get(Document, document_id))
            remove_object.assert_not_called()
            cleanup_job = session.scalar(select(ExternalCleanupJob).where(ExternalCleanupJob.resource_id == document_id))
            self.assertIsNotNone(cleanup_job)
            self.assertEqual(cleanup_job.status, "failed")
            self.assertEqual(cleanup_job.attempts, 1)
            self.assertIn("qdrant offline", cleanup_job.error_message)
            cleanup_audit = session.scalar(select(AuditLog).where(AuditLog.action == "document.external_cleanup"))
            self.assertIsNotNone(cleanup_audit)
            self.assertEqual(cleanup_audit.outcome, "failed")
            delete_audit = session.scalar(select(AuditLog).where(AuditLog.action == "document.delete"))
            self.assertEqual(delete_audit.extra_metadata["cleanup_status"], "failed")
            self.assertEqual(delete_audit.extra_metadata["cleanup_job_id"], cleanup_job.id)

            with (
                patch("app.services.cleanup_service.delete_document_vectors") as delete_vectors,
                patch("app.services.cleanup_service.remove_object") as retry_remove_object,
            ):
                retried = run_external_cleanup_job(session, cleanup_job.id)

            self.assertEqual(retried.status, "completed")
            self.assertEqual(retried.attempts, 2)
            delete_vectors.assert_called_once_with(document_id)
            retry_remove_object.assert_called_once_with("objects/cleanup.md")

    def test_non_admin_cannot_upload_to_public_knowledge_base(self) -> None:
        with isolated_session() as session:
            admin = create_user(session, "public-doc-admin@example.com", "Public Doc Admin")
            make_admin(session, admin)
            user = create_user(session, "public-doc-user@example.com", "Public Doc User")
            kb = create_knowledge_base(
                session,
                admin.id,
                KnowledgeBaseCreate(name="Public Docs KB", visibility="public"),
            )

            with self.assertRaises(HTTPException) as error:
                create_uploaded_document(
                    session,
                    user.id,
                    kb.id,
                    "public.md",
                    "text/markdown",
                    b"# Public",
                )

            self.assertEqual(error.exception.status_code, 403)
            audit_log = session.scalar(select(AuditLog).where(AuditLog.action == "document.upload"))
            self.assertIsNotNone(audit_log)
            self.assertEqual(audit_log.outcome, "denied")
            self.assertEqual(audit_log.detail, "Only admins can manage public knowledge base documents")

    def test_department_knowledge_base_documents_respect_user_security_level(self) -> None:
        with isolated_session() as session:
            department = create_department(session, DepartmentCreate(name="Legal"))
            owner = create_user(session, "legal-owner@example.com", "Legal Owner")
            viewer = create_user(session, "legal-viewer@example.com", "Legal Viewer")
            owner.department_id = department.id
            viewer.department_id = department.id
            viewer.security_level = 1
            session.add_all([owner, viewer])
            session.commit()
            kb = create_knowledge_base(
                session,
                owner.id,
                KnowledgeBaseCreate(name="Legal KB", visibility="department"),
            )
            low_document = Document(
                knowledge_base_id=kb.id,
                uploader_id=owner.id,
                file_name="legal-public.md",
                file_ext="md",
                mime_type="text/markdown",
                file_size=10,
                object_key="legal-public",
                content_hash="legal-public",
                status="indexed",
                security_level=1,
            )
            high_document = Document(
                knowledge_base_id=kb.id,
                uploader_id=owner.id,
                file_name="legal-secret.md",
                file_ext="md",
                mime_type="text/markdown",
                file_size=10,
                object_key="legal-secret",
                content_hash="legal-secret",
                status="indexed",
                security_level=4,
            )
            session.add_all([low_document, high_document])
            session.commit()

            documents = list_documents(session, viewer.id, kb.id)

            self.assertEqual([document.file_name for document in documents], ["legal-public.md"])

    def test_admin_can_upload_to_public_knowledge_base_created_by_another_admin(self) -> None:
        with isolated_session() as session:
            creator = create_user(session, "public-doc-creator@example.com", "Public Doc Creator")
            manager = create_user(session, "public-doc-manager@example.com", "Public Doc Manager")
            make_admin(session, creator)
            make_admin(session, manager)
            kb = create_knowledge_base(
                session,
                creator.id,
                KnowledgeBaseCreate(name="Shared Public Docs KB", visibility="public"),
            )

            with (
                patch("app.services.document_service.upload_bytes"),
                patch(
                    "app.services.document_service.process_document.delay",
                    return_value=SimpleNamespace(id="job-1"),
                ),
            ):
                uploaded = create_uploaded_document(
                    session,
                    manager.id,
                    kb.id,
                    "shared.md",
                    "text/markdown",
                    b"# Shared",
                    security_level=5,
                )

            self.assertEqual(uploaded.status, "uploaded")
            self.assertEqual(uploaded.security_level, 5)

    def test_admin_can_classify_uploaded_document_above_own_retrieval_level(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "admin-classifier@example.com", "Admin Classifier")
            user.security_level = 2
            make_admin(session, user)
            kb = create_knowledge_base(
                session,
                user.id,
                KnowledgeBaseCreate(name="Admin KB", visibility="public"),
            )

            with (
                patch("app.services.document_service.upload_bytes"),
                patch(
                    "app.services.document_service.process_document.delay",
                    return_value=SimpleNamespace(id="job-1"),
                ),
            ):
                uploaded = create_uploaded_document(
                    session,
                    user.id,
                    kb.id,
                    "restricted.md",
                    "text/markdown",
                    b"# Restricted",
                    security_level=4,
                )

            self.assertEqual(uploaded.security_level, 4)

    def test_file_name_sanitization_keeps_safe_basename(self) -> None:
        self.assertEqual(sanitize_file_name("../财务 制度?.md"), "财务_制度.md")
        self.assertEqual(sanitize_file_name("..."), "upload.txt")


def make_admin(session, user) -> None:
    user.is_admin = True
    session.add(user)
    session.commit()


if __name__ == "__main__":
    unittest.main()
