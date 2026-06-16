from __future__ import annotations

import unittest

from job_hunter.sources.handshake import (
    _build_row,
    _dedupe_rows,
    _extract_cards_from_page_text,
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
        self.assertIn("Location: Lake Forest, CA", parsed["description"])

    def test_build_row_prefers_detail_fields(self) -> None:
        row = _build_row(CARD_TEXT, DETAIL_TEXT, "https://app.joinhandshake.com/job-search/example")
        self.assertIsNotNone(row)
        self.assertEqual(row["source"], "handshake")
        self.assertEqual(row["title"], "Software Intern")
        self.assertEqual(row["company"], "Advantest America, Inc.")
        self.assertIn("Location: Lake Forest, CA", row["description"])

    def test_relative_age_to_iso(self) -> None:
        self.assertIsNotNone(_relative_age_to_iso("Posted 4 days ago"))
        self.assertIsNotNone(_relative_age_to_iso("Lake Forest, CA · 4d ago"))

    def test_extract_cards_from_page_text(self) -> None:
        cards = _extract_cards_from_page_text(PAGE_TEXT)
        self.assertEqual(len(cards), 2)
        self.assertEqual(cards[0]["company"], "National Journal")
        self.assertEqual(cards[1]["title"], "Urology Sales Intern")

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


if __name__ == "__main__":
    unittest.main()
