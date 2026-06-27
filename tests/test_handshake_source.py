from __future__ import annotations

import unittest

from job_hunter.sources.handshake import (
    _build_row,
    _dedupe_rows,
    _extract_cards_from_page_text,
    _normalize_search_url,
    _parse_card_text,
    _parse_detail_text,
    _relative_age_to_iso,
)


CARD_TEXT = """Advantest America, Inc.
Software Intern
$20–30/hr · Internship · Jul 6—Jul 5
Lake Forest, CA
4d ago
"""

DETAIL_TEXT = """Advantest America, Inc.
Computer Networking
Software Intern
Posted 4 days ago • Apply by July 12, 2026 at 1:59 AM
At a glance
$20–30/hr
Onsite, based in Lake Forest, CA
Internship
US work authorization required
Job description
Software Intern
Location: Lake Forest, CA (On-site, No Hybrid)
Company: Advantest Test Solutions (ATS)
"""

SIEMENS_STYLE_DETAIL_TEXT = """Siemens Digital Industries Software
Industrial Automation
Data Science Internship
Posted 5 days ago • Apply by July 12, 2026 at 1:59 AM
At a glance
$18–50/hr
Onsite, based in Pasadena, CA
Internship
Open to candidates with OPT/CPT
Legally authorized to work in the United States without the need for current or future sponsorship by the company
Job description
Siemens Digital Industries Software Strategic Student Program (SSP)
Discover your career with us at Siemens Digital Industries Software!
We are a leading global software company dedicated to the world of computer aided design, 3D
3.0 GPA Masters Statistics major Data Science major
About the employer
Siemens employer profile
"""

PAGE_TEXT = """Skip to content
Explore
Jobs
Search
Saved
678 jobs found
National Journal
Social Impact & Reputational Risk Intern
$20/hr · Internship · Jun 30—Aug 31
Washington, DC
∙
New
Teleflex
Urology Sales Intern
$18-24/hr · Internship · Jul 5—Aug 27
Morrisville, NC
∙
New
Jobs
National Journal
Social Impact & Reputational Risk Intern
"""

MONTH_CARD_TEXT = """Hyphenova
AI/ML Data Engineering Intern
$300-450/yr · Internship · May 14—Aug 30
Remote
1mo ago
"""

MONTH_DETAIL_TEXT = """Hyphenova
Movies, TV, Music, Gaming
AI/ML Data Engineering Intern
Posted 1 month ago∙Apply by June 25, 2026 at 10:59 PM
At a glance
$300-450/yr
Remote, based in United States
Internship
Job description
The Opportunity
"""

SUMMARY_BETA_DETAIL_TEXT = """Citizens for Responsibility and Ethics in Washington
Non-Profit - Other
Communications Intern
Posted 10 hours ago∙Apply by July 27, 2026 at 1:59 AM
Save
Share
Apply externally
Summary Beta
This role as a Data Engineer Intern aligns closely with the user's query
It focuses on hands-on experience with data management tools, which can enhance skills relevant to their future career goals in tech fields such as software development or data science
At a glance
$18/hr
Hybrid or onsite, based in Washington, DC
Internship
Open to candidates with OPT/CPT
Job description
The communications intern will assist in gaining coverage of CREW's fight for an ethical and transparent government.
About the employer
CREW
"""


