from __future__ import annotations

import tempfile
import unittest
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
            description="Must be authorized to work in the US. No visa sponsorship.",
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
        self.settings = Settings(
            db_path=db_path,
            poll_interval_minutes=15,
            request_timeout_seconds=10,
            use_arbeitnow=False,
            use_remotive=False,
            use_themuse=False,
            min_relevance_score=2.0,
            min_eligibility_confidence=0.4,
            notify_on_ambiguous_eligibility=False,
            telegram_bot_token=None,
            telegram_chat_id=None,
        )
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
                "description": "CPT OPT welcome. Build NLP models in Python.",
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


if __name__ == "__main__":
    unittest.main()
