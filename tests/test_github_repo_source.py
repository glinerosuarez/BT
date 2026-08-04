from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from job_hunter.sources.github_repo import _extract_url, _is_generic_listing_url, _is_within_lookback, _normalize_posted_at, _parse_markdown_table


SAMPLE_MARKDOWN = """
# Summer 2026

| Company | Role | Location | Application/Link | Date Posted |
| ------- | ---- | -------- | ---------------- | ----------- |
| Apple | Software Engineer Intern, Undergrad | United States | [Apply](https://example.com/apple-undergrad) | May 22 |
| ↳ | Software Engineering Intern, Masters | United States | [Apply](https://example.com/apple-masters) | May 22 |
| Salesforce | Software Engineer Intern(Futureforce Summer 2027) | **5 locations**San Francisco, CA
Palo Alto, CA
New York, NY | [Apply](https://example.com/salesforce) | May 09 |
[⬆️ Back to Top ⬆️](https://example.com/top)
"""


class GithubRepoSourceTests(unittest.TestCase):
    def test_parse_markdown_table_handles_multiline_rows_and_company_carry(self) -> None:
        rows = _parse_markdown_table(SAMPLE_MARKDOWN)

        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["company"], "Apple")
        self.assertEqual(rows[1]["company"], "Apple")
        self.assertEqual(rows[1]["role"], "Software Engineering Intern, Masters")
        self.assertEqual(rows[2]["company"], "Salesforce")
        self.assertEqual(rows[2]["application_url"], "https://example.com/salesforce")
        self.assertIn("Palo Alto, CA", rows[2]["location"])

    def test_normalize_posted_at_infers_current_year(self) -> None:
        posted_at = _normalize_posted_at("May 22")
        self.assertIsNotNone(posted_at)
        self.assertTrue(str(posted_at).startswith("2026-05-22"))

    def test_extract_url_removes_html_appended_after_markdown_link(self) -> None:
        url = _extract_url(
            '[Apply](https://www.janestreet.com/join-jane-street/position/8611307002/?utm_source=github-vansh-ouckah"><img)'
        )
        self.assertEqual(
            url,
            "https://www.janestreet.com/join-jane-street/position/8611307002/?utm_source=github-vansh-ouckah",
        )

    def test_lookback_excludes_stale_repository_rows_before_detail_fetch(self) -> None:
        now = datetime.now(timezone.utc)
        stale = (now - timedelta(days=8)).isoformat()
        recent = (now - timedelta(days=1)).isoformat()
        self.assertFalse(_is_within_lookback(stale, max_posting_age_days=7))
        self.assertTrue(_is_within_lookback(recent, max_posting_age_days=7))

    def test_generic_microsoft_careers_search_url_is_not_a_job_link(self) -> None:
        self.assertTrue(
            _is_generic_listing_url(
                "https://apply.careers.microsoft.com/careers?query=intern&start=0&location=united+states"
            )
        )
        self.assertFalse(
            _is_generic_listing_url(
                "https://apply.careers.microsoft.com/careers/job/1970393556951950?utm_source=linkedin"
            )
        )


if __name__ == "__main__":
    unittest.main()
