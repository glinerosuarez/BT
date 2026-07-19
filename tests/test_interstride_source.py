from __future__ import annotations

import unittest

from job_hunter.sources.interstride import _build_row, _parse_card, _relative_age_to_iso


class InterstrideSourceTests(unittest.TestCase):
    def test_parse_card(self) -> None:
        card = _parse_card(
            """Data Engineering Intern
Example Labs
Remote
Posted 6 hours ago
""",
            "/jobs/detail/job-123",
        )
        self.assertEqual(card["title"], "Data Engineering Intern")
        self.assertEqual(card["company"], "Example Labs")
        self.assertEqual(card["location"], "Remote")
        self.assertTrue(card["posted_at"])
        self.assertEqual(card["url"], "https://student.interstride.com/jobs/detail/job-123")

    def test_build_row_uses_detail_description_and_external_apply_url(self) -> None:
        card = _parse_card(
            """Machine Learning Intern
Example Labs
Boston, MA
Posted 1 day ago
""",
            "/jobs/detail/job-456",
        )
        row = _build_row(
            card,
            """Machine Learning Intern
Job Description
Build machine learning models and data pipelines with Python and SQL.
Collaborate with the engineering team to deploy reliable systems.
Benefits
Medical coverage
""",
            "https://student.interstride.com/jobs/search",
            True,
            "https://careers.example.com/jobs/456",
        )
        self.assertEqual(row["external_id"], "job-456")
        self.assertIn("data pipelines", row["description"])
        self.assertNotIn("Medical coverage", row["description"])
        self.assertEqual(row["source_metadata"]["external_apply_url"], "https://careers.example.com/jobs/456")

    def test_relative_age_parser(self) -> None:
        self.assertIsNotNone(_relative_age_to_iso("Posted 3 hours ago"))
        self.assertIsNotNone(_relative_age_to_iso("2 weeks ago"))


if __name__ == "__main__":
    unittest.main()
