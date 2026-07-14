from __future__ import annotations

import unittest

from job_hunter.sources.linkedin import (
    _build_row,
    _canonical_linkedin_job_url,
    _is_card_older_than_lookback,
    _normalize_search_url,
    _parse_card_text,
    _parse_detail_text,
    _partition_linkedin_urls,
    _relative_age_to_iso,
)


CARD_TEXT = """Machine Learning Scientist Intern
LinkedIn
Mountain View, CA
Reposted 2 days ago
"""

COMPANY_FIRST_CARD_TEXT = """DoorDash
Machine Learning Engineer, Marketplace Optimization
Sunnyvale, CA
Compartido hace 1 día
"""

VERIFIED_DUPLICATE_CARD_TEXT = """Machine Learning Applied Researcher (Empleo verificado)
Machine Learning Applied Researcher
Archetype AI
San Mateo, CA (Híbrido)
Publicado hace 9 horas
"""

MERCOR_CARD_TEXT = """Machine Learning Engineer, Marketplace
Mercor
San Francisco, CA (Presencial)
130K USD/yr - 500K USD/yr
Publicado hace 18 horas
"""

TIKTOK_CARD_TEXT = """AI/ML Software Engineer Intern (Data Platform) - 2026 Summer (BS/MS) (Empleo verificado)
AI/ML Software Engineer Intern (Data Platform) - 2026 Summer (BS/MS)

TikTok 2

San José, CA

Prestación de servicios médicos

1 contacto trabaja aquí

Visto

 ·

Adelántate a solicitar el empleo

 ·

Publicado hace 14 horas
hace 14 horas
"""

THREEM_CARD_TEXT = """Data Science Intern
3M
Remote
Posted 6 hours ago
"""

NOISY_CARD_TEXT = """Skip to main content
AI Engineer Intern
Athena
United States
Posted 3 hours ago
"""

DETAIL_TEXT = """Machine Learning Scientist Intern
LinkedIn
Mountain View, CA
Reposted 2 days ago
About the job
We are looking for a machine learning intern to build production models, evaluate experiments, and work with Python and SQL.
This role supports applied ML systems and data pipelines in production.
Seniority level
Internship
"""

CHROME_HEAVY_DETAIL_TEXT = """Intern - AI/ML Data Engineering - St. Louis (Verified job)Intern - AI/ML Data Engineering - St. Louis
Core & Main
St Louis, MO
1 hour ago
Save
Apply
Show match details
Tailor my resume
Create cover letter
About the job
The AI/ML Intern will support the Data Engineering team in developing and applying machine learning and artificial intelligence solutions.
This role will assist in building data pipelines and experimenting with machine learning models.
People you can reach out to
About the company
Core & Main is a leader in advancing reliable infrastructure.
"""

LEADING_NOISE_DETAIL_TEXT = """0 notifications
Skip to main content
Jobs
Core & Main
Intern - AI/ML Data Engineering - St. Louis
St Louis, MO
1 hour ago
About the job
The AI/ML Intern will support the Data Engineering team in developing and applying machine learning solutions.
Acerca de la empresa
Core & Main is a leader in advancing reliable infrastructure.
"""

ENGLISH_LEADING_NOISE_DETAIL_TEXT = """Skip to primary content
Skip to aside
Jobs
LinkedIn
Machine Learning Scientist Intern
Mountain View, CA
Posted 3 hours ago
About the job
We are looking for a machine learning intern to build production models.
About the company
LinkedIn
"""

CISCO_DETAIL_TEXT = """Cisco

Machine Learning Engineer

San José, CA  · hace 15 horas · Más de 100 personas han hecho clic en «Solicitar»

Promocionado por técnico de selección · Respuestas gestionadas fuera de LinkedIn

Jornada completa
Solicitar
Guardar
Personas con las que puedes hablar

Antitta y otros miembros de tu red

Mostrar todo
Acerca del empleo

We are seeking a Machine Learning Engineer to build dynamic troubleshooting agents.
"""

