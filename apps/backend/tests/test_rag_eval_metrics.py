from __future__ import annotations

import unittest

from app.evaluation.metrics import compute_metrics


class RagEvalMetricTests(unittest.TestCase):
    def test_compute_metrics_for_recall_mrr_and_citation_hit_rate(self) -> None:
        cases = [
            {
                "id": "travel",
                "question": "What is the hotel reimbursement limit?",
                "expected_sources": ["company_policy_demo.md"],
                "expected_keywords": ["hotel"],
            },
            {
                "id": "invoice",
                "question": "What invoice fields are required?",
                "expected_sources": ["invoice_policy.md"],
                "expected_keywords": ["invoice"],
            },
        ]
        results = [
            {
                "answer": "The hotel reimbursement limit is 600 CNY.",
                "citations": [
                    {"file_name": "company_policy_demo.md", "chunk_id": "c1", "document_id": "d1", "content_preview": "hotel limit"},
                ],
            },
            {
                "answer": "No citation hit here.",
                "citations": [
                    {"file_name": "other.md", "chunk_id": "c2", "document_id": "d2", "content_preview": "other"},
                ],
            },
        ]

        report = compute_metrics(cases, results, top_k=3)

        self.assertEqual(report["total"], 2)
        self.assertEqual(report["recall_at_k"], 0.5)
        self.assertEqual(report["mrr"], 0.5)
        self.assertEqual(report["citation_hit_rate"], 0.5)
        self.assertEqual(report["answer_keyword_hit_rate"], 0.5)


if __name__ == "__main__":
    unittest.main()
