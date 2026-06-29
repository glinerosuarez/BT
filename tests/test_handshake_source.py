from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from job_hunter.sources.handshake import (
    _build_row,
    _build_row_from_job_page,
    _discover_card_url,
    _dedupe_rows,
    _extract_cards_from_page_text,
    _is_card_older_than_lookback,
    _job_search_url_to_jobs_url,
    _normalize_title_token,
    _normalize_search_url,
    _partition_handshake_urls,
    _page_body_has_security_verification,
    _parse_card_text,
    _parse_detail_text,
    _relative_age_to_iso,
    _resolve_job_url,
    _select_best_card_url,
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

INLINE_SUMMARY_BETA_DETAIL_TEXT = """CRH Construction Commercialization Intern Posted 2 days ago∙Apply by August 8, 2026 at 10:59 PM Save Share Apply externally Summary Beta This job posting describes a data engineering intern position, which aligns well with the user's interest in learning about data-related roles. It highlights key responsibilities such as supporting commercialization efforts, developing reports, and maintaining documentation. At a glance $22–31/hr Remote Work from home Internship Part-time∙35 hours a week∙From September 1, 2026 to December 18, 2026 US work authorization required Eligible for visa sponsorship and open to candidates with OPT/CPT Job description CRH is a leading global diversified building materials group. Position Overview: As a Commercialization Intern (Americas) you will support various commercialization efforts within the Americas Solutions Group."""

PRESTO_BODY_TEXT = """Skip to content
Explore
Jobs
Inbox
Feed
AI showcase
Events
People
Employers
Career center
AI work
Get the app
28
Presto
Internet & Software
Engineering Intern
Posted 5 days ago∙Apply by July 23, 2026 at 10:59 PM
Save
Share
Apply
At a glance
$16–23/hr
Remote, based in United States
Work from home
Internship
Full-time∙From August 3, 2026 to December 4, 2026
US work authorization required
Open to candidates with OPT/CPT
Job description
AI Engineering Intern, Voice & LLM Systems
About Presto Phoenix, Inc.
Presto is the leading Voice AI company for restaurant drive-thrus.
"""

POLLUTED_TITLE_DETAIL_TEXT = """Citizens for Responsibility and Ethics in Washington
Non-Profit - Other
Citizens for Responsibility and Ethics in Washington (CREW) is seeking a paid full-time or part-time communications intern. As a nonpartisan nonprofit government watchdog, CREW is dedicated to rooting out government corruption and fighting the influence of money in politics through legal action and bold communications grounded by in-depth research.
Posted 2 days ago∙Apply by July 27, 2026 at 1:59 AM
Save Share Apply externally Summary Beta This role as a Data Engineer Intern aligns closely with the user's interest in data.
At a glance
$18/hr
Hybrid or onsite, based in Washington, DC
Internship
Open to candidates with OPT/CPT
Job description
The communications intern will assist in gaining coverage of CREW's fight for an ethical and transparent government.
"""


class HandshakeSourceTests(unittest.TestCase):
    def test_resolve_job_url_prefers_direct_jobs_link(self) -> None:
        class FakeLocator:
            def evaluate_all(self, script, arg):
                _ = script
                self._arg = arg
                return [
                    {
                        "text": "Summer Business Analyst Intern, Advanced Degree",
                        "href": "https://app.joinhandshake.com/jobs/11159981?searchId=abc",
                    }
                ]

        class FakePage:
            def locator(self, selector):
                self._selector = selector
                return FakeLocator()

        page = FakePage()
        resolved = _resolve_job_url(
            page,
            "Summer Business Analyst Intern, Advanced Degree",
            "https://app.joinhandshake.com/job-search/11159981?query=data+engineer+intern",
        )
        self.assertEqual(resolved, "https://app.joinhandshake.com/jobs/11159981?searchId=abc")

    def test_resolve_job_url_falls_back_when_direct_link_missing(self) -> None:
        class FakeLocator:
            def evaluate_all(self, script, arg):
                _ = script
                _ = arg
                return []

        class FakePage:
            def locator(self, selector):
                self._selector = selector
                return FakeLocator()

        fallback = "https://app.joinhandshake.com/job-search/11159981?query=data+engineer+intern"
        resolved = _resolve_job_url(FakePage(), "Software Intern", fallback)
        self.assertEqual(resolved, "https://app.joinhandshake.com/jobs/11159981")

    def test_select_best_card_url_prefers_exact_title_match(self) -> None:
        candidates = [
            {
                "text": "Software Engineering Intern",
                "href": "https://app.joinhandshake.com/jobs/111",
            },
            {
                "text": "AI & Data Scientist Intern - Fall 2026",
                "href": "https://app.joinhandshake.com/jobs/222",
            },
        ]
        resolved = _select_best_card_url(candidates, "AI & Data Scientist Intern - Fall 2026")
        self.assertEqual(resolved, "https://app.joinhandshake.com/jobs/222")

    def test_discover_card_url_reads_job_link_candidates(self) -> None:
        class FakeLocator:
            def evaluate_all(self, script, arg):
                _ = script, arg
                return [
                    {
                        "text": "Other Role",
                        "href": "https://app.joinhandshake.com/jobs/111",
                    },
                    {
                        "text": "AI & Data Scientist Intern - Fall 2026",
                        "href": "https://app.joinhandshake.com/job-search/11159961?page=1",
                    },
                ]

        class FakePage:
            def locator(self, selector):
                self._selector = selector
                return FakeLocator()

        resolved = _discover_card_url(FakePage(), "AI & Data Scientist Intern - Fall 2026")
        self.assertEqual(resolved, "https://app.joinhandshake.com/job-search/11159961?page=1")

    def test_job_search_url_to_jobs_url(self) -> None:
        self.assertEqual(
            _job_search_url_to_jobs_url(
                "https://app.joinhandshake.com/job-search/11159981?query=data+engineer+intern&page=1"
            ),
            "https://app.joinhandshake.com/jobs/11159981",
        )
        self.assertEqual(_job_search_url_to_jobs_url("https://app.joinhandshake.com/job-search"), "")

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
            detail_fetch_attempted=True,
            detail_click_succeeded=True,
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
        self.assertEqual(row["source_metadata"]["detail_quality_status"], "detail_complete")
        self.assertTrue(row["source_metadata"]["detail_contains_job_description"])

    def test_build_row_preserves_work_auth_language_for_pipeline(self) -> None:
        row = _build_row(
            CARD_TEXT,
            SIEMENS_STYLE_DETAIL_TEXT,
            "https://app.joinhandshake.com/job-search/example",
            "https://app.joinhandshake.com/job-search/11120024?query=data+engineer+intern",
            detail_fetch_attempted=True,
            detail_click_succeeded=True,
        )
        self.assertIsNotNone(row)
        self.assertIn(
            "without the need for current or future sponsorship by the company",
            row["description"],
        )

    def test_build_row_marks_card_only_when_detail_missing(self) -> None:
        row = _build_row(
            CARD_TEXT,
            "",
            "https://app.joinhandshake.com/job-search/example",
            "https://app.joinhandshake.com/job-search/11120024?query=data+engineer+intern",
            detail_fetch_attempted=True,
            detail_click_succeeded=False,
        )
        self.assertIsNotNone(row)
        self.assertEqual(row["source_metadata"]["detail_quality_status"], "card_only")
        self.assertEqual(row["source_metadata"]["detail_fallback_reason"], "missing_detail_text")

    def test_build_row_cleans_line_delimited_summary_beta_pollution(self) -> None:
        row = _build_row(
            CARD_TEXT,
            SUMMARY_BETA_DETAIL_TEXT,
            "https://app.joinhandshake.com/job-search/example",
            "https://app.joinhandshake.com/job-search/11120024?query=data+engineer+intern",
            detail_fetch_attempted=True,
            detail_click_succeeded=True,
        )
        self.assertIsNotNone(row)
        self.assertNotIn("Summary Beta", row["description"])
        self.assertNotIn("This role as a Data Engineer Intern aligns closely", row["description"])
        self.assertIn("The communications intern will assist", row["description"])

    def test_build_row_strips_inline_summary_beta_pollution(self) -> None:
        row = _build_row(
            CARD_TEXT,
            INLINE_SUMMARY_BETA_DETAIL_TEXT,
            "https://app.joinhandshake.com/job-search/example",
            "https://app.joinhandshake.com/job-search/11161752?query=data+engineer+intern",
            detail_fetch_attempted=True,
            detail_click_succeeded=True,
        )
        self.assertIsNotNone(row)
        self.assertNotIn("Summary Beta", row["description"])
        self.assertNotIn("This job posting describes a data engineering intern position", row["description"])
        self.assertIn("Position Overview: As a Commercialization Intern", row["description"])

    def test_build_row_recovers_polluted_title_from_card_title(self) -> None:
        row = _build_row(
            "Citizens for Responsibility and Ethics in Washington\nCommunications Intern\n$18/hr · Internship\nWashington, DC\n2d ago\n",
            POLLUTED_TITLE_DETAIL_TEXT,
            "https://app.joinhandshake.com/job-search/example",
            "https://app.joinhandshake.com/jobs/11162812",
            detail_fetch_attempted=True,
            detail_click_succeeded=True,
        )
        self.assertIsNotNone(row)
        self.assertEqual(row["title"], "Communications Intern")
        self.assertEqual(row["source_metadata"]["detail_quality_status"], "detail_complete")
        self.assertEqual(row["source_metadata"]["detail_fallback_reason"], "")
        self.assertTrue(row["source_metadata"]["detail_title_polluted"])
        self.assertTrue(row["source_metadata"]["detail_title_recovered_from_card"])
        self.assertNotIn("Summary Beta", row["description"])
        self.assertIn("The communications intern will assist", row["description"])

    def test_build_row_from_job_page_supports_direct_urls(self) -> None:
        row = _build_row_from_job_page(
            detail_text=DETAIL_TEXT,
            job_url="https://app.joinhandshake.com/jobs/11120024",
            detail_fetch_attempted=True,
            detail_click_succeeded=True,
        )
        self.assertIsNotNone(row)
        self.assertEqual(row["url"], "https://app.joinhandshake.com/jobs/11120024")
        self.assertEqual(row["source_detail"], "https://app.joinhandshake.com/jobs/11120024")
        self.assertEqual(row["source_metadata"]["detail_quality_status"], "detail_complete")
        self.assertIn("Location: Lake Forest, CA", row["description"])

    def test_build_row_from_job_page_supports_full_body_text(self) -> None:
        body_text = "\n".join(
            [
                "Skip to content",
                "Jobs",
                DETAIL_TEXT,
                "About the employer",
                "Advantest employer profile",
            ]
        )
        row = _build_row_from_job_page(
            detail_text=body_text,
            job_url="https://app.joinhandshake.com/jobs/11120024",
            detail_fetch_attempted=True,
            detail_click_succeeded=True,
        )
        self.assertIsNotNone(row)
        self.assertEqual(row["source_metadata"]["detail_quality_status"], "detail_complete")
        self.assertEqual(row["company"], "Advantest America, Inc.")
        self.assertNotIn("Skip to content", row["description"])
        self.assertNotIn("About the employer", row["description"])

    def test_build_row_from_job_page_prefers_specific_job_description_title(self) -> None:
        row = _build_row_from_job_page(
            detail_text=PRESTO_BODY_TEXT,
            job_url="https://app.joinhandshake.com/jobs/11149721",
            detail_fetch_attempted=True,
            detail_click_succeeded=True,
        )
        self.assertIsNotNone(row)
        self.assertEqual(row["company"], "Presto")
        self.assertEqual(row["title"], "AI Engineering Intern, Voice & LLM Systems")
        self.assertNotIn("Skip to content", row["description"])

    def test_build_row_from_job_page_strips_inline_summary_beta_after_actions(self) -> None:
        body_text = "\n".join(
            [
                "CRH",
                "Construction",
                "Commercialization Intern",
                "Posted 2 days ago∙Apply by August 8, 2026 at 10:59 PM",
                "Save Share Apply externally Summary Beta This job posting describes a data engineering intern position, which aligns well with the user's interest in learning about data-related roles.",
                "At a glance",
                "$22–31/hr",
                "Remote",
                "Internship",
                "Job description",
                "Position Overview: As a Commercialization Intern (Americas) you will support various commercialization efforts within the Americas Solutions Group.",
            ]
        )
        row = _build_row_from_job_page(
            detail_text=body_text,
            job_url="https://app.joinhandshake.com/jobs/11161752",
            detail_fetch_attempted=True,
            detail_click_succeeded=True,
        )
        self.assertIsNotNone(row)
        self.assertNotIn("Summary Beta", row["description"])
        self.assertNotIn("This job posting describes a data engineering intern position", row["description"])
        self.assertIn("Position Overview: As a Commercialization Intern", row["description"])

    def test_partition_handshake_urls_separates_search_and_job_urls(self) -> None:
        search_urls, job_urls = _partition_handshake_urls(
            [
                "https://app.joinhandshake.com/job-search/11120409?query=data",
                "https://app.joinhandshake.com/jobs/11120024",
            ]
        )
        self.assertEqual(search_urls, ["https://app.joinhandshake.com/job-search/11120409?query=data"])
        self.assertEqual(job_urls, ["https://app.joinhandshake.com/jobs/11120024"])

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

    def test_card_older_than_lookback(self) -> None:
        card = {
            "company": "Example",
            "title": "Old Intern",
            "meta": "$20/hr · Internship",
            "location": "Remote",
            "freshness": "2wk ago",
        }
        self.assertTrue(_is_card_older_than_lookback(card, 7))

    def test_card_within_lookback(self) -> None:
        card = {
            "company": "Example",
            "title": "Fresh Intern",
            "meta": "$20/hr · Internship",
            "location": "Remote",
            "freshness": "4d ago",
        }
        self.assertFalse(_is_card_older_than_lookback(card, 7))

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

    def test_page_body_has_security_verification_true(self) -> None:
        class FakeBodyLocator:
            def inner_text(self):
                return (
                    "Performing security verification\n"
                    "This website uses a security service to protect against malicious bots.\n"
                    "Performance and Security by Cloudflare"
                )

        class FakePage:
            def locator(self, selector):
                self._selector = selector
                return FakeBodyLocator()

        self.assertTrue(_page_body_has_security_verification(FakePage()))

    def test_page_body_has_security_verification_false(self) -> None:
        class FakeBodyLocator:
            def inner_text(self):
                return "Jobs\n123 jobs found\nData Engineer Intern"

        class FakePage:
            def locator(self, selector):
                self._selector = selector
                return FakeBodyLocator()

        self.assertFalse(_page_body_has_security_verification(FakePage()))


if __name__ == "__main__":
    unittest.main()
