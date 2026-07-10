from __future__ import annotations

import unittest

from fastapi import HTTPException

from app.db.models.llm_call_log import LlmCallLog
from app.services.llm_log_service import get_llm_call_log, list_llm_call_logs
from helpers import create_user, isolated_session


class LlmLogServiceTests(unittest.TestCase):
    def test_user_llm_log_list_excludes_other_users_and_global_logs(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "llm-log-user@example.com", "LLM Log User")
            other = create_user(session, "llm-log-other@example.com", "LLM Log Other")
            own_log = LlmCallLog(
                user_id=user.id,
                agent_name="rag_agent",
                provider="local",
                model_name="local-rule-based",
                status="success",
            )
            other_log = LlmCallLog(
                user_id=other.id,
                agent_name="rag_agent",
                provider="local",
                model_name="local-rule-based",
                status="success",
            )
            global_log = LlmCallLog(
                user_id=None,
                agent_name="startup",
                provider="local",
                model_name="local-rule-based",
                status="failed",
                error_message="global provider diagnostic",
            )
            session.add_all([own_log, other_log, global_log])
            session.commit()

            logs = list_llm_call_logs(session, user.id)

            self.assertEqual([log.id for log in logs], [own_log.id])

    def test_user_cannot_read_global_llm_log_by_id(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "llm-log-global-reader@example.com", "LLM Log Global Reader")
            global_log = LlmCallLog(
                user_id=None,
                agent_name="startup",
                provider="local",
                model_name="local-rule-based",
                status="failed",
                error_message="global provider diagnostic",
            )
            session.add(global_log)
            session.commit()

            with self.assertRaises(HTTPException) as error:
                get_llm_call_log(session, user.id, global_log.id)

            self.assertEqual(error.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