MERCOR_DETAIL_TEXT = """Mercor

Machine Learning Engineer, Marketplace

San Francisco, CA  · hace 17 horas · Más de 100 personas han hecho clic en «Solicitar»

Promocionado por técnico de selección · Respuestas gestionadas fuera de LinkedIn

130K USD/yr - 500K USD/yr
Presencial
Jornada completa
Solicitar
Guardar
Personas con las que puedes hablar

Antiguos alumnos de University of Southern California y otras personas de tu red

Mostrar todo
Acerca del empleo

As a Machine Learning Engineer on the Marketplace team, you will build the models and decision systems that power Mercor's hiring engine.
"""

NOISY_HEADER_DETAIL_TEXT = """Save
Use AI to assess how you fit
San Francisco, CA
Posted 3 hours ago
About the job
We are looking for a machine learning intern to build production models.
"""

CLOSED_DETAIL_TEXT = """Innovaccer

Data Ops-Intern

United States · Posted 8 hours ago
No longer accepting applications

About the job
We are looking for a Data Ops - Intern to help customers explore healthcare data.
"""

POLLUTED_COMPANY_DETAIL_TEXT = """Machine Learning Scientist Intern
Skip to main content LinkedIn
Mountain View, CA
Posted 3 hours ago
About the job
We are looking for a machine learning intern to build production models.
"""


