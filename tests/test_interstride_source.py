from __future__ import annotations

import unittest

from job_hunter.sources.interstride import _build_row, _dedupe_rows, _search_payload


class InterstrideSourceTests(unittest.TestCase):
    def test_build_row_uses_structured_api_fields(self) -> None:
        row = _build_row(
            {
                "id": 456,
                "job_key": "job-456",
                "job_title": "Machine Learning Intern",
                "company": "Example Labs",
                "formatted_location_full": "Boston, MA",
                "date": "2026-07-20T00:00:00.000Z",
                "url": "https://careers.example.com/jobs/456",
                "ai_summary": "Build machine learning models and data pipelines with Python and SQL.",
                "visa_sponsorship": True,
                "source": "employer",
            },
            "https://student.interstride.com/jobs/search",
        )
        self.assertEqual(row["external_id"], "456")
        self.assertIn("data pipelines", row["description"])
        self.assertIn("Sponsorship available", row["description"])
        self.assertEqual(row["source_metadata"]["external_apply_url"], "https://careers.example.com/jobs/456")

    def test_search_payload_uses_keyword_query_parameter(self) -> None:
        payload = _search_payload("https://student.interstride.com/jobs/search?keyword=data%20engineer%20intern")
        self.assertEqual(payload["search"], "data engineer intern")
        self.assertEqual(payload["keyword"], "data engineer intern")
        self.assertEqual(payload["visa"], "all_sponsored_companies")
        self.assertEqual(payload["job_type"], ["internship"])

    def test_dedupe_rows_keeps_richest_aggregator_variant(self) -> None:
        rows = _dedupe_rows(
            [
                {"external_id": "one", "title": "AI Intern", "company": "Example", "location": "Remote", "posted_at": "2026-07-20", "description": "Short"},
                {"external_id": "two", "title": "AI Intern", "company": "Example", "location": "Remote", "posted_at": "2026-07-20", "description": "Longer Interstride summary"},
            ]
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["external_id"], "two")


if __name__ == "__main__":
    unittest.main()
