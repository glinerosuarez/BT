from __future__ import annotations

import json
import unittest

from job_hunter.sources.hiring_cafe import _build_row, _extract_hits


class HiringCafeSourceTests(unittest.TestCase):
    def test_extract_hits_reads_next_data_payload(self) -> None:
        payload = {"props": {"pageProps": {"ssrHits": [{"id": "one"}]}}}
        document = '<script id="__NEXT_DATA__" type="application/json">' + json.dumps(payload) + "</script>"
        self.assertEqual(_extract_hits(document), [{"id": "one"}])

    def test_build_row_uses_direct_apply_url_and_structured_gate_text(self) -> None:
        row = _build_row(
            {
                "objectID": "grnhse___example___123",
                "source": "grnhse",
                "apply_url": "https://jobs.example.com/123",
                "job_information": {"title": "Data Engineering Intern"},
                "v5_processed_job_data": {
                    "company_name": "Example",
                    "requirements_summary": "Pursuing a CS degree with Python and SQL.",
                    "technical_tools": ["Python", "SQL"],
                    "commitment": ["Internship"],
                    "formatted_workplace_location": "Seattle, Washington, United States",
                    "estimated_publish_date": "2026-07-26T12:00:00.000Z",
                    "visa_sponsorship": False,
                },
            },
            "https://hiring.cafe/jobs/data-engineer-intern-united-states",
        )
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["url"], "https://jobs.example.com/123")
        self.assertEqual(row["source_metadata"]["external_apply_url"], "https://jobs.example.com/123")
        self.assertIn("Commitment: Internship", row["description"])
        self.assertIn("Visa sponsorship is not available.", row["description"])
        self.assertEqual(row["skills"], ["Python", "SQL"])

    def test_build_row_skips_pinned_and_expired_results(self) -> None:
        self.assertIsNone(_build_row({"is_hc_pinned": True}, "https://hiring.cafe/jobs/test"))
        self.assertIsNone(_build_row({"is_expired": True}, "https://hiring.cafe/jobs/test"))


if __name__ == "__main__":
    unittest.main()
