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
            "Masters degree preferred. Production ML systems with LLM deployment and causal inference research."
        )
        self.assertIn("mentions_masters", flags)
        self.assertIn("mentions_production_ml", flags)
        self.assertIn("mentions_llm", flags)
        self.assertIn("mentions_causal_inference", flags)
        self.assertIn("mentions_research", flags)

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


if __name__ == "__main__":
    unittest.main()
