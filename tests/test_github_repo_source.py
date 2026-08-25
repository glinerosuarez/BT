from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from job_hunter.sources.github_repo import (
    GithubRepoSource,
    _extract_url,
    _is_generic_listing_url,
    _is_within_lookback,
    _normalize_posted_at,
    _parse_markdown_table,
)


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

    @patch("job_hunter.sources.github_repo.urllib.request.urlopen")
    @patch("job_hunter.sources.github_repo._fetch_detail_text")
    def test_fetch_uses_static_detail_when_available(self, mock_fetch_detail, mock_urlopen) -> None:
        mock_resp = MagicMock()
        mock_resp.read.return_value = SAMPLE_MARKDOWN.encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp
        mock_fetch_detail.return_value = "Detailed job description for software engineering internship. " * 10

        source = GithubRepoSource(
            readme_urls=["https://example.com/README.md"],
            max_posting_age_days=365,
            enable_browser_fallback=False,
        )
        results = source.fetch(timeout_seconds=5)
        self.assertEqual(len(results), 3)
        self.assertEqual(results[0]["source_metadata"]["detail_quality_status"], "detail_complete")
        self.assertEqual(results[0]["source_metadata"]["description_provenance"], "github_repo_detail")
        self.assertIn("Detailed job description", results[0]["description"])

    @patch("job_hunter.sources.github_repo.urllib.request.urlopen")
    @patch("job_hunter.sources.github_repo._fetch_detail_text")
    @patch("job_hunter.sources.github_repo._BrowserDetailFetcher")
    def test_fetch_falls_back_to_browser_when_static_fetch_empty(
        self, mock_browser_class, mock_fetch_detail, mock_urlopen
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.read.return_value = SAMPLE_MARKDOWN.encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp
        mock_fetch_detail.return_value = ""

        mock_browser_instance = MagicMock()
        mock_browser_instance.fetch_text.return_value = "Browser-rendered Workday job description content. " * 10
        mock_browser_class.return_value = mock_browser_instance

        source = GithubRepoSource(
            readme_urls=["https://example.com/README.md"],
            max_posting_age_days=365,
            enable_browser_fallback=True,
        )
        results = source.fetch(timeout_seconds=5)
        self.assertEqual(len(results), 3)
        self.assertEqual(results[0]["source_metadata"]["detail_quality_status"], "detail_complete")
        self.assertEqual(results[0]["source_metadata"]["description_provenance"], "github_repo_browser_detail")
        self.assertIn("Browser-rendered Workday", results[0]["description"])
        mock_browser_instance.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()

