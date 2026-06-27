from __future__ import annotations

import re
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from job_hunter.config import Settings
from job_hunter.models import JobRecord
from job_hunter.pipeline import (
    _classify_compensation,
    _dedupe_key,
    _evaluate_eligibility,
    _fails_policy_gate,
    _is_internship,
    _passes_data_role_gate,
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
        use_github_repos=False,
        use_ashby=False,
        use_handshake=False,
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
        github_repo_readmes=[],
        ashby_boards=[],
        handshake_search_urls=[],
        title_blacklist_patterns=[r"\brecruiter\b"],
        data_role_title_patterns=[
            r"\b(machine learning|ml)\b",
            r"\bdata (science|scientist)\b",
            r"\bdata engineer(ing)?\b",
            r"\banalytics engineer\b",
        ],
        non_data_title_patterns=[
            r"\bdeveloper advocacy\b",
            r"\bgo[- ]to[- ]market\b",
            r"\b(content|video content)\b",
        ],
        policy_reject_patterns=[
            r"\bph\.?d\.?\b",
            r"\bdoctoral\b",
            r"\beconomics team\b",
            r"\boperations research\b",
        ],
        min_data_signal_count=2,
        greenhouse_token_file=None,
        lever_token_file=None,
        rss_feed_file=None,
        greenhouse_quarantine_file=None,
        lever_quarantine_file=None,
        rss_quarantine_file=None,
        source_failure_quarantine_threshold=3,
        source_restore_success_threshold=2,
        handshake_profile_dir=".handshake-profile",
        handshake_headless=True,
        handshake_max_results=25,
        handshake_page_timeout_seconds=30,
        handshake_fetch_details=True,
        usajobs_user_agent=None,
        usajobs_auth_key=None,
        usajobs_results_per_page=250,
        adzuna_app_id=None,
        adzuna_app_key=None,
        adzuna_country="us",
        adzuna_pages=2,
    )


