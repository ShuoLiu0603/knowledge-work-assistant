from __future__ import annotations

import unittest

from scripts.evaluate_rgb_reader import PASSAGE_COUNT, prepare_case
from scripts.evaluate_agent_trajectory import tool_sequence_metrics
from scripts.evaluate_lit_ragbench_reader import case_family, prepare_case as prepare_lit_case
from scripts.evaluate_longmemeval_memory import abstention_marker_correct


class RgbEvaluationProtocolTests(unittest.TestCase):
    def test_information_integration_uses_five_documents_and_covers_every_group(self) -> None:
        row = {
            "id": 1,
            "query": "Question",
            "answer": ["A", "B"],
            "positive": [["A1", "A2", "A3"], ["B1", "B2", "B3"]],
            "negative": ["N1", "N2", "N3"],
        }

        first = prepare_case("information_integration", row)
        second = prepare_case("information_integration", row)

        self.assertEqual(first, second)
        self.assertEqual(len(first["documents"]), PASSAGE_COUNT)
        self.assertEqual({document["positive_group"] for document in first["documents"]}, {0, 1})
        self.assertTrue(all(document["positive"] for document in first["documents"]))

    def test_counterfactual_protocol_never_exceeds_five_documents(self) -> None:
        row = {
            "id": 2,
            "query": "Question",
            "answer": "Answer",
            "positive_wrong": [f"Wrong {index}" for index in range(7)],
            "negative": ["Noise 1", "Noise 2"],
        }

        case = prepare_case("counterfactual_robustness", row)

        self.assertEqual(len(case["documents"]), PASSAGE_COUNT)
        self.assertTrue(all(document.get("counterfactual") for document in case["documents"]))


class AgentEvaluationProtocolTests(unittest.TestCase):
    def test_repeated_tool_is_not_multiset_or_count_correct(self) -> None:
        metrics = tool_sequence_metrics(["rag", "rag"], [["rag"]])

        self.assertTrue(metrics["tool_set_correct"])
        self.assertFalse(metrics["tool_multiset_correct"])
        self.assertFalse(metrics["tool_count_correct"])
        self.assertEqual(metrics["extra_tool_call_count"], 1)


class LongMemEvalProtocolTests(unittest.TestCase):
    def test_exact_insufficient_marker_is_a_supplementary_deterministic_metric(self) -> None:
        self.assertTrue(abstention_marker_correct({"abstention": True}, "INSUFFICIENT_MEMORY"))
        self.assertFalse(abstention_marker_correct({"abstention": False}, "INSUFFICIENT_MEMORY"))


class LitRagBenchProtocolTests(unittest.TestCase):
    def test_case_preparation_is_deterministic_and_preserves_evidence_labels(self) -> None:
        row = {
            "question": "What is the policy limit?",
            "answer": "100",
            "qa_type": ["I_multiple", "R_calculate"],
            "positive_chunk_list": [{"title": "Policy", "content": "The limit is 100."}],
            "negative_chunk_list": [{"title": "Noise", "content": "Unrelated."}],
            "reasoning_content": "Read the policy limit.",
        }

        first = prepare_lit_case(0, row, "generate", "judge")
        second = prepare_lit_case(0, row, "generate", "judge")

        self.assertEqual(first, second)
        self.assertEqual(first["family"], "integration")
        self.assertEqual(sum(document["positive"] for document in first["documents"]), 1)
        self.assertEqual(len(first["documents"]), 2)

    def test_abstention_family_takes_precedence(self) -> None:
        self.assertEqual(case_family(["A_conflicted", "R_multihop"]), "abstention")


if __name__ == "__main__":
    unittest.main()