class HandshakeSourceTests(unittest.TestCase):
    def test_parse_card_text(self) -> None:
        parsed = _parse_card_text(CARD_TEXT)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["company"], "Advantest America, Inc.")
        self.assertEqual(parsed["title"], "Software Intern")
        self.assertEqual(parsed["location"], "Lake Forest, CA")
        self.assertIsNotNone(parsed["posted_at"])

    def test_parse_detail_text(self) -> None:
        parsed = _parse_detail_text(DETAIL_TEXT)
        self.assertEqual(parsed["company"], "Advantest America, Inc.")
        self.assertEqual(parsed["title"], "Software Intern")
        self.assertIn("Lake Forest, CA", parsed["location"])
        self.assertIn("US work authorization required", parsed["description"])
        self.assertIn("Location: Lake Forest, CA", parsed["description"])

    def test_parse_detail_text_keeps_handshake_work_auth_lines(self) -> None:
        parsed = _parse_detail_text(SIEMENS_STYLE_DETAIL_TEXT)
        self.assertIn("Open to candidates with OPT/CPT", parsed["description"])
        self.assertIn(
            "Legally authorized to work in the United States without the need for current or future sponsorship by the company",
            parsed["description"],
        )
        self.assertNotIn("About the employer", parsed["description"])

    def test_build_row_prefers_detail_fields(self) -> None:
        row = _build_row(
            CARD_TEXT,
            DETAIL_TEXT,
            "https://app.joinhandshake.com/job-search/example",
            "https://app.joinhandshake.com/job-search/11120024?query=data+engineer+intern",
        )
        self.assertIsNotNone(row)
        self.assertEqual(row["source"], "handshake")
        self.assertEqual(row["title"], "Software Intern")
        self.assertEqual(row["company"], "Advantest America, Inc.")
        self.assertIn("Location: Lake Forest, CA", row["description"])
        self.assertEqual(
            row["url"],
            "https://app.joinhandshake.com/job-search/11120024?query=data+engineer+intern",
        )
        self.assertEqual(row["compensation_type"], "paid")

    def test_build_row_preserves_work_auth_language_for_pipeline(self) -> None:
        row = _build_row(
            CARD_TEXT,
            SIEMENS_STYLE_DETAIL_TEXT,
            "https://app.joinhandshake.com/job-search/example",
            "https://app.joinhandshake.com/job-search/11120024?query=data+engineer+intern",
        )
        self.assertIsNotNone(row)
        self.assertIn(
            "without the need for current or future sponsorship by the company",
            row["description"],
        )

    def test_relative_age_to_iso(self) -> None:
        self.assertIsNotNone(_relative_age_to_iso("Posted 4 days ago"))
        self.assertIsNotNone(_relative_age_to_iso("Lake Forest, CA · 4d ago"))
        self.assertIsNotNone(_relative_age_to_iso("Posted 1 month ago"))
        self.assertIsNotNone(_relative_age_to_iso("Remote · 4wk ago"))
        self.assertIsNotNone(_relative_age_to_iso("1mo ago"))

    def test_extract_cards_from_page_text(self) -> None:
        cards = _extract_cards_from_page_text(PAGE_TEXT)
        self.assertEqual(len(cards), 2)
        self.assertEqual(cards[0]["company"], "National Journal")
        self.assertEqual(cards[1]["title"], "Urology Sales Intern")

    def test_parse_month_based_card_text(self) -> None:
        parsed = _parse_card_text(MONTH_CARD_TEXT)
        self.assertIsNotNone(parsed)
        self.assertTrue(parsed["posted_at"].startswith("20"))

    def test_parse_month_based_detail_text(self) -> None:
        parsed = _parse_detail_text(MONTH_DETAIL_TEXT)
        self.assertTrue(parsed["posted_at"].startswith("20"))

    def test_parse_detail_text_strips_summary_beta_noise(self) -> None:
        parsed = _parse_detail_text(SUMMARY_BETA_DETAIL_TEXT)
        self.assertNotIn("Summary Beta", parsed["description"])
        self.assertNotIn("This role as a Data Engineer Intern aligns closely", parsed["description"])
        self.assertIn("The communications intern will assist", parsed["description"])

    def test_dedupe_rows_keeps_first_external_id(self) -> None:
        rows = [
            {"external_id": "a", "url": "u1", "title": "one"},
            {"external_id": "a", "url": "u2", "title": "two"},
            {"external_id": "b", "url": "u3", "title": "three"},
        ]
        self.assertEqual(
            _dedupe_rows(rows),
            [
                {"external_id": "a", "url": "u1", "title": "one"},
                {"external_id": "b", "url": "u3", "title": "three"},
            ],
        )

    def test_normalize_search_url_forces_newest_sort(self) -> None:
        url = (
            "https://app.joinhandshake.com/job-search/11120409"
            "?jobType=3&query=data&per_page=25&page=1"
        )
        normalized = _normalize_search_url(url)
        self.assertIn("sort=posted_date_desc", normalized)
        self.assertIn("query=data", normalized)
        self.assertIn("page=1", normalized)

    def test_normalize_search_url_replaces_existing_sort(self) -> None:
        url = (
            "https://app.joinhandshake.com/job-search/11120409"
            "?query=data&sort=relevance&page=1"
        )
        normalized = _normalize_search_url(url)
        self.assertIn("sort=posted_date_desc", normalized)
        self.assertNotIn("sort=relevance", normalized)


if __name__ == "__main__":
    unittest.main()
