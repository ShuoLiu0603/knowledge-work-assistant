from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import select, text

from app.core.security import verify_password
from app.db.models.audit_log import AuditLog
from app.db.models.document import Document
from app.db.models.external_cleanup_job import ExternalCleanupJob
from app.db.models.knowledge_base import KnowledgeBase, KnowledgeBaseMember
from app.db.models.user import User
from app.schemas.admin import AdminUserCreate, AdminUserUpdate
from app.schemas.department import DepartmentAdminUpdate, DepartmentCreate
from app.schemas.knowledge_base import KnowledgeBaseCreate
from app.services.admin_service import create_admin_user, delete_admin_user, update_admin_user
from app.services.department_service import create_department, update_department_admin
from app.services.knowledge_base_service import create_knowledge_base
from helpers import create_user, isolated_session


class AdminUserManagementTests(unittest.TestCase):
    def test_system_admin_can_create_another_system_admin(self) -> None:
        with isolated_session() as session:
            actor = create_user(session, "admin-actor@example.com", "Admin Actor")
            actor.is_admin = True
            session.add(actor)
            session.commit()

            created = create_admin_user(
                session,
                AdminUserCreate(
                    email="New.Admin@Example.com",
                    username="New Admin",
                    password="Password123!",
                    is_admin=True,
                    security_level=5,
                ),
                actor_user_id=actor.id,
            )

            stored = session.get(User, created.id)
            self.assertEqual(created.email, "new.admin@example.com")
            self.assertTrue(created.is_admin)
            self.assertTrue(verify_password("Password123!", stored.hashed_password))
            audit = session.scalar(
                select(AuditLog).where(AuditLog.action == "admin.create_user", AuditLog.resource_id == created.id)
            )
            self.assertIsNotNone(audit)
            self.assertEqual(audit.actor_user_id, actor.id)

    def test_delete_user_removes_account_and_queues_owned_resource_cleanup(self) -> None:
        with isolated_session() as session:
            actor = create_user(session, "delete-actor@example.com", "Delete Actor")
            actor.is_admin = True
            session.add(actor)
            session.commit()
            target = create_user(session, "delete-target@example.com", "Delete Target")
            target.is_admin = True
            session.add(target)
            session.commit()
            knowledge_base = create_knowledge_base(
                session,
                target.id,
                KnowledgeBaseCreate(name="Target Private KB", visibility="private"),
            )
            public_knowledge_base = create_knowledge_base(
                session,
                target.id,
                KnowledgeBaseCreate(name="Target Public KB", visibility="public"),
            )
            session.add(
                Document(
                    knowledge_base_id=knowledge_base.id,
                    uploader_id=target.id,
                    file_name="target.md",
                    file_ext="md",
                    mime_type="text/markdown",
                    file_size=10,
                    object_key="objects/target.md",
                    content_hash="target-document-hash",
                    status="indexed",
                )
            )
            session.commit()

            with patch("app.services.admin_service.dispatch_external_cleanup_jobs") as dispatch:
                delete_admin_user(session, target.id, actor_user_id=actor.id)

            self.assertIsNone(session.get(User, target.id))
            self.assertIsNone(session.get(KnowledgeBase, knowledge_base.id))
            transferred_public = session.get(KnowledgeBase, public_knowledge_base.id)
            self.assertIsNotNone(transferred_public)
            self.assertEqual(transferred_public.owner_id, actor.id)
            cleanup_job = session.scalar(
                select(ExternalCleanupJob).where(
                    ExternalCleanupJob.resource_type == "knowledge_base",
                    ExternalCleanupJob.resource_id == knowledge_base.id,
                )
            )
            self.assertIsNotNone(cleanup_job)
            self.assertEqual(cleanup_job.object_keys, ["objects/target.md"])
            dispatch.assert_called_once_with([cleanup_job.id])
            audit = session.scalar(
                select(AuditLog).where(AuditLog.action == "admin.delete_user", AuditLog.resource_id == target.id)
            )
            self.assertEqual(audit.extra_metadata["owned_knowledge_base_count"], 2)
            self.assertEqual(audit.extra_metadata["deleted_knowledge_base_count"], 1)
            self.assertEqual(audit.extra_metadata["transferred_knowledge_base_count"], 1)

    def test_department_admin_must_be_replaced_before_deactivation_or_deletion(self) -> None:
        with isolated_session() as session:
            actor = create_user(session, "department-guard-actor@example.com", "Department Guard Actor")
            actor.is_admin = True
            session.add(actor)
            session.commit()
            manager = create_user(session, "department-guard-manager@example.com", "Department Guard Manager")
            department = create_department(
                session,
                DepartmentCreate(name="Guarded Department", admin_user_id=manager.id),
                actor_user_id=actor.id,
            )

            with self.assertRaises(HTTPException) as deactivate_error:
                update_admin_user(
                    session,
                    manager.id,
                    AdminUserUpdate(is_active=False),
                    actor_user_id=actor.id,
                )
            with self.assertRaises(HTTPException) as delete_error:
                delete_admin_user(session, manager.id, actor_user_id=actor.id)

            self.assertEqual(deactivate_error.exception.status_code, 409)
            self.assertEqual(delete_error.exception.status_code, 409)
            self.assertEqual(session.get(User, manager.id).department_id, department.id)

    def test_delete_replaced_department_admin_preserves_shared_knowledge_base(self) -> None:
        with isolated_session() as session:
            session.execute(text("PRAGMA foreign_keys = ON"))
            actor = create_user(session, "department-delete-actor@example.com", "Department Delete Actor")
            actor.is_admin = True
            session.add(actor)
            session.commit()
            previous_admin = create_user(
                session,
                "department-previous-admin@example.com",
                "Previous Department Admin",
            )
            replacement = create_user(
                session,
                "department-replacement-admin@example.com",
                "Replacement Department Admin",
            )
            department = create_department(
                session,
                DepartmentCreate(name="Preserved Department", admin_user_id=previous_admin.id),
                actor_user_id=actor.id,
            )
            knowledge_base = create_knowledge_base(
                session,
                previous_admin.id,
                KnowledgeBaseCreate(name="Preserved Department KB", visibility="department"),
            )
            document = Document(
                knowledge_base_id=knowledge_base.id,
                uploader_id=previous_admin.id,
                file_name="preserved.md",
                file_ext="md",
                mime_type="text/markdown",
                file_size=10,
                object_key="objects/preserved.md",
                content_hash="preserved-department-document-hash",
                status="indexed",
            )
            session.add(document)
            session.commit()

            update_department_admin(
                session,
                department.id,
                DepartmentAdminUpdate(admin_user_id=replacement.id),
                actor_user_id=actor.id,
            )
            with patch("app.services.admin_service.dispatch_external_cleanup_jobs") as dispatch:
                delete_admin_user(session, previous_admin.id, actor_user_id=actor.id)

            self.assertIsNone(session.get(User, previous_admin.id))
            stored_knowledge_base = session.get(KnowledgeBase, knowledge_base.id)
            self.assertIsNotNone(stored_knowledge_base)
            self.assertEqual(stored_knowledge_base.owner_id, replacement.id)
            stored_document = session.get(Document, document.id)
            self.assertIsNotNone(stored_document)
            self.assertIsNone(stored_document.uploader_id)
            replacement_membership = session.scalar(
                select(KnowledgeBaseMember).where(
                    KnowledgeBaseMember.knowledge_base_id == knowledge_base.id,
                    KnowledgeBaseMember.user_id == replacement.id,
                )
            )
            self.assertIsNotNone(replacement_membership)
            self.assertEqual(replacement_membership.role, "owner")
            cleanup_job = session.scalar(
                select(ExternalCleanupJob).where(
                    ExternalCleanupJob.resource_type == "knowledge_base",
                    ExternalCleanupJob.resource_id == knowledge_base.id,
                )
            )
            self.assertIsNone(cleanup_job)
            dispatch.assert_not_called()

    def test_system_admin_cannot_delete_own_account(self) -> None:
        with isolated_session() as session:
            actor = create_user(session, "self-delete-admin@example.com", "Self Delete Admin")
            actor.is_admin = True
            session.add(actor)
            session.commit()

            with self.assertRaises(HTTPException) as error:
                delete_admin_user(session, actor.id, actor_user_id=actor.id)

            self.assertEqual(error.exception.status_code, 400)
            self.assertIsNotNone(session.get(User, actor.id))

    def test_system_admin_cannot_modify_own_account(self) -> None:
        with isolated_session() as session:
            actor = create_user(session, "self-update-admin@example.com", "Self Update Admin")
            actor.is_admin = True
            actor.security_level = 5
            backup = create_user(session, "self-update-backup@example.com", "Backup Admin")
            backup.is_admin = True
            session.add_all([actor, backup])
            session.commit()

            with self.assertRaises(HTTPException) as error:
                update_admin_user(
                    session,
                    actor.id,
                    AdminUserUpdate(is_active=False, is_admin=False, security_level=1),
                    actor_user_id=actor.id,
                )

            self.assertEqual(error.exception.status_code, 400)
            self.assertEqual(error.exception.detail, "Administrators cannot modify their own account")
            session.refresh(actor)
            self.assertTrue(actor.is_active)
            self.assertTrue(actor.is_admin)
            self.assertEqual(actor.security_level, 5)
            audit = session.scalar(
                select(AuditLog).where(
                    AuditLog.action == "admin.update_user",
                    AuditLog.actor_user_id == actor.id,
                    AuditLog.resource_id == actor.id,
                )
            )
            self.assertEqual(audit.outcome, "denied")


if __name__ == "__main__":
    unittest.main()