def recent_posted_at(days_ago: int = 1) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


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
    def test_compensation_classification(self) -> None:
        self.assertEqual(_classify_compensation("Data Engineering Intern", "Unpaid · Internship Remote"), "unpaid")
        self.assertEqual(_classify_compensation("Data Science Internship", "$18-50/hr · Internship"), "paid")
        self.assertEqual(_classify_compensation("ML Intern", "Build models and pipelines"), "unknown")
        self.assertEqual(
            _classify_compensation(
                "Data Science Internship",
                "Data Science Internship $18-50/hr Internship Pasadena, CA Similar Jobs Unpaid Internship Remote",
            ),
            "paid",
        )

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

    def test_eligibility_rejects_current_or_future_sponsorship_block(self) -> None:
        job = JobRecord(
            source="x",
            external_id="2",
            url="https://example.com/2",
            title="Data Science Internship",
            company="Example",
            location="Pasadena, CA",
            is_internship=True,
            posted_at="2026-06-12",
            description=(
                "Open to candidates with OPT/CPT. "
                "Legally authorized to work in the United States without the need for current or future sponsorship by the company."
            ),
            ingested_at="2026-06-17T00:00:00+00:00",
        )
        status, confidence, negative, positive = _evaluate_eligibility(job)
        self.assertEqual(status, "reject")
        self.assertEqual(confidence, 0.0)
        self.assertIn("no_current_future_sponsorship", negative)
        self.assertEqual(positive, [])

    def test_eligibility_rejects_siemens_style_conflicting_handshake_text(self) -> None:
        job = JobRecord(
            source="handshake",
            external_id="3",
            url="https://example.com/3",
            title="Data Science Internship",
            company="Siemens Digital Industries Software",
            location="Pasadena, CA",
            is_internship=True,
            posted_at="2026-06-12",
            description=(
                "Open to candidates with OPT/CPT. "
                "Legally authorized to work in the United States without the need for current or future sponsorship by the company. "
                "3.0 GPA Masters Statistics major Data Science major."
            ),
            ingested_at="2026-06-17T00:00:00+00:00",
        )
        status, confidence, negative, positive = _evaluate_eligibility(job)
        self.assertEqual(status, "reject")
        self.assertEqual(confidence, 0.0)
        self.assertIn("no_current_future_sponsorship", negative)
        self.assertEqual(positive, [])

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

    def test_us_scope_accepts_city_state_locations(self) -> None:
        job = JobRecord(
            source="x",
            external_id="1",
            url="https://example.com",
            title="Data Science Intern",
            company="Example",
            location="Washington, DC",
            is_internship=True,
            posted_at=None,
            description="Python and SQL",
            ingested_at="now",
        )
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
        self.assertEqual(status, "reject")
        self.assertEqual(confidence, 0.0)
        self.assertTrue(negative)
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

    def test_data_role_gate_rejects_non_data_title(self) -> None:
        job = JobRecord(
            source="x",
            external_id="1",
            url="https://example.com",
            title="Developer Advocacy Intern",
            company="Example",
            location="US",
            is_internship=True,
            posted_at=None,
            description="Build developer communities with Python tutorials.",
            ingested_at="now",
        )
        self.assertFalse(
            _passes_data_role_gate(
                job,
                data_role_title_regexes=[re.compile(r"\bdata (science|scientist)\b", re.IGNORECASE)],
                non_data_role_title_regexes=[re.compile(r"\bdeveloper advocacy\b", re.IGNORECASE)],
                min_data_signal_count=2,
            )
        )

    def test_data_role_gate_accepts_backend_adjacent_software_intern(self) -> None:
        job = JobRecord(
            source="x",
            external_id="2",
            url="https://example.com/backend",
            title="Software Development Intern",
            company="Example",
            location="Remote - US",
            is_internship=True,
            posted_at=None,
            description=(
                "Build and maintain backend systems and APIs for warehouse operations. "
                "Work with relational and non-relational databases, Kafka, Docker, and Kubernetes."
            ),
            ingested_at="now",
        )
        self.assertTrue(
            _passes_data_role_gate(
                job,
                data_role_title_regexes=[re.compile(r"\bdata (science|scientist)\b", re.IGNORECASE)],
                non_data_role_title_regexes=[
                    re.compile(r"\bdeveloper advocacy\b", re.IGNORECASE),
                    re.compile(r"\b(frontend|front-end|ios|android|mobile app|react native)\b", re.IGNORECASE),
                ],
                min_data_signal_count=2,
            )
        )

    def test_data_role_gate_rejects_frontend_only_software_intern(self) -> None:
        job = JobRecord(
            source="x",
            external_id="3",
            url="https://example.com/frontend",
            title="Software Engineer Intern",
            company="Example",
            location="Remote - US",
            is_internship=True,
            posted_at=None,
            description=(
                "Build frontend interfaces in React Native for mobile experiences. "
                "Focus on UI polish and client-side interactions."
            ),
            ingested_at="now",
        )
        self.assertFalse(
            _passes_data_role_gate(
                job,
                data_role_title_regexes=[re.compile(r"\bdata (science|scientist)\b", re.IGNORECASE)],
                non_data_role_title_regexes=[
                    re.compile(r"\bdeveloper advocacy\b", re.IGNORECASE),
                    re.compile(r"\b(frontend|front-end|ios|android|mobile app|react native)\b", re.IGNORECASE),
                ],
                min_data_signal_count=2,
            )
        )

    def test_policy_gate_rejects_phd_research_roles(self) -> None:
        job = JobRecord(
            source="x",
            external_id="1",
            url="https://example.com",
            title="PhD Fall Machine Learning Intern",
            company="Pinterest",
            location="US",
            is_internship=True,
            posted_at=None,
            description="Publications and causal inference research required.",
            ingested_at="now",
        )
        self.assertTrue(
            _fails_policy_gate(
                job,
                policy_reject_regexes=[
                    re.compile(r"\bph\.?d\.?\b", re.IGNORECASE),
                ],
            )
        )
        self.assertFalse(
            _passes_data_role_gate(
                job,
                data_role_title_regexes=[re.compile(r"\bdata (science|scientist)\b", re.IGNORECASE)],
                non_data_role_title_regexes=[re.compile(r"\bdeveloper advocacy\b", re.IGNORECASE)],
                min_data_signal_count=2,
            )
        )

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
                "posted_at": recent_posted_at(),
                "description": "Build NLP models in Python for our summer internship program.",
                "skills": ["python", "nlp"],
            }
        ]
        notifier = FakeNotifier()

        with patch("job_hunter.pipeline.build_sources", return_value=[FakeSource(payload)]):
            outcome1 = run_pipeline(self.settings, self.store, notifier)
            outcome2 = run_pipeline(self.settings, self.store, notifier)

        self.assertEqual(outcome1.normalized_count, 1)
        self.assertEqual(outcome1.rejected_missing_core_fields_count, 0)
        self.assertEqual(outcome1.after_stage_1a_count, 1)
        self.assertEqual(outcome1.after_stage_1b_count, 1)
        self.assertEqual(outcome1.after_stage_1c_count, 1)
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
                "posted_at": recent_posted_at(),
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

        self.assertEqual(outcome.normalized_count, 1)
        self.assertEqual(outcome.after_stage_1a_count, 0)
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
                "posted_at": recent_posted_at(),
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

    def test_duplicate_refreshes_enriched_description(self) -> None:
        first_payload = [
            {
                "source": "handshake",
                "external_id": "job-5",
                "url": "https://app.joinhandshake.com/job-search/11120409?query=data+engineer+intern&page=1",
                "title": "Data Engineering Intern",
                "company": "Finz",
                "location": "Remote",
                "posted_at": recent_posted_at(),
                "description": "Finz Data Engineering Intern Unpaid · Internship Remote 5d ago",
                "skills": [],
                "source_detail": "https://app.joinhandshake.com/job-search/11120409?query=data+engineer+intern",
            }
        ]
        second_payload = [
            {
                "source": "handshake",
                "external_id": "job-5",
                "url": "https://app.joinhandshake.com/job-search/11120409?query=data+engineer+intern&page=1",
                "title": "Data Engineering Intern",
                "company": "Finz",
                "location": "Remote, based in United States",
                "posted_at": recent_posted_at(),
                "description": (
                    "We are looking for a Data Engineering Intern to help build a multi-tenant "
                    "data lakehouse from the ground up."
                ),
                "skills": [],
                "source_detail": "https://app.joinhandshake.com/job-search/11120409?query=data+engineer+intern&page=1",
            }
        ]
        with patch("job_hunter.pipeline.build_sources", return_value=[FakeSource(first_payload)]):
            outcome1 = run_pipeline(self.settings, self.store, None)
        self.assertEqual(outcome1.persisted_count, 1)

        with patch("job_hunter.pipeline.build_sources", return_value=[FakeSource(second_payload)]):
            outcome2 = run_pipeline(self.settings, self.store, None)
        self.assertEqual(outcome2.persisted_count, 0)
        row = self.store.get_job_for_labeling(1)
        self.assertIsNotNone(row)
        self.assertIn("multi-tenant data lakehouse", row["description"])

    def test_duplicate_refresh_prefers_cleaner_handshake_description(self) -> None:
        first_payload = [
            {
                "source": "handshake",
                "external_id": "job-5b",
                "url": "https://app.joinhandshake.com/job-search/11120409?query=data+engineer+intern&page=1",
                "title": "Data Engineering Intern",
                "company": "Example",
                "location": "Remote",
                "posted_at": recent_posted_at(),
                "description": (
                    "Summary Beta This role as a Data Engineer Intern aligns closely with the user's query. "
                    "Build ETL pipelines and data workflows for analytics."
                ),
                "skills": [],
                "source_detail": "https://app.joinhandshake.com/job-search/11120409?query=data+engineer+intern",
            }
        ]
        second_payload = [
            {
                "source": "handshake",
                "external_id": "job-5b",
                "url": "https://app.joinhandshake.com/job-search/11120409?query=data+engineer+intern&page=1",
                "title": "Data Engineering Intern",
                "company": "Example",
                "location": "Remote",
                "posted_at": recent_posted_at(),
                "description": "Build ETL pipelines and data workflows for analytics.",
                "skills": [],
                "source_detail": "https://app.joinhandshake.com/job-search/11120409?query=data+engineer+intern&page=1",
            }
        ]
        with patch("job_hunter.pipeline.build_sources", return_value=[FakeSource(first_payload)]):
            outcome1 = run_pipeline(self.settings, self.store, None)
        self.assertEqual(outcome1.persisted_count, 1)

        with patch("job_hunter.pipeline.build_sources", return_value=[FakeSource(second_payload)]):
            outcome2 = run_pipeline(self.settings, self.store, None)
        self.assertEqual(outcome2.persisted_count, 0)
        row = self.store.get_job_for_labeling(1)
        self.assertIsNotNone(row)
        self.assertNotIn("Summary Beta", row["description"])
        self.assertIn("Build ETL pipelines and data workflows for analytics.", row["description"])
        snapshot_row = self.store._conn.execute("SELECT job_text_snapshot FROM jobs WHERE id = 1").fetchone()
        self.assertIsNotNone(snapshot_row)
        self.assertNotIn("Summary Beta", snapshot_row["job_text_snapshot"])
        self.assertIn("Build ETL pipelines and data workflows for analytics", snapshot_row["job_text_snapshot"])

    def test_duplicate_refresh_rebuilds_polluted_snapshot_even_when_description_is_clean(self) -> None:
        payload = [
            {
                "source": "handshake",
                "external_id": "job-5c",
                "url": "https://app.joinhandshake.com/job-search/11120409?query=data+engineer+intern&page=1",
                "title": "Data Engineering Intern",
                "company": "Example",
                "location": "Remote",
                "posted_at": recent_posted_at(),
                "description": "Build ETL pipelines and data workflows for analytics.",
                "skills": [],
                "source_detail": "https://app.joinhandshake.com/job-search/11120409?query=data+engineer+intern&page=1",
            }
        ]
        with patch("job_hunter.pipeline.build_sources", return_value=[FakeSource(payload)]):
            outcome1 = run_pipeline(self.settings, self.store, None)
        self.assertEqual(outcome1.persisted_count, 1)

        self.store._conn.execute(
            """
            UPDATE jobs
            SET job_text_snapshot = ?
            WHERE id = 1
            """,
            ("TITLE: Data Engineering Intern\nSUMMARY:\nSummary Beta fake text",),
        )
        self.store._conn.commit()

        with patch("job_hunter.pipeline.build_sources", return_value=[FakeSource(payload)]):
            outcome2 = run_pipeline(self.settings, self.store, None)
        self.assertEqual(outcome2.persisted_count, 0)
        snapshot_row = self.store._conn.execute("SELECT job_text_snapshot FROM jobs WHERE id = 1").fetchone()
        self.assertIsNotNone(snapshot_row)
        self.assertNotIn("Summary Beta", snapshot_row["job_text_snapshot"])
        self.assertIn("Build ETL pipelines and data workflows for analytics", snapshot_row["job_text_snapshot"])

    def test_persisted_jobs_include_stage2_shadow_fields(self) -> None:
        class FakeSemanticResult:
            semantic_base_score = 0.88
            semantic_match_score = 0.81
            semantic_match_label = "pass"
            semantic_match_reason_codes = ["semantic_profile_data_engineering", "semantic_similarity_high"]
            semantic_research_heaviness_score = 0.07
            semantic_adjustment_reason_codes = ["semantic_penalty_masters_signal"]
            semantic_profile_id = "data_engineering"
            semantic_model_name = "fake-semantic-model"
            semantic_scorer_version = "semantic_shadow_v1"
            semantic_text_hash = "abc123"

        class FakeSemanticScorer:
            def score(self, job):
                _ = job
                return FakeSemanticResult()

        payload = [
            {
                "source": "fake",
                "external_id": "job-stage2-1",
                "url": "https://example.com/job-stage2-1",
                "title": "AI/ML Data Engineering Intern",
                "company": "Example",
                "location": "Remote - US",
                "posted_at": recent_posted_at(),
                "description": (
                    "Build production ML systems.\n"
                    "Requirements\n"
                    "- Python\n"
                    "- SQL\n"
                    "Responsibilities\n"
                    "- Build ETL pipelines\n"
                ),
                "skills": ["python", "sql"],
            }
        ]

        with patch("job_hunter.pipeline.build_sources", return_value=[FakeSource(payload)]):
            with patch("job_hunter.pipeline._build_semantic_shadow_scorer", return_value=FakeSemanticScorer()):
                outcome = run_pipeline(self.settings, self.store, None)

        self.assertEqual(outcome.persisted_count, 1)
        row = self.store._conn.execute(
            """
            SELECT role_relevance_label, role_relevance_reason_codes, policy_gate_status,
                   profile_match_score, profile_match_label, profile_match_reason_codes,
                   profile_version, scorer_version, job_text_version, job_text_snapshot,
                   semantic_match_score, semantic_match_label, semantic_match_reason_codes,
                   semantic_profile_id, semantic_model_name, semantic_scorer_version,
                   semantic_text_hash
            FROM jobs
            WHERE id = 1
            """
        ).fetchone()
        self.assertEqual(row["role_relevance_label"], "pass")
        self.assertEqual(row["policy_gate_status"], "pass")
        self.assertGreaterEqual(float(row["profile_match_score"]), 0.0)
        self.assertIn(row["profile_match_label"], {"pass", "review", "reject"})
        self.assertEqual(row["profile_version"], "default_v1")
        self.assertEqual(row["scorer_version"], "shadow_rules_v1")
        self.assertEqual(row["job_text_version"], "job_text_v1")
        self.assertIn("TITLE: AI/ML Data Engineering Intern", row["job_text_snapshot"])
        self.assertAlmostEqual(float(row["semantic_match_score"]), 0.81)
        self.assertEqual(row["semantic_match_label"], "pass")
        self.assertIn("semantic_similarity_high", str(row["semantic_match_reason_codes"]))
        self.assertEqual(row["semantic_profile_id"], "data_engineering")
        self.assertEqual(row["semantic_model_name"], "fake-semantic-model")
        self.assertEqual(row["semantic_scorer_version"], "semantic_shadow_v1")
        self.assertEqual(row["semantic_text_hash"], "abc123")

    def test_source_meta_counters_are_recorded(self) -> None:
        payload = [
            {
                "source": "fake",
                "external_id": "job-4",
                "url": "https://example.com/job-4",
                "title": "Machine Learning Intern",
                "company": "Acme",
                "location": "Remote - US",
                "posted_at": recent_posted_at(),
                "description": "Summer internship program for ML and Python",
                "skills": ["python"],
            }
        ]

        class FakeMetaSource(FakeSource):
            def get_fetch_meta(self) -> dict[str, int]:
                return {"dead_token_count": 3, "feed_error_count": 2}

        with patch("job_hunter.pipeline.build_sources", return_value=[FakeMetaSource(payload)]):
            outcome = run_pipeline(self.settings, self.store, None)

        self.assertEqual(outcome.source_stats["fake"].normalized_count, 1)
        self.assertEqual(outcome.source_stats["fake"].after_stage_1a_count, 1)
        self.assertEqual(outcome.source_stats["fake"].after_stage_1b_count, 1)
        self.assertEqual(outcome.source_stats["fake"].after_stage_1c_count, 1)
        self.assertEqual(outcome.source_stats["fake"].dead_token_count, 3)
        self.assertEqual(outcome.source_stats["fake"].feed_error_count, 2)

    def test_missing_core_fields_are_tracked_separately(self) -> None:
        payload = [
            {
                "source": "fake",
                "external_id": "missing-url",
                "url": "",
                "title": "Machine Learning Intern",
                "company": "Acme",
                "location": "Remote - US",
                "posted_at": recent_posted_at(),
                "description": "Summer internship program for ML and Python",
                "skills": ["python"],
            }
        ]

        with patch("job_hunter.pipeline.build_sources", return_value=[FakeSource(payload)]):
            outcome = run_pipeline(self.settings, self.store, None)

        self.assertEqual(outcome.normalized_count, 1)
        self.assertEqual(outcome.rejected_missing_core_fields_count, 1)
        self.assertEqual(outcome.after_stage_1a_count, 0)
        self.assertEqual(outcome.source_stats["fake"].rejected_missing_core_fields_count, 1)

    def test_title_blacklist_blocks_non_target_roles(self) -> None:
        payload = [
            {
                "source": "fake",
                "external_id": "job-5",
                "url": "https://example.com/job-5",
                "title": "University Recruiter (Contract)",
                "company": "Acme",
                "location": "Remote - US",
                "posted_at": recent_posted_at(),
                "description": "Join internship program operations for campus hiring",
                "skills": ["coordination"],
            }
        ]

        with patch("job_hunter.pipeline.build_sources", return_value=[FakeSource(payload)]):
            outcome = run_pipeline(self.settings, self.store, None)

        self.assertEqual(outcome.persisted_count, 0)
        self.assertEqual(outcome.source_stats["fake"].rejected_title_blacklist_count, 1)

    def test_data_role_gate_blocks_twilio_style_non_data_internships(self) -> None:
        payload = [
            {
                "source": "fake",
                "external_id": "twilio-1",
                "url": "https://example.com/twilio-1",
                "title": "Developer Advocacy Intern",
                "company": "Twilio",
                "location": "Remote - US",
                "posted_at": recent_posted_at(),
                "description": "Empower developers and create Python-focused content.",
                "skills": ["python"],
            },
            {
                "source": "fake",
                "external_id": "twilio-2",
                "url": "https://example.com/twilio-2",
                "title": "Technical Video Content Intern, Developer Ecosystem",
                "company": "Twilio",
                "location": "Remote - US",
                "posted_at": recent_posted_at(),
                "description": "Produce technical videos for developer ecosystem analytics dashboards.",
                "skills": ["analytics"],
            },
        ]

        with patch("job_hunter.pipeline.build_sources", return_value=[FakeSource(payload)]):
            outcome = run_pipeline(self.settings, self.store, None)

        self.assertEqual(outcome.persisted_count, 0)
        self.assertEqual(outcome.source_stats["fake"].rejected_data_role_count, 2)

    def test_research_heavy_ms_role_is_not_hard_rejected(self) -> None:
        payload = [
            {
                "source": "fake",
                "external_id": "pinterest-ms-1",
                "url": "https://example.com/pinterest-ms-1",
                "title": "Master's Fall Machine Learning Internship (ATG - Visual Search)",
                "company": "Pinterest",
                "location": "US",
                "posted_at": recent_posted_at(),
                "description": (
                    "Working towards a Master's degree in Computer Science. "
                    "Preferred qualifications: Publications in machine learning and strong passion for research."
                ),
                "skills": ["python", "pytorch"],
            }
        ]

        with patch("job_hunter.pipeline.build_sources", return_value=[FakeSource(payload)]):
            outcome = run_pipeline(self.settings, self.store, None)

        self.assertEqual(outcome.source_stats["fake"].rejected_policy_gate_count, 0)
        self.assertEqual(outcome.persisted_count, 1)


if __name__ == "__main__":
    unittest.main()
