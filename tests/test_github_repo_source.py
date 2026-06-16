from __future__ import annotations

import unittest

from job_hunter.sources.github_repo import _normalize_posted_at, _parse_markdown_table


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


if __name__ == "__main__":
    unittest.main()
