from __future__ import annotations

import unittest

from job_hunter.models import JobRecord
from job_hunter.stage2 import ShadowProfileScorer, build_job_text_v1, extract_job_flags


class Stage2Tests(unittest.TestCase):
    def test_build_job_text_v1_is_deterministic_and_structured(self) -> None:
        job = JobRecord(
            source="fake",
            external_id="1",
            url="https://example.com/job-1",
            title="AI/ML Data Engineering Intern",
            company="Hyphenova",
            location="Remote",
            is_internship=True,
            posted_at="2026-06-12",
            description=(
                "The Opportunity:\n"
                "Build production ML and data pipelines for creator analytics.\n"
                "Requirements\n"
                "- Python\n"
                "- AWS\n"
                "- SQL\n"
                "Responsibilities\n"
                "- Build ETL jobs\n"
                "- Support model deployment\n"
                "About the employer\n"
                "Boilerplate that should not appear."
            ),
            ingested_at="2026-06-17T00:00:00+00:00",
        )

        text1 = build_job_text_v1(job)
        text2 = build_job_text_v1(job)

        self.assertEqual(text1, text2)
        self.assertIn("TITLE: AI/ML Data Engineering Intern", text1)
        self.assertIn("ORG: Hyphenova", text1)
        self.assertIn("QUALIFICATIONS:", text1)
        self.assertIn("- Python", text1)
        self.assertIn("RESPONSIBILITIES:", text1)
        self.assertIn("- Build ETL jobs", text1)
        self.assertNotIn("Boilerplate that should not appear.", text1)

    def test_extract_job_flags(self) -> None:
        flags = extract_job_flags(
            "Masters degree preferred. Production ML systems with LLM deployment and causal inference research in quantitative trading."
        )
        self.assertIn("mentions_masters", flags)
        self.assertIn("mentions_production_ml", flags)
        self.assertIn("mentions_llm", flags)
        self.assertIn("mentions_causal_inference", flags)
        self.assertIn("mentions_research", flags)
        self.assertIn("mentions_quant", flags)
        self.assertIn("mentions_trading", flags)

    def test_shadow_profile_scorer_returns_shadow_fields(self) -> None:
        job = JobRecord(
            source="fake",
            external_id="2",
            url="https://example.com/job-2",
            title="Data Science Internship",
            company="Siemens",
            location="Pasadena, CA",
            is_internship=True,
            posted_at="2026-06-12",
            description="Masters preferred. Research background in web analytics is preferable.",
            compensation_type="paid",
            ingested_at="2026-06-17T00:00:00+00:00",
        )
        result = ShadowProfileScorer().score(job)
        self.assertEqual(result.job_text_version, "job_text_v1")
        self.assertEqual(result.profile_version, "default_v1")
        self.assertEqual(result.scorer_version, "shadow_rules_v1")
        self.assertIn(result.profile_match_label, {"pass", "review", "reject"})
        self.assertTrue(result.job_text_snapshot.startswith("TITLE: Data Science Internship"))

    def test_shadow_profile_scorer_rewards_builder_signals(self) -> None:
        job = JobRecord(
            source="fake",
            external_id="3",
            url="https://example.com/job-3",
            title="AI Builder Intern",
            company="Scale AI",
            location="San Francisco, CA",
            is_internship=True,
            posted_at="2026-06-12",
            description=(
                "You'll spend the summer building AI-powered tools, automating workflows, "
                "and deploying agentic systems used by real teams."
            ),
            compensation_type="paid",
            ingested_at="2026-06-17T00:00:00+00:00",
        )
        result = ShadowProfileScorer().score(job)
        self.assertEqual(result.profile_match_label, "pass")
        self.assertIn("builder_signal_alignment", result.profile_match_reason_codes)

    def test_shadow_profile_scorer_uses_normalized_compensation_type(self) -> None:
        job = JobRecord(
            source="fake",
            external_id="4",
            url="https://example.com/job-4",
            title="AI/ML Data Engineering Intern",
            company="Hyphenova",
            location="Remote",
            is_internship=True,
            posted_at="2026-06-12",
            description="Build ETL pipelines and deploy production ML systems.",
            compensation_type="paid",
            ingested_at="2026-06-17T00:00:00+00:00",
        )
        result = ShadowProfileScorer().score(job)
        self.assertNotIn("compensation_unpaid", result.profile_match_reason_codes)

    def test_shadow_profile_scorer_penalizes_research_heavy_roles(self) -> None:
        job = JobRecord(
            source="fake",
            external_id="5",
            url="https://example.com/job-5",
            title="Master's Fall Machine Learning Internship",
            company="Pinterest",
            location="US",
            is_internship=True,
            posted_at="2026-06-12",
            description=(
                "Preferred qualifications: Publications in machine learning and strong passion for research. "
                "Research background in search relevance is preferred."
            ),
            compensation_type="paid",
            ingested_at="2026-06-17T00:00:00+00:00",
        )
        result = ShadowProfileScorer().score(job)
        self.assertEqual(result.profile_match_label, "reject")
        self.assertLess(result.profile_match_score, 0.45)
        self.assertIn("research_heavy_signal", result.profile_match_reason_codes)

    def test_shadow_profile_scorer_penalizes_quant_research_roles(self) -> None:
        job = JobRecord(
            source="fake",
            external_id="5b",
            url="https://example.com/job-5b",
            title="Quantitative Research Intern (BS/MS)",
            company="IMC Trading",
            location="Chicago, IL",
            is_internship=True,
            posted_at="2026-07-01",
            description=(
                "Develop your research skills with support from a mentor. "
                "Enhance your understanding of options theory and market making. "
                "Experience in Python is highly desired."
            ),
            compensation_type="paid",
            ingested_at="2026-07-02T00:00:00+00:00",
        )
        result = ShadowProfileScorer().score(job)
        self.assertEqual(result.profile_match_label, "reject")
        self.assertIn("flag_quant_research", result.profile_match_reason_codes)

    def test_job_text_summary_prefers_job_specific_sentences_over_brand_copy(self) -> None:
        job = JobRecord(
            source="fake",
            external_id="6",
            url="https://example.com/job-6",
            title="Machine Learning Internship",
            company="Pinterest",
            location="US",
            is_internship=True,
            posted_at="2026-06-12",
            description=(
                "At Pinterest, we're on a mission to bring everyone the inspiration to create a life they love. "
                "Discover a career where you ignite innovation for millions. "
                "Build ML systems for visual search and recommendation pipelines. "
                "Deploy production models for ranking."
            ),
            compensation_type="paid",
            ingested_at="2026-06-17T00:00:00+00:00",
        )
        text = build_job_text_v1(job)
        self.assertIn("Build ML systems for visual search and recommendation pipelines", text)
        self.assertIn("Deploy production models for ranking", text)
        self.assertNotIn("we're on a mission", text.lower())
        self.assertNotIn("discover a career", text.lower())

    def test_job_text_summary_strips_handshake_ui_residue(self) -> None:
        job = JobRecord(
            source="fake",
            external_id="7",
            url="https://example.com/job-7",
            title="AI/ML Data Engineering Intern",
            company="Hyphenova",
            location="Remote",
            is_internship=True,
            posted_at="2026-06-12",
            description=(
                "The Opportunity: Build ETL pipelines and support AWS data systems. "
                "More Save Apply What they're looking for You match all qualifications. Nice!"
            ),
            compensation_type="paid",
            ingested_at="2026-06-17T00:00:00+00:00",
        )
        text = build_job_text_v1(job)
        self.assertIn("Build ETL pipelines and support AWS data systems", text)
        self.assertNotIn("More Save Apply", text)
        self.assertNotIn("You match all qualifications", text)

    def test_job_text_extracts_inline_handshake_backend_sections(self) -> None:
        job = JobRecord(
            source="handshake",
            external_id="8",
            url="https://example.com/job-8",
            title="Backend Developer Intern",
            company="GBCS Group",
            location="Remote",
            is_internship=True,
            posted_at="2026-07-01",
            description=(
                "Posted 6 hours ago Save Share Apply At a glance Remote Job description "
                "Role Description As a Backend Developer Intern at SkyIT, you will contribute to robust server-side components and APIs. "
                "Key Responsibilities Develop, optimize, and maintain backend services and APIs using Django REST Framework (Python) and Express (JavaScript). "
                "Deploy, monitor, and manage backend applications on Microsoft Azure cloud platform. "
                "Required Skills and Qualifications Strong portfolio showcasing backend development with Django REST Framework (Python) and possibly Express (JavaScript). "
                "Working knowledge of Docker containerization and RESTful and GraphQL API design."
            ),
            compensation_type="paid",
            ingested_at="2026-07-02T00:00:00+00:00",
        )
        text = build_job_text_v1(job)
        self.assertIn("SUMMARY:", text)
        self.assertIn("Backend Developer Intern at SkyIT", text)
        self.assertIn("QUALIFICATIONS:", text)
        self.assertIn("Django REST Framework", text)
        self.assertIn("RESTful and GraphQL API design", text)
        self.assertIn("RESPONSIBILITIES:", text)
        self.assertIn("backend services and APIs using Django REST Framework", text)
        self.assertIn("backend applications on Microsoft Azure cloud platform", text)
        self.assertNotIn("Posted 6 hours ago", text)


if __name__ == "__main__":
    unittest.main()
