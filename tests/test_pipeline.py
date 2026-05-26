from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from job_hunter.config import Settings
from job_hunter.models import JobRecord
from job_hunter.pipeline import (
    _dedupe_key,
    _evaluate_eligibility,
    _is_internship,
    _is_us_scope,
    _score_relevance,
    run_pipeline,
)
from job_hunter.storage import JobStore


def make_settings(db_path: str) -> Settings:
    return Settings(
        db_path=db_path,
        poll_interval_minutes=15,
        request_timeout_seconds=10,
        use_arbeitnow=False,
        use_remotive=False,
        use_themuse=False,
        use_greenhouse=False,
        use_lever=False,
        use_rss=False,
        use_usajobs=False,
        use_adzuna=False,
        min_relevance_score=3.0,
        min_eligibility_confidence=0.4,
        notify_on_ambiguous_eligibility=True,
        max_posting_age_days=7,
        telegram_bot_token=None,
        telegram_chat_id=None,
        themuse_pages=2,
        greenhouse_boards=[],
        lever_companies=[],
        rss_feeds=[],
        usajobs_user_agent=None,
        usajobs_auth_key=None,
        usajobs_results_per_page=250,
        adzuna_app_id=None,
        adzuna_app_key=None,
        adzuna_country="us",
        adzuna_pages=2,
    )


class FakeSource:
    name = "fake"

    def __init__(self, payload: list[dict]) -> None:
        self.payload = payload

    def fetch(self, timeout_seconds: int) -> list[dict]:
        _ = timeout_seconds
        return self.payload


class FakeNotifier:
    def __init__(self) -> None:
        self.sent = 0

    def send(self, job: JobRecord) -> bool:
        self.sent += 1
        return True


class PipelineUnitTests(unittest.TestCase):
    def test_eligibility_negative_rule_excludes(self) -> None:
        job = JobRecord(
            source="x",
            external_id="1",
            url="https://example.com",
            title="Machine Learning Intern",
            company="Example",
            location="Remote - US",
            is_internship=True,
            posted_at="2026-05-20",
            description="Must be authorized to work in the US.",
            ingested_at="2026-05-25T00:00:00+00:00",
        )
        status, confidence, negative, _ = _evaluate_eligibility(job)
        self.assertEqual(status, "reject")
        self.assertEqual(confidence, 0.0)
        self.assertTrue(negative)

    def test_internship_and_us_scope_filters(self) -> None:
        job = JobRecord(
            source="x",
            external_id="1",
            url="https://example.com",
            title="Data Science Intern",
            company="Example",
            location="United States",
            is_internship=False,
            posted_at=None,
            description="Python and SQL",
            ingested_at="now",
        )
        self.assertTrue(_is_internship(job))
        self.assertTrue(_is_us_scope(job))

    def test_description_based_internship_match(self) -> None:
        job = JobRecord(
            source="x",
            external_id="1",
            url="https://example.com",
            title="Machine Learning Program Participant",
            company="Example",
            location="United States",
            is_internship=False,
            posted_at=None,
            description="Join our summer internship program for AI research.",
            ingested_at="now",
        )
        self.assertTrue(_is_internship(job))

    def test_false_positive_non_intern_role_is_filtered(self) -> None:
        job = JobRecord(
            source="x",
            external_id="1",
            url="https://example.com",
            title="Business Transformation Lead",
            company="Example",
            location="USA",
            is_internship=False,
            posted_at=None,
            description=(
                "Lead initiatives across international pharmacy operations and "
                "optimize workflows with AI/ML tooling."
            ),
            ingested_at="now",
            skills=["AI/ML", "automation"],
        )
        self.assertFalse(_is_internship(job))

    def test_eligibility_without_explicit_us_auth_requirement_is_ambiguous(self) -> None:
        job = JobRecord(
            source="x",
            external_id="1",
            url="https://example.com",
            title="Data Science Intern",
            company="Example",
            location="US",
            is_internship=True,
            posted_at=None,
            description="We currently do not provide visa sponsorship.",
            ingested_at="now",
        )
        status, confidence, negative, positive = _evaluate_eligibility(job)
        self.assertEqual(status, "ambiguous")
        self.assertEqual(confidence, 0.6)
        self.assertEqual(negative, [])
        self.assertEqual(positive, [])

    def test_relevance_scoring(self) -> None:
        job = JobRecord(
            source="x",
            external_id="1",
            url="https://example.com",
            title="Machine Learning Intern",
            company="Example",
            location="US",
            is_internship=True,
            posted_at="2026-05-20",
            description="NLP, Deep Learning, Python, SQL",
            ingested_at="now",
        )
        score, hits = _score_relevance(job)
        self.assertGreaterEqual(score, 5.0)
        self.assertIn("machine learning", hits)

    def test_relevance_keyword_word_boundaries(self) -> None:
        job = JobRecord(
            source="x",
            external_id="1",
            url="https://example.com",
            title="Generalist",
            company="Example",
            location="US",
            is_internship=False,
            posted_at=None,
            description="Build HTML interfaces and optimize systems.",
            ingested_at="now",
        )
        score, hits = _score_relevance(job)
        self.assertEqual(score, 0.0)
        self.assertEqual(hits, [])

    def test_relevance_unknown_age_penalty(self) -> None:
        job = JobRecord(
            source="x",
            external_id="1",
            url="https://example.com",
            title="Machine Learning Intern",
            company="Example",
            location="US",
            is_internship=True,
            posted_at=None,
            description="Machine learning internship",
            ingested_at="now",
        )
        score, _ = _score_relevance(job)
        self.assertGreaterEqual(score, 2.75)

    def test_dedupe_stability(self) -> None:
        j1 = JobRecord(
            source="x",
            external_id="1",
            url="https://example.com/job?ref=abc",
            title="Data Engineer Intern",
            company="Acme",
            location="US",
            is_internship=True,
            posted_at=None,
            description="",
            ingested_at="now",
        )
        j2 = JobRecord(
            source="x",
            external_id="2",
            url="https://example.com/job?ref=zzz",
            title="Data Engineer Intern",
            company="Acme",
            location="United States",
            is_internship=True,
            posted_at=None,
            description="",
            ingested_at="now",
        )
        self.assertEqual(_dedupe_key(j1), _dedupe_key(j2))


class PipelineIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = str(Path(self.temp_dir.name) / "test.db")
        self.settings = make_settings(db_path)
        self.store = JobStore(db_path)

    def tearDown(self) -> None:
        self.store.close()
        self.temp_dir.cleanup()

    def test_end_to_end_and_idempotency(self) -> None:
        payload = [
            {
                "source": "fake",
                "external_id": "job-1",
                "url": "https://example.com/job-1",
                "title": "Machine Learning Intern",
                "company": "Acme",
                "location": "Remote - US",
                "posted_at": "2026-05-21",
                "description": "Build NLP models in Python for our summer internship program.",
                "skills": ["python", "nlp"],
            }
        ]
        notifier = FakeNotifier()

        with patch("job_hunter.pipeline.build_sources", return_value=[FakeSource(payload)]):
            outcome1 = run_pipeline(self.settings, self.store, notifier)
            outcome2 = run_pipeline(self.settings, self.store, notifier)

        self.assertEqual(outcome1.persisted_count, 1)
        self.assertEqual(outcome1.notified_count, 1)
        self.assertEqual(outcome2.persisted_count, 0)
        self.assertGreaterEqual(outcome2.duplicate_count, 1)
        self.assertEqual(notifier.sent, 1)

    def test_db_false_positive_regression(self) -> None:
        payload = [
            {
                "source": "fake",
                "external_id": "job-2",
                "url": "https://example.com/job-2",
                "title": "Business Transformation Lead",
                "company": "Expion Health",
                "location": "USA",
                "posted_at": "2026-05-21",
                "description": (
                    "Expion Health is building the future of pharmacy economics. "
                    "Work across international teams and optimize business operations "
                    "with AI/ML automation."
                ),
                "skills": ["AI/ML", "automation", "healthcare"],
            }
        ]
        notifier = FakeNotifier()

        with patch("job_hunter.pipeline.build_sources", return_value=[FakeSource(payload)]):
            outcome = run_pipeline(self.settings, self.store, notifier)

        self.assertEqual(outcome.passed_filter_count, 0)
        self.assertEqual(outcome.persisted_count, 0)
        self.assertEqual(outcome.notified_count, 0)

    def test_age_window_filters_old_postings(self) -> None:
        stale_payload = [
            {
                "source": "fake",
                "external_id": "old-1",
                "url": "https://example.com/old-1",
                "title": "Data Science Intern",
                "company": "Acme",
                "location": "Remote - US",
                "posted_at": "2020-01-01T00:00:00+00:00",
                "description": "Summer internship program for ML and analytics",
                "skills": ["python"],
            }
        ]
        with patch("job_hunter.pipeline.build_sources", return_value=[FakeSource(stale_payload)]):
            outcome = run_pipeline(self.settings, self.store, None)

        self.assertEqual(outcome.persisted_count, 0)
        self.assertEqual(outcome.source_stats["fake"].rejected_age_count, 1)

    def test_duplicate_can_notify_when_previously_unnotified(self) -> None:
        payload = [
            {
                "source": "fake",
                "external_id": "job-3",
                "url": "https://example.com/job-3",
                "title": "Data Science Intern",
                "company": "Acme",
                "location": "Remote - US",
                "posted_at": "2026-05-21",
                "description": "Summer internship program for ML and Python",
                "skills": ["python"],
            }
        ]

        notifier1 = FakeNotifier()
        settings_no_ambiguous = replace(self.settings, notify_on_ambiguous_eligibility=False)
        with patch("job_hunter.pipeline.build_sources", return_value=[FakeSource(payload)]):
            outcome1 = run_pipeline(settings_no_ambiguous, self.store, notifier1)
        self.assertEqual(outcome1.persisted_count, 1)
        self.assertEqual(outcome1.notified_count, 0)
        self.assertEqual(notifier1.sent, 0)

        notifier2 = FakeNotifier()
        settings_with_ambiguous = replace(self.settings, notify_on_ambiguous_eligibility=True)
        with patch("job_hunter.pipeline.build_sources", return_value=[FakeSource(payload)]):
            outcome2 = run_pipeline(settings_with_ambiguous, self.store, notifier2)
        self.assertEqual(outcome2.persisted_count, 0)
        self.assertGreaterEqual(outcome2.duplicate_count, 1)
        self.assertEqual(outcome2.notified_count, 1)
        self.assertEqual(notifier2.sent, 1)


if __name__ == "__main__":
    unittest.main()
