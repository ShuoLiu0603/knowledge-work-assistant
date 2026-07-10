from __future__ import annotations

import unittest

from fastapi import HTTPException
from sqlalchemy import select

from app.db.models.audit_log import AuditLog
from app.schemas.department import DepartmentCreate
from app.services.department_service import create_department
from helpers import create_user, isolated_session


class DepartmentServiceTests(unittest.TestCase):
    def test_create_department_records_audit_event(self) -> None:
        with isolated_session() as session:
            admin = create_user(session, "department-admin@example.com", "Department Admin")

            department = create_department(
                session,
                DepartmentCreate(name="Operations", description="Ops team"),
                actor_user_id=admin.id,
            )

            audit_log = session.scalar(select(AuditLog).where(AuditLog.action == "department.create"))
            self.assertIsNotNone(audit_log)
            self.assertEqual(audit_log.actor_user_id, admin.id)
            self.assertEqual(audit_log.resource_type, "department")
            self.assertEqual(audit_log.resource_id, department.id)
            self.assertEqual(audit_log.outcome, "success")
            self.assertEqual(audit_log.extra_metadata["name"], "Operations")

    def test_duplicate_department_records_denied_audit_event(self) -> None:
        with isolated_session() as session:
            admin = create_user(session, "department-duplicate-admin@example.com", "Department Duplicate Admin")
            create_department(session, DepartmentCreate(name="Legal"), actor_user_id=admin.id)

            with self.assertRaises(HTTPException) as error:
                create_department(session, DepartmentCreate(name="Legal"), actor_user_id=admin.id)

            self.assertEqual(error.exception.status_code, 409)
            audit_logs = session.scalars(
                select(AuditLog)
                .where(AuditLog.action == "department.create")
            ).all()
            logs_by_outcome = {log.outcome: log for log in audit_logs}
            self.assertEqual(set(logs_by_outcome), {"success", "denied"})
            self.assertEqual(logs_by_outcome["denied"].detail, "Department name already exists")
            self.assertEqual(logs_by_outcome["denied"].extra_metadata["name"], "Legal")


if __name__ == "__main__":
    unittest.main()
