from __future__ import annotations

import unittest

from app.evaluation.p0_metrics import (
    answer_groups_match,
    binary_ndcg_at_k,
    citation_scores,
    normalized_token_f1,
    official_longmemeval_ndcg_at_k,
    official_rgb_answer_match,
    retrieval_all,
    retrieval_any,
    retrieval_recall,
)


class P0MetricsTests(unittest.TestCase):
    def test_answer_groups_require_one_match_from_every_group(self) -> None:
        ground_truth = [["January 2, 2022", "2 January 2022"], ["CNN"]]

        self.assertTrue(answer_groups_match("It aired on January 2, 2022 on CNN.", ground_truth))
        self.assertFalse(answer_groups_match("It aired on January 2, 2022.", ground_truth))

    def test_answer_groups_accept_equivalent_dates_spelling_and_middle_initials(self) -> None:
        self.assertTrue(answer_groups_match("The funeral was on 19 September at Westminster Abbey.", ["September 19"]))
        self.assertTrue(answer_groups_match("It was held at Shoreline Amphitheater.", ["Shoreline Amphitheatre"]))
        self.assertTrue(answer_groups_match("The actor is Jordan C. Reed.", ["Jordan Reed"]))
        self.assertFalse(answer_groups_match("It happened on September 20.", ["September 19"]))

    def test_official_rgb_match_remains_strict_substring_matching(self) -> None:
        self.assertTrue(official_rgb_answer_match("It aired on September 19.", ["September 19"]))
        self.assertFalse(official_rgb_answer_match("It aired on 19 September.", ["September 19"]))

    def test_citation_scores_reject_invalid_and_negative_documents(self) -> None:
        scores = citation_scores("Answer [2] [4] [9]", {2, 3}, document_count=4)

        self.assertEqual(scores["citation_count"], 3)
        self.assertEqual(scores["invalid_citation_count"], 1)
        self.assertEqual(scores["citation_precision"], 0.5)
        self.assertEqual(scores["citation_coverage"], 1.0)

    def test_citation_coverage_requires_each_evidence_group(self) -> None:
        scores = citation_scores(
            "First fact [1], second fact unsupported.",
            {1, 2, 3},
            document_count=4,
            positive_groups=[{1, 2}, {3}],
        )

        self.assertEqual(scores["citation_precision"], 1.0)
        self.assertEqual(scores["citation_coverage"], 0.5)
        self.assertEqual(scores["cited_positive_group_count"], 1)
        self.assertEqual(scores["required_positive_group_count"], 2)

    def test_normalized_token_f1_and_retrieval_recall(self) -> None:
        self.assertAlmostEqual(normalized_token_f1("GPS system failed", "GPS system"), 0.8)
        self.assertEqual(retrieval_recall(["a", "x"], ["a", "b"]), 0.5)
        self.assertTrue(retrieval_any(["a", "x"], ["a", "b"]))
        self.assertFalse(retrieval_all(["a", "x"], ["a", "b"]))
        self.assertAlmostEqual(binary_ndcg_at_k(["x", "a"], ["a"], 2), 1 / 1.584962500721156)
        self.assertEqual(official_longmemeval_ndcg_at_k(["x", "a"], ["a"], 2), 1.0)


if __name__ == "__main__":
    unittest.main()