class LinkedInSourceTests(unittest.TestCase):
    def test_partition_linkedin_urls_separates_search_and_job_urls(self) -> None:
        search_urls, job_urls = _partition_linkedin_urls(
            [
                "https://www.linkedin.com/jobs/search/?keywords=data",
                "https://www.linkedin.com/jobs/view/1234567890/?trackingId=abc",
            ]
        )
        self.assertEqual(search_urls, ["https://www.linkedin.com/jobs/search/?keywords=data"])
        self.assertEqual(job_urls, ["https://www.linkedin.com/jobs/view/1234567890/?trackingId=abc"])

    def test_normalize_search_url_forces_newest_sort(self) -> None:
        normalized = _normalize_search_url("https://www.linkedin.com/jobs/search/?keywords=data&sortBy=R")
        self.assertIn("sortBy=DD", normalized)
        self.assertNotIn("sortBy=R", normalized)

    def test_canonical_linkedin_job_url_strips_tracking_query(self) -> None:
        canonical = _canonical_linkedin_job_url("https://www.linkedin.com/jobs/view/1234567890/?trackingId=abc")
        self.assertEqual(canonical, "https://www.linkedin.com/jobs/view/1234567890")
        canonical_from_search = _canonical_linkedin_job_url(
            "https://www.linkedin.com/jobs/search-results/?currentJobId=1234567890&keywords=data"
        )
        self.assertEqual(canonical_from_search, "https://www.linkedin.com/jobs/view/1234567890")

    def test_parse_card_text_prefers_live_locator_job_url_when_supplied(self) -> None:
        parsed = _parse_card_text(
            CARD_TEXT,
            fallback_url="https://www.linkedin.com/jobs/view/9999999999/?trackingId=stale",
        )
        self.assertEqual(parsed["url"], "https://www.linkedin.com/jobs/view/9999999999")

    def test_parse_card_text(self) -> None:
        parsed = _parse_card_text(
            CARD_TEXT,
            fallback_url="https://www.linkedin.com/jobs/view/1234567890/?trackingId=abc",
        )
        self.assertEqual(parsed["title"], "Machine Learning Scientist Intern")
        self.assertEqual(parsed["company"], "LinkedIn")
        self.assertEqual(parsed["location"], "Mountain View, CA")
        self.assertTrue(parsed["posted_at"])
        self.assertTrue(parsed["is_reposted"])
        self.assertEqual(parsed["url"], "https://www.linkedin.com/jobs/view/1234567890")

    def test_parse_card_text_company_first_layout(self) -> None:
        parsed = _parse_card_text(
            COMPANY_FIRST_CARD_TEXT,
            fallback_url="https://www.linkedin.com/jobs/view/4356782624",
        )
        self.assertEqual(parsed["company"], "DoorDash")
        self.assertEqual(parsed["title"], "Machine Learning Engineer, Marketplace Optimization")
        self.assertEqual(parsed["location"], "Sunnyvale, CA")
        self.assertTrue(parsed["posted_at"])

    def test_parse_card_text_strips_verified_duplicate_title(self) -> None:
        parsed = _parse_card_text(
            VERIFIED_DUPLICATE_CARD_TEXT,
            fallback_url="https://www.linkedin.com/jobs/view/4406254905",
        )
        self.assertEqual(parsed["title"], "Machine Learning Applied Researcher")
        self.assertEqual(parsed["company"], "Archetype AI")
        self.assertEqual(parsed["location"], "San Mateo, CA (Híbrido)")

    def test_parse_card_text_keeps_role_like_title_with_comma(self) -> None:
        parsed = _parse_card_text(
            MERCOR_CARD_TEXT,
            fallback_url="https://www.linkedin.com/jobs/view/4435372215",
        )
        self.assertEqual(parsed["title"], "Machine Learning Engineer, Marketplace")
        self.assertEqual(parsed["company"], "Mercor")
        self.assertEqual(parsed["location"], "San Francisco, CA (Presencial)")

    def test_parse_card_text_ignores_leading_navigation_noise(self) -> None:
        parsed = _parse_card_text(
            NOISY_CARD_TEXT,
            fallback_url="https://www.linkedin.com/jobs/view/4435408803",
        )
        self.assertEqual(parsed["title"], "AI Engineer Intern")
        self.assertEqual(parsed["company"], "Athena")
        self.assertEqual(parsed["location"], "United States")

    def test_build_row_strips_spurious_trailing_company_count(self) -> None:
        card = _parse_card_text(
            TIKTOK_CARD_TEXT,
            fallback_url="https://www.linkedin.com/jobs/view/4405987988",
        )
        row = _build_row(
            card=card,
            detail_text="",
            search_url="https://www.linkedin.com/jobs/search/?keywords=data+engineer+intern",
            detail_fetch_attempted=False,
        )
        assert row is not None
        self.assertEqual(row["company"], "TikTok")

    def test_build_row_keeps_legitimate_numeric_company_name(self) -> None:
        card = _parse_card_text(
            THREEM_CARD_TEXT,
            fallback_url="https://www.linkedin.com/jobs/view/4400000000",
        )
        row = _build_row(
            card=card,
            detail_text="",
            search_url="https://www.linkedin.com/jobs/search/?keywords=data+science+intern",
            detail_fetch_attempted=False,
        )
        assert row is not None
        self.assertEqual(row["company"], "3M")

    def test_parse_detail_text(self) -> None:
        parsed = _parse_detail_text(DETAIL_TEXT)
        self.assertEqual(parsed["title"], "Machine Learning Scientist Intern")
        self.assertEqual(parsed["company"], "LinkedIn")
        self.assertEqual(parsed["location"], "Mountain View, CA")
        self.assertTrue(parsed["is_reposted"])
        self.assertIn("production models", parsed["description"])

    def test_parse_detail_text_strips_linkedin_page_chrome(self) -> None:
        parsed = _parse_detail_text(CHROME_HEAVY_DETAIL_TEXT)
        self.assertEqual(parsed["title"], "Intern - AI/ML Data Engineering - St. Louis (Verified job)Intern - AI/ML Data Engineering - St. Louis")
        self.assertEqual(parsed["company"], "Core & Main")
        self.assertEqual(parsed["location"], "St Louis, MO")
        self.assertIn("support the Data Engineering team", parsed["description"])
        self.assertNotIn("People you can reach out to", parsed["description"])

    def test_parse_detail_text_strips_leading_navigation_noise(self) -> None:
        parsed = _parse_detail_text(LEADING_NOISE_DETAIL_TEXT)
        self.assertEqual(parsed["company"], "Core & Main")
        self.assertEqual(parsed["title"], "Intern - AI/ML Data Engineering - St. Louis")
        self.assertEqual(parsed["location"], "St Louis, MO")
        self.assertIn("machine learning solutions", parsed["description"])
        self.assertNotIn("0 notifications", parsed["description"])

    def test_parse_detail_text_strips_english_skip_navigation_noise(self) -> None:
        parsed = _parse_detail_text(ENGLISH_LEADING_NOISE_DETAIL_TEXT)
        self.assertEqual(parsed["company"], "LinkedIn")
        self.assertEqual(parsed["title"], "Machine Learning Scientist Intern")
        self.assertEqual(parsed["location"], "Mountain View, CA")
        self.assertIn("production models", parsed["description"])
        self.assertNotIn("Skip to primary content", parsed["description"])

    def test_parse_detail_text_company_then_role_cisco_shape(self) -> None:
        parsed = _parse_detail_text(CISCO_DETAIL_TEXT)
        self.assertEqual(parsed["company"], "Cisco")
        self.assertEqual(parsed["title"], "Machine Learning Engineer")
        self.assertEqual(parsed["location"], "San José, CA")

    def test_parse_detail_text_company_then_role_with_salary_line(self) -> None:
        parsed = _parse_detail_text(MERCOR_DETAIL_TEXT)
        self.assertEqual(parsed["company"], "Mercor")
        self.assertEqual(parsed["title"], "Machine Learning Engineer, Marketplace")
        self.assertEqual(parsed["location"], "San Francisco, CA")

    def test_parse_detail_text_detects_closed_job(self) -> None:
        parsed = _parse_detail_text(CLOSED_DETAIL_TEXT)
        self.assertEqual(parsed["company"], "Innovaccer")
        self.assertEqual(parsed["title"], "Data Ops-Intern")
        self.assertEqual(parsed["location"], "United States")
        self.assertFalse(parsed["accepting_applications"])

    def test_build_row_prefers_detail_fields(self) -> None:
        card = _parse_card_text(
            """Machine Learning Scientist Intern
LinkedIn
Mountain View, CA
Posted 2 days ago
""",
            fallback_url="https://www.linkedin.com/jobs/view/1234567890/?trackingId=abc",
        )
        row = _build_row(
            card=card,
            detail_text="""Machine Learning Scientist Intern
LinkedIn
Mountain View, CA
Posted 2 days ago
About the job
We are looking for a machine learning intern to build production models, evaluate experiments, and work with Python and SQL.
This role supports applied ML systems and data pipelines in production.
Seniority level
Internship
""",
            search_url="https://www.linkedin.com/jobs/search/?keywords=machine+learning",
            detail_fetch_attempted=True,
        )
        assert row is not None
        self.assertEqual(row["source"], "linkedin")
        self.assertEqual(row["title"], "Machine Learning Scientist Intern")
        self.assertEqual(row["company"], "LinkedIn")
        self.assertEqual(row["url"], "https://www.linkedin.com/jobs/view/1234567890")
        self.assertEqual(row["source_metadata"]["detail_quality_status"], "detail_partial")

    def test_build_row_falls_back_to_card_fields_when_detail_header_is_noise(self) -> None:
        card = _parse_card_text(
            """Machine Learning Scientist Intern
LinkedIn
Mountain View, CA
Posted 3 hours ago
""",
            fallback_url="https://www.linkedin.com/jobs/view/1234567890/?trackingId=abc",
        )
        row = _build_row(
            card=card,
            detail_text=NOISY_HEADER_DETAIL_TEXT,
            search_url="https://www.linkedin.com/jobs/search/?keywords=machine+learning",
            detail_fetch_attempted=True,
        )
        assert row is not None
        self.assertEqual(row["title"], "Machine Learning Scientist Intern")
        self.assertEqual(row["company"], "LinkedIn")

    def test_build_row_marks_closed_linkedin_job(self) -> None:
        card = _parse_card_text(
            """Data Ops-Intern
Innovaccer
United States
Posted 8 hours ago
""",
            fallback_url="https://www.linkedin.com/jobs/view/1234567890/?trackingId=abc",
        )
        row = _build_row(
            card=card,
            detail_text=CLOSED_DETAIL_TEXT,
            search_url="https://www.linkedin.com/jobs/search/?keywords=data",
            detail_fetch_attempted=True,
        )
        assert row is not None
        self.assertFalse(bool(row["source_metadata"]["accepting_applications"]))

    def test_build_row_drops_polluted_company_and_falls_back_to_card_company(self) -> None:
        card = _parse_card_text(
            """Machine Learning Scientist Intern
LinkedIn
Mountain View, CA
Posted 3 hours ago
""",
            fallback_url="https://www.linkedin.com/jobs/view/1234567890/?trackingId=abc",
        )
        row = _build_row(
            card=card,
            detail_text=POLLUTED_COMPANY_DETAIL_TEXT,
            search_url="https://www.linkedin.com/jobs/search/?keywords=machine+learning",
            detail_fetch_attempted=True,
        )
        assert row is not None
        self.assertEqual(row["company"], "LinkedIn")

    def test_build_row_persists_external_apply_url(self) -> None:
        card = _parse_card_text(
            """Software Engineer Intern
Example
Austin, TX
Posted 8 hours ago
""",
            fallback_url="https://www.linkedin.com/jobs/view/1234567890/?trackingId=abc",
        )
        row = _build_row(
            card=card,
            detail_text="""Software Engineer Intern
Example
Austin, TX
Posted 8 hours ago
About the job
Build backend systems and APIs with Python.
""",
            search_url="https://www.linkedin.com/jobs/search/?keywords=software+engineer+intern",
            detail_fetch_attempted=True,
            external_apply_url="https://careers.example.com/jobs/123",
        )
        assert row is not None
        self.assertEqual(
            row["source_metadata"]["external_apply_url"],
            "https://careers.example.com/jobs/123",
        )

    def test_build_row_skips_reposted_card(self) -> None:
        card = _parse_card_text(
            CARD_TEXT,
            fallback_url="https://www.linkedin.com/jobs/view/1234567890/?trackingId=abc",
        )
        row = _build_row(
            card=card,
            detail_text="",
            search_url="https://www.linkedin.com/jobs/search/?keywords=machine+learning",
            detail_fetch_attempted=False,
        )
        self.assertIsNone(row)

    def test_build_row_skips_reposted_detail(self) -> None:
        card = _parse_card_text(
            """Machine Learning Scientist Intern
LinkedIn
Mountain View, CA
Posted 3 hours ago
""",
            fallback_url="https://www.linkedin.com/jobs/view/1234567890/?trackingId=abc",
        )
        row = _build_row(
            card=card,
            detail_text=DETAIL_TEXT,
            search_url="https://www.linkedin.com/jobs/search/?keywords=machine+learning",
            detail_fetch_attempted=True,
        )
        self.assertIsNone(row)

    def test_build_row_skips_spanish_reposted_card(self) -> None:
        card = _parse_card_text(
            """Data Engineer Intern (E-commerce) - 2026 Summer (BS/MS)
TikTok
San José, CA
Compartido hace 12 horas
""",
            fallback_url="https://www.linkedin.com/jobs/view/4281120423",
        )
        row = _build_row(
            card=card,
            detail_text="",
            search_url="https://www.linkedin.com/jobs/search/?keywords=data+platform+intern",
            detail_fetch_attempted=False,
        )
        self.assertIsNone(row)

    def test_relative_age_to_iso(self) -> None:
        self.assertIsNotNone(_relative_age_to_iso("Reposted 2 days ago"))
        self.assertIsNotNone(_relative_age_to_iso("Posted 3 hours ago"))
        self.assertIsNotNone(_relative_age_to_iso("Publicado hace 3 horas"))
        self.assertIsNotNone(_relative_age_to_iso("hace 2 días"))
        self.assertIsNone(_relative_age_to_iso("No age here"))

    def test_card_within_lookback(self) -> None:
        card = {"posted_at": _relative_age_to_iso("2 days ago") or ""}
        self.assertFalse(_is_card_older_than_lookback(card, 7))


if __name__ == "__main__":
    unittest.main()
