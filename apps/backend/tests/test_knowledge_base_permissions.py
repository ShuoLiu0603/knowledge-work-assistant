from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.db.models.audit_log import AuditLog
from app.db.models.document import Document
from app.db.models.knowledge_base import KnowledgeBaseMember
from app.schemas.department import DepartmentCreate
from app.schemas.knowledge_base import KnowledgeBaseCreate, KnowledgeBaseUpdate
from app.services.department_service import create_department
from app.services.knowledge_base_service import (
    create_knowledge_base,
    delete_knowledge_base,
    ensure_kb_access,
    list_knowledge_bases,
    resolve_search_scope,
    update_knowledge_base,
)
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

            with patch("app.services.knowledge_base_service.delete_knowledge_base_vectors"):
                delete_knowledge_base(session, manager.id, public_kb.id)
            with self.assertRaises(HTTPException) as missing:
                ensure_kb_access(session, viewer.id, public_kb.id, required_role="viewer")
            self.assertEqual(missing.exception.status_code, 404)

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

            with (
                patch("app.services.knowledge_base_service.delete_knowledge_base_vectors") as delete_vectors,
                patch("app.services.knowledge_base_service.remove_object") as remove_object,
            ):
                delete_knowledge_base(session, owner.id, kb.id)

            delete_vectors.assert_called_once_with(kb.id)
            remove_object.assert_called_once_with("objects/delete-kb.md")
            with self.assertRaises(HTTPException):
                ensure_kb_access(session, owner.id, kb.id, required_role="viewer")

    def test_delete_knowledge_base_keeps_database_row_when_cleanup_fails(self) -> None:
        with isolated_session() as session:
            owner = create_user(session, "delete-kb-cleanup@example.com", "Delete KB Cleanup")
            kb = create_knowledge_base(session, owner.id, KnowledgeBaseCreate(name="Delete KB Cleanup"))

            with (
                patch(
                    "app.services.knowledge_base_service.delete_knowledge_base_vectors",
                    side_effect=RuntimeError("qdrant offline"),
                ),
                self.assertRaises(HTTPException) as error,
            ):
                delete_knowledge_base(session, owner.id, kb.id)

            self.assertEqual(error.exception.status_code, 503)
            knowledge_base, role = ensure_kb_access(session, owner.id, kb.id, required_role="owner")
            self.assertEqual(knowledge_base.id, kb.id)
            self.assertEqual(role, "owner")
            audit_log = session.query(AuditLog).filter(AuditLog.action == "knowledge_base.delete_cleanup").one()
            self.assertEqual(audit_log.outcome, "failed")

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

    def test_department_knowledge_base_is_visible_only_inside_department(self) -> None:
        with isolated_session() as session:
            ops = create_department(session, DepartmentCreate(name="Operations"))
            marketing = create_department(session, DepartmentCreate(name="Marketing"))
            owner = create_user(session, "ops-owner@example.com", "Ops Owner")
            peer = create_user(session, "ops-peer@example.com", "Ops Peer")
            outsider = create_user(session, "marketing-user@example.com", "Marketing User")
            owner.department_id = ops.id
            peer.department_id = ops.id
            outsider.department_id = marketing.id
            session.add_all([owner, peer, outsider])
            session.commit()

            kb = create_knowledge_base(
                session,
                owner.id,
                KnowledgeBaseCreate(name="Ops Playbook", visibility="department"),
            )

            self.assertEqual(kb.department_id, ops.id)
            _knowledge_base, peer_role = ensure_kb_access(session, peer.id, kb.id, required_role="viewer")
            self.assertEqual(peer_role, "viewer")
            with self.assertRaises(HTTPException) as missing:
                ensure_kb_access(session, outsider.id, kb.id, required_role="viewer")
            self.assertEqual(missing.exception.status_code, 404)
            self.assertEqual([item.id for item in list_knowledge_bases(session, peer.id)], [kb.id])
            self.assertEqual(list_knowledge_bases(session, outsider.id), [])

    def test_search_scopes_use_distinct_knowledge_base_sets(self) -> None:
        with isolated_session() as session:
            department = create_department(session, DepartmentCreate(name="Finance"))
            admin = create_user(session, "finance-admin@example.com", "Finance Admin")
            user = create_user(session, "finance-user@example.com", "Finance User")
            admin.is_admin = True
            user.department_id = department.id
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


if __name__ == "__main__":
    unittest.main()
