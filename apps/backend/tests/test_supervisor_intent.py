from __future__ import annotations

import unittest

from app.agents.supervisor import normalize_intent
from app.llm.structured_outputs import IntentOutput


class SupervisorIntentTests(unittest.TestCase):
    def test_preserves_valid_llm_intent_labels(self) -> None:
        for label in ("rag", "memory", "chat", "summary", "writing"):
            with self.subTest(label=label):
                self.assertEqual(normalize_intent(label, "任意用户输入"), label)

    def test_text_does_not_override_llm_intent(self) -> None:
        self.assertEqual(normalize_intent("rag", "你好"), "rag")
        self.assertEqual(normalize_intent("rag", "帮我写一封会议通知"), "rag")
        self.assertEqual(normalize_intent("memory", "报销政策是什么？"), "memory")
        self.assertEqual(normalize_intent("chat", "报销政策是什么？"), "chat")

    def test_normalizes_common_non_enum_llm_outputs(self) -> None:
        self.assertEqual(normalize_intent("intent: summary", "请总结这份制度"), "summary")
        self.assertEqual(normalize_intent("写作", "帮我写一封会议通知"), "writing")
        self.assertEqual(normalize_intent("聊天", "你好"), "chat")
        self.assertEqual(normalize_intent("记忆", "你记得我什么？"), "memory")

    def test_invalid_llm_output_falls_back_to_rag(self) -> None:
        self.assertEqual(normalize_intent("", "你好"), "rag")
        self.assertEqual(normalize_intent("unknown", "帮我写一封会议通知"), "rag")

    def test_structured_intent_output_accepts_chinese_labels(self) -> None:
        self.assertEqual(IntentOutput.model_validate({"intent": "总结"}).intent, "summary")
        self.assertEqual(IntentOutput.model_validate({"intent": "写作"}).intent, "writing")
        self.assertEqual(IntentOutput.model_validate({"intent": "聊天"}).intent, "chat")
        self.assertEqual(IntentOutput.model_validate({"intent": "记忆"}).intent, "memory")


if __name__ == "__main__":
    unittest.main()
