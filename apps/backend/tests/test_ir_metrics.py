from __future__ import annotations

import unittest

from app.evaluation.ir_metrics import compute_ir_metrics


class IrMetricsTests(unittest.TestCase):
    def test_compute_ir_metrics_uses_macro_averages_and_graded_ndcg(self) -> None:
        metrics = compute_ir_metrics(
            {
                "q1": {"a": 2, "b": 1},
                "q2": {"c": 1},
            },
            {
                "q1": ["b", "x", "a"],
                "q2": ["x", "c", "y"],
            },
            cutoffs=(1, 3),
        )

        self.assertEqual(metrics["query_count"], 2)
        self.assertEqual(metrics["Recall@1"], 0.25)
        self.assertEqual(metrics["Precision@1"], 0.5)
        self.assertEqual(metrics["MRR@1"], 0.5)
        self.assertEqual(metrics["MAP@1"], 0.5)
        self.assertEqual(metrics["Recall@3"], 1.0)
        self.assertEqual(metrics["Precision@3"], 0.5)
        self.assertEqual(metrics["MRR@3"], 0.75)
        self.assertAlmostEqual(float(metrics["MAP@3"]), 0.666667, places=6)
        self.assertAlmostEqual(float(metrics["nDCG@1"]), 1 / 6, places=6)
        self.assertAlmostEqual(float(metrics["nDCG@3"]), 0.659729, places=6)

    def test_compute_ir_metrics_deduplicates_ranked_documents(self) -> None:
        metrics = compute_ir_metrics(
            {"q": {"a": 1, "b": 1}},
            {"q": ["a", "a", "b"]},
            cutoffs=(2,),
        )

        self.assertEqual(metrics["Recall@2"], 1.0)
        self.assertEqual(metrics["Precision@2"], 1.0)

    def test_compute_ir_metrics_rejects_empty_positive_qrels(self) -> None:
        with self.assertRaises(ValueError):
            compute_ir_metrics({"q": {"a": 0}}, {"q": ["a"]})


if __name__ == "__main__":
    unittest.main()
