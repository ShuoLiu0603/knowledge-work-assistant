from __future__ import annotations

import unittest

from fastapi import HTTPException
from sqlalchemy import select

from app.db.models.audit_log import AuditLog
from app.db.models.department import Department
from app.db.models.knowledge_base import KnowledgeBase, KnowledgeBaseMember
from app.schemas.department import DepartmentAdminUpdate, DepartmentCreate
from app.schemas.knowledge_base import KnowledgeBaseCreate
from app.services.department_service import create_department, delete_department, update_department_admin
from app.services.knowledge_base_service import create_knowledge_base
from helpers import create_user, isolated_session


class DepartmentServiceTests(unittest.TestCase):
    def test_create_department_records_audit_event(self) -> None:
        with isolated_session() as session:
            admin = create_user(session, "department-admin@example.com", "Department Admin")
            manager = create_user(session, "operations-manager@example.com", "Operations Manager")

            department = create_department(
                session,
                DepartmentCreate(name="Operations", description="Ops team", admin_user_id=manager.id),
                actor_user_id=admin.id,
            )

            audit_log = session.scalar(select(AuditLog).where(AuditLog.action == "department.create"))
            self.assertIsNotNone(audit_log)
            self.assertEqual(audit_log.actor_user_id, admin.id)
            self.assertEqual(audit_log.resource_type, "department")
            self.assertEqual(audit_log.resource_id, department.id)
            self.assertEqual(audit_log.outcome, "success")
            self.assertEqual(audit_log.extra_metadata["name"], "Operations")
            self.assertEqual(department.admin_user_id, manager.id)
            session.refresh(manager)
            self.assertEqual(manager.department_id, department.id)

    def test_duplicate_department_records_denied_audit_event(self) -> None:
        with isolated_session() as session:
            admin = create_user(session, "department-duplicate-admin@example.com", "Department Duplicate Admin")
            manager = create_user(session, "legal-manager@example.com", "Legal Manager")
            payload = DepartmentCreate(name="Legal", admin_user_id=manager.id)
            create_department(session, payload, actor_user_id=admin.id)

            with self.assertRaises(HTTPException) as error:
                create_department(session, payload, actor_user_id=admin.id)

            self.assertEqual(error.exception.status_code, 409)
            audit_logs = session.scalars(
                select(AuditLog)
                .where(AuditLog.action == "department.create")
            ).all()
            logs_by_outcome = {log.outcome: log for log in audit_logs}
            self.assertEqual(set(logs_by_outcome), {"success", "denied"})
            self.assertEqual(logs_by_outcome["denied"].detail, "Department name already exists")
            self.assertEqual(logs_by_outcome["denied"].extra_metadata["name"], "Legal")

    def test_system_admin_can_replace_department_admin_with_member_or_unassigned_user(self) -> None:
        with isolated_session() as session:
            actor = create_user(session, "department-actor@example.com", "Department Actor")
            first = create_user(session, "department-first@example.com", "First Manager")
            replacement = create_user(session, "department-replacement@example.com", "Replacement Manager")
            department = create_department(
                session,
                DepartmentCreate(name="Finance", admin_user_id=first.id),
                actor_user_id=actor.id,
            )
            knowledge_base = create_knowledge_base(
                session,
                first.id,
                KnowledgeBaseCreate(name="Finance Shared", visibility="department"),
            )

            updated = update_department_admin(
                session,
                department.id,
                DepartmentAdminUpdate(admin_user_id=replacement.id),
                actor_user_id=actor.id,
            )

            session.refresh(first)
            session.refresh(replacement)
            self.assertEqual(updated.admin_user_id, replacement.id)
            self.assertEqual(first.department_id, department.id)
            self.assertEqual(replacement.department_id, department.id)
            stored_knowledge_base = session.get(KnowledgeBase, knowledge_base.id)
            self.assertEqual(stored_knowledge_base.owner_id, replacement.id)
            memberships = session.scalars(
                select(KnowledgeBaseMember).where(
                    KnowledgeBaseMember.knowledge_base_id == knowledge_base.id
                )
            ).all()
            roles = {membership.user_id: membership.role for membership in memberships}
            self.assertEqual(roles[first.id], "viewer")
            self.assertEqual(roles[replacement.id], "owner")

    def test_system_admin_cannot_control_own_department_admin_assignment(self) -> None:
        with isolated_session() as session:
            actor = create_user(session, "self-department-admin@example.com", "Self Department Admin")
            backup = create_user(session, "department-backup@example.com", "Department Backup")
            manager = create_user(session, "department-manager@example.com", "Department Manager")

            with self.assertRaises(HTTPException) as create_error:
                create_department(
                    session,
                    DepartmentCreate(name="Self Managed", admin_user_id=actor.id),
                    actor_user_id=actor.id,
                )
            self.assertEqual(create_error.exception.status_code, 400)

            department = create_department(
                session,
                DepartmentCreate(name="Managed By Another", admin_user_id=actor.id),
                actor_user_id=backup.id,
            )
            with self.assertRaises(HTTPException) as delete_error:
                delete_department(session, department.id, actor_user_id=actor.id)
            with self.assertRaises(HTTPException) as handoff_error:
                update_department_admin(
                    session,
                    department.id,
                    DepartmentAdminUpdate(admin_user_id=manager.id),
                    actor_user_id=actor.id,
                )

            self.assertEqual(delete_error.exception.status_code, 400)
            self.assertEqual(handoff_error.exception.status_code, 400)
            session.refresh(actor)
            self.assertEqual(actor.department_id, department.id)
            self.assertEqual(department.admin_user_id, actor.id)

    def test_system_admin_can_delete_department_with_only_its_manager(self) -> None:
        with isolated_session() as session:
            actor = create_user(session, "delete-department-actor@example.com", "Delete Department Actor")
            manager = create_user(session, "delete-department-manager@example.com", "Delete Department Manager")
            department = create_department(
                session,
                DepartmentCreate(name="Temporary Department", admin_user_id=manager.id),
                actor_user_id=actor.id,
            )

            delete_department(session, department.id, actor_user_id=actor.id)

            self.assertIsNone(session.get(Department, department.id))
            session.refresh(manager)
            self.assertIsNone(manager.department_id)
            audit = session.scalar(
                select(AuditLog).where(
                    AuditLog.action == "department.delete",
                    AuditLog.resource_id == department.id,
                    AuditLog.outcome == "success",
                )
            )
            self.assertIsNotNone(audit)
            self.assertEqual(audit.extra_metadata["previous_admin_user_id"], manager.id)

    def test_department_with_members_or_knowledge_bases_cannot_be_deleted(self) -> None:
        with isolated_session() as session:
            actor = create_user(session, "guard-delete-actor@example.com", "Guard Delete Actor")
            manager = create_user(session, "guard-delete-manager@example.com", "Guard Delete Manager")
            member = create_user(session, "guard-delete-member@example.com", "Guard Delete Member")
            department = create_department(
                session,
                DepartmentCreate(name="Guarded Delete Department", admin_user_id=manager.id),
                actor_user_id=actor.id,
            )
            member.department_id = department.id
            session.add(member)
            session.commit()

            with self.assertRaises(HTTPException) as member_error:
                delete_department(session, department.id, actor_user_id=actor.id)
            self.assertEqual(member_error.exception.status_code, 409)

            member.department_id = None
            session.add(member)
            session.commit()
            create_knowledge_base(
                session,
                manager.id,
                KnowledgeBaseCreate(name="Guarded Department KB", visibility="department"),
            )

            with self.assertRaises(HTTPException) as knowledge_base_error:
                delete_department(session, department.id, actor_user_id=actor.id)

            self.assertEqual(knowledge_base_error.exception.status_code, 409)
            self.assertIsNotNone(session.get(Department, department.id))


if __name__ == "__main__":
    unittest.main()
