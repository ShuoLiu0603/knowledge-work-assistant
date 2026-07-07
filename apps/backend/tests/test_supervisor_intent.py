from __future__ import annotations

import unittest

from app.agents.supervisor import normalize_intent


class SupervisorIntentTests(unittest.TestCase):
    def test_greeting_misclassified_as_writing_is_downgraded_to_rag(self) -> None:
        self.assertEqual(normalize_intent("writing", "你好"), "rag")

    def test_explicit_writing_request_stays_writing(self) -> None:
        self.assertEqual(normalize_intent("writing", "帮我写一封会议通知"), "writing")

    def test_explicit_summary_request_stays_summary(self) -> None:
        self.assertEqual(normalize_intent("summary", "请总结这份制度"), "summary")

    def test_greeting_can_route_to_chat(self) -> None:
        self.assertEqual(normalize_intent("chat", "你好"), "chat")

    def test_memory_recall_routes_to_memory(self) -> None:
        self.assertEqual(normalize_intent("memory", "你记得我什么？"), "memory")

    def test_enterprise_question_overrides_memory_label(self) -> None:
        self.assertEqual(normalize_intent("memory", "我的报销政策是什么？"), "rag")


if __name__ == "__main__":
    unittest.main()
