from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import select

from app.db.models.audit_log import AuditLog
from app.db.models.document import Document
from app.db.models.external_cleanup_job import ExternalCleanupJob
from app.db.models.knowledge_base import KnowledgeBaseMember
from app.schemas.department import DepartmentCreate
from app.schemas.knowledge_base import KnowledgeBaseCreate, KnowledgeBaseUpdate
from app.schemas.qa import AskKnowledgeBaseRequest
from app.services.department_service import create_department
from app.services.knowledge_base_service import (
    create_knowledge_base,
    delete_knowledge_base,
    ensure_kb_access,
    get_knowledge_base,
    list_knowledge_bases,
    resolve_search_scope,
    update_knowledge_base,
)
from app.services.qa_service import ask_knowledge_base
from helpers import create_user, isolated_session


class KnowledgeBasePermissionTests(unittest.TestCase):
    def test_owner_viewer_and_private_boundary(self) -> None:
        with isolated_session() as session:
            owner = create_user(session, "owner@example.com", "Owner")
            other = create_user(session, "viewer@example.com", "Viewer")

            kb = create_knowledge_base(
                session,
                owner.id,
                KnowledgeBaseCreate(name="Private Policy KB", visibility="private"),
            )

            knowledge_base, role = ensure_kb_access(session, owner.id, kb.id, required_role="owner")
            self.assertEqual(knowledge_base.id, kb.id)
            self.assertEqual(role, "owner")

            with self.assertRaises(HTTPException) as no_membership:
                ensure_kb_access(session, other.id, kb.id, required_role="viewer")
            self.assertEqual(no_membership.exception.status_code, 404)
            self.assertEqual(list_knowledge_bases(session, other.id), [])

            session.add(KnowledgeBaseMember(knowledge_base_id=kb.id, user_id=other.id, role="viewer"))
            session.commit()

            _knowledge_base, viewer_role = ensure_kb_access(session, other.id, kb.id, required_role="viewer")
            self.assertEqual(viewer_role, "viewer")

            with self.assertRaises(HTTPException) as insufficient_role:
                ensure_kb_access(session, other.id, kb.id, required_role="editor")
            self.assertEqual(insufficient_role.exception.status_code, 403)

    def test_public_knowledge_base_is_readable_but_admin_managed(self) -> None:
        with isolated_session() as session:
            admin = create_user(session, "public-admin@example.com", "Public Admin")
            admin.is_admin = True
            session.add(admin)
            session.commit()
            user = create_user(session, "public-user@example.com", "Public User")

            public_kb = create_knowledge_base(
                session,
                admin.id,
                KnowledgeBaseCreate(name="Public Policy KB", visibility="public"),
            )

            _knowledge_base, admin_role = ensure_kb_access(session, admin.id, public_kb.id, required_role="owner")
            self.assertEqual(admin_role, "owner")
            _knowledge_base, user_role = ensure_kb_access(session, user.id, public_kb.id, required_role="viewer")
            self.assertEqual(user_role, "viewer")

            with self.assertRaises(HTTPException) as insufficient_role:
                ensure_kb_access(session, user.id, public_kb.id, required_role="editor")
            self.assertEqual(insufficient_role.exception.status_code, 403)

            visible_to_user = list_knowledge_bases(session, user.id)
            self.assertEqual([item.id for item in visible_to_user], [public_kb.id])
            self.assertEqual(visible_to_user[0].role, "viewer")

    def test_any_admin_can_manage_public_knowledge_base(self) -> None:
        with isolated_session() as session:
            creator = create_user(session, "public-creator@example.com", "Public Creator")
            manager = create_user(session, "public-manager@example.com", "Public Manager")
            viewer = create_user(session, "public-viewer@example.com", "Public Viewer")
            creator.is_admin = True
            manager.is_admin = True
            session.add_all([creator, manager])
            session.commit()
            public_kb = create_knowledge_base(
                session,
                creator.id,
                KnowledgeBaseCreate(name="Shared Public KB", visibility="public"),
            )

            updated = update_knowledge_base(
                session,
                manager.id,
                public_kb.id,
                KnowledgeBaseUpdate(name="Managed Public KB"),
            )
            self.assertEqual(updated.name, "Managed Public KB")
            self.assertEqual(updated.role, "owner")

            with self.assertRaises(HTTPException) as forbidden:
                update_knowledge_base(
                    session,
                    viewer.id,
                    public_kb.id,
                    KnowledgeBaseUpdate(name="Viewer Rename"),
                )
            self.assertEqual(forbidden.exception.status_code, 403)

            delete_knowledge_base(session, manager.id, public_kb.id)
            with self.assertRaises(HTTPException) as missing:
                ensure_kb_access(session, viewer.id, public_kb.id, required_role="viewer")
            self.assertEqual(missing.exception.status_code, 404)

    def test_knowledge_base_lifecycle_records_audit_events(self) -> None:
        with isolated_session() as session:
            owner = create_user(session, "kb-audit-owner@example.com", "KB Audit Owner")
            kb = create_knowledge_base(
                session,
                owner.id,
                KnowledgeBaseCreate(name="Audited KB", visibility="private"),
            )

            updated = update_knowledge_base(
                session,
                owner.id,
                kb.id,
                KnowledgeBaseUpdate(name="Audited KB Updated"),
            )
            delete_knowledge_base(session, owner.id, kb.id)

            audit_logs = session.scalars(
                select(AuditLog).where(AuditLog.resource_id == kb.id)
            ).all()
            logs_by_action = {log.action: log for log in audit_logs}

            self.assertEqual(
                set(logs_by_action),
                {
                    "knowledge_base.create",
                    "knowledge_base.update",
                    "knowledge_base.external_cleanup",
                    "knowledge_base.delete",
                },
            )
            self.assertTrue(all(log.actor_user_id == owner.id for log in audit_logs))
            self.assertEqual(logs_by_action["knowledge_base.create"].extra_metadata["name"], "Audited KB")
            self.assertEqual(logs_by_action["knowledge_base.update"].extra_metadata["previous"]["name"], "Audited KB")
            self.assertEqual(logs_by_action["knowledge_base.update"].extra_metadata["current"]["name"], updated.name)
            self.assertEqual(logs_by_action["knowledge_base.delete"].extra_metadata["name"], updated.name)
            self.assertEqual(logs_by_action["knowledge_base.delete"].extra_metadata["cleanup_status"], "completed")
            self.assertEqual(logs_by_action["knowledge_base.external_cleanup"].outcome, "success")

    def test_denied_public_update_audit_does_not_commit_partial_changes(self) -> None:
        with isolated_session() as session:
            owner = create_user(session, "kb-public-denied-owner@example.com", "KB Public Denied Owner")
            kb = create_knowledge_base(
                session,
                owner.id,
                KnowledgeBaseCreate(name="Private Draft", visibility="private"),
            )

            with self.assertRaises(HTTPException) as forbidden:
                update_knowledge_base(
                    session,
                    owner.id,
                    kb.id,
                    KnowledgeBaseUpdate(name="Should Not Persist", visibility="public"),
                )

            self.assertEqual(forbidden.exception.status_code, 403)
            current = get_knowledge_base(session, owner.id, kb.id)
            self.assertEqual(current.name, "Private Draft")
            self.assertEqual(current.visibility, "private")
            denied = session.scalar(
                select(AuditLog).where(
                    AuditLog.action == "knowledge_base.update",
                    AuditLog.resource_id == kb.id,
                    AuditLog.outcome == "denied",
                )
            )
            self.assertIsNotNone(denied)
            self.assertEqual(denied.detail, "Only admins can publish knowledge bases")

    def test_delete_knowledge_base_removes_document_objects(self) -> None:
        with isolated_session() as session:
            owner = create_user(session, "delete-kb-owner@example.com", "Delete KB Owner")
            kb = create_knowledge_base(session, owner.id, KnowledgeBaseCreate(name="Delete KB"))
            document = Document(
                knowledge_base_id=kb.id,
                uploader_id=owner.id,
                file_name="delete-kb.md",
                file_ext="md",
                mime_type="text/markdown",
                file_size=10,
                object_key="objects/delete-kb.md",
                content_hash="delete-kb-hash",
                status="indexed",
            )
            session.add(document)
            session.commit()

            with patch("app.services.cleanup_service.remove_object") as remove_object:
                delete_knowledge_base(session, owner.id, kb.id)

            remove_object.assert_called_once_with("objects/delete-kb.md")
            with self.assertRaises(HTTPException):
                ensure_kb_access(session, owner.id, kb.id, required_role="viewer")

    def test_delete_knowledge_base_records_retryable_cleanup_job_when_cleanup_fails(self) -> None:
        with isolated_session() as session:
            owner = create_user(session, "delete-kb-cleanup@example.com", "Delete KB Cleanup")
            kb = create_knowledge_base(session, owner.id, KnowledgeBaseCreate(name="Delete KB Cleanup"))

            with patch(
                "app.services.cleanup_service.cleanup_external_resources",
                side_effect=RuntimeError("object storage offline"),
            ):
                delete_knowledge_base(session, owner.id, kb.id)

            with self.assertRaises(HTTPException) as missing:
                ensure_kb_access(session, owner.id, kb.id, required_role="owner")
            self.assertEqual(missing.exception.status_code, 404)
            cleanup_job = session.scalar(select(ExternalCleanupJob).where(ExternalCleanupJob.resource_id == kb.id))
            self.assertIsNotNone(cleanup_job)
            self.assertEqual(cleanup_job.status, "failed")
            self.assertEqual(cleanup_job.attempts, 1)
            audit_log = session.query(AuditLog).filter(AuditLog.action == "knowledge_base.external_cleanup").one()
            self.assertEqual(audit_log.outcome, "failed")
            delete_audit = session.scalar(
                select(AuditLog).where(AuditLog.action == "knowledge_base.delete", AuditLog.resource_id == kb.id)
            )
            self.assertEqual(delete_audit.extra_metadata["cleanup_status"], "failed")
            self.assertEqual(delete_audit.extra_metadata["cleanup_job_id"], cleanup_job.id)

    def test_only_admin_can_create_public_knowledge_base(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "private-user@example.com", "Private User")

            private_kb = create_knowledge_base(
                session,
                user.id,
                KnowledgeBaseCreate(name="Personal KB", visibility="private"),
            )
            self.assertEqual(private_kb.role, "owner")
            self.assertEqual(private_kb.visibility, "private")

            with self.assertRaises(HTTPException) as forbidden:
                create_knowledge_base(
                    session,
                    user.id,
                    KnowledgeBaseCreate(name="Not Allowed Public KB", visibility="public"),
                )
            self.assertEqual(forbidden.exception.status_code, 403)
            denied = session.scalar(
                select(AuditLog).where(
                    AuditLog.action == "knowledge_base.create",
                    AuditLog.outcome == "denied",
                )
            )
            self.assertIsNotNone(denied)
            self.assertEqual(denied.actor_user_id, user.id)
            self.assertEqual(denied.detail, "Only admins can create public knowledge bases")

    def test_department_knowledge_base_is_visible_only_inside_department(self) -> None:
        with isolated_session() as session:
            owner = create_user(session, "ops-owner@example.com", "Ops Owner")
            peer = create_user(session, "ops-peer@example.com", "Ops Peer")
            outsider = create_user(session, "marketing-user@example.com", "Marketing User")
            marketing_manager = create_user(session, "marketing-manager@example.com", "Marketing Manager")
            system_admin = create_user(session, "department-system-admin@example.com", "Department System Admin")
            system_admin.is_admin = True
            session.add(system_admin)
            session.commit()
            ops = create_department(session, DepartmentCreate(name="Operations", admin_user_id=owner.id))
            marketing = create_department(
                session,
                DepartmentCreate(name="Marketing", admin_user_id=marketing_manager.id),
            )
            peer.department_id = ops.id
            outsider.department_id = marketing.id
            session.add_all([peer, outsider])
            session.commit()

            kb = create_knowledge_base(
                session,
                owner.id,
                KnowledgeBaseCreate(name="Ops Playbook", visibility="department"),
            )
            system_created = create_knowledge_base(
                session,
                system_admin.id,
                KnowledgeBaseCreate(
                    name="Ops System Managed",
                    visibility="department",
                    department_id=ops.id,
                ),
            )

            self.assertEqual(kb.department_id, ops.id)
            self.assertEqual(system_created.owner_id, owner.id)
            session.add(KnowledgeBaseMember(knowledge_base_id=kb.id, user_id=peer.id, role="owner"))
            session.commit()
            _knowledge_base, peer_role = ensure_kb_access(session, peer.id, kb.id, required_role="viewer")
            self.assertEqual(peer_role, "viewer")
            with self.assertRaises(HTTPException) as create_forbidden:
                create_knowledge_base(
                    session,
                    peer.id,
                    KnowledgeBaseCreate(name="Peer Department KB", visibility="department"),
                )
            self.assertEqual(create_forbidden.exception.status_code, 403)
            with self.assertRaises(HTTPException) as update_forbidden:
                update_knowledge_base(
                    session,
                    peer.id,
                    kb.id,
                    KnowledgeBaseUpdate(name="Peer Rename"),
                )
            self.assertEqual(update_forbidden.exception.status_code, 403)
            with self.assertRaises(HTTPException) as missing:
                ensure_kb_access(session, outsider.id, kb.id, required_role="viewer")
            self.assertEqual(missing.exception.status_code, 404)
            self.assertEqual(
                {item.id for item in list_knowledge_bases(session, peer.id)},
                {kb.id, system_created.id},
            )
            self.assertEqual(list_knowledge_bases(session, outsider.id), [])

    def test_search_scopes_use_distinct_knowledge_base_sets(self) -> None:
        with isolated_session() as session:
            admin = create_user(session, "finance-admin@example.com", "Finance Admin")
            user = create_user(session, "finance-user@example.com", "Finance User")
            department = create_department(
                session,
                DepartmentCreate(name="Finance", admin_user_id=user.id),
            )
            admin.is_admin = True
            admin.department_id = department.id
            session.add_all([admin, user])
            session.commit()

            public_kb = create_knowledge_base(
                session,
                admin.id,
                KnowledgeBaseCreate(name="Company Policy", visibility="public"),
            )
            department_kb = create_knowledge_base(
                session,
                user.id,
                KnowledgeBaseCreate(name="Finance SOP", visibility="department"),
            )
            private_kb = create_knowledge_base(
                session,
                user.id,
                KnowledgeBaseCreate(name="Personal Notes", visibility="private"),
            )

            single_scope = resolve_search_scope(session, user.id, private_kb.id, scope_type="single")
            department_scope = resolve_search_scope(session, user.id, scope_type="department")
            public_scope = resolve_search_scope(session, user.id, scope_type="public")
            accessible_scope = resolve_search_scope(session, user.id, scope_type="accessible")

            self.assertEqual(single_scope.knowledge_base_ids, [private_kb.id])
            self.assertEqual(set(department_scope.knowledge_base_ids), {department_kb.id})
            self.assertEqual(set(public_scope.knowledge_base_ids), {public_kb.id})
            self.assertEqual(
                set(accessible_scope.knowledge_base_ids),
                {public_kb.id, department_kb.id, private_kb.id},
            )

    def test_direct_knowledge_base_ask_cannot_widen_search_scope(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "direct-ask-scope@example.com", "Direct Ask Scope")
            department = create_department(
                session,
                DepartmentCreate(name="Legal", admin_user_id=user.id),
            )
            kb = create_knowledge_base(
                session,
                user.id,
                KnowledgeBaseCreate(name="Legal Notes", visibility="private"),
            )

            invalid_payloads = [
                AskKnowledgeBaseRequest(question="What changed?", search_scope="public"),
                AskKnowledgeBaseRequest(question="What changed?", search_scope="department", department_id=department.id),
                AskKnowledgeBaseRequest(question="What changed?", department_id=department.id),
            ]
            for payload in invalid_payloads:
                with self.subTest(search_scope=payload.search_scope, department_id=payload.department_id):
                    with self.assertRaises(HTTPException) as error:
                        ask_knowledge_base(session, user.id, kb.id, payload)

                    self.assertEqual(error.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
