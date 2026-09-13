from __future__ import annotations

import re
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from job_hunter.config import Settings, load_settings
from job_hunter.keywords import (
    FULL_TIME_BACKEND_ADJACENT_TITLE_PATTERNS,
    MANAGEMENT_TITLE_PATTERNS,
)
from job_hunter.models import JobRecord
from job_hunter.notify import _format_alert
from job_hunter.pipeline import (
    _detect_job_type_from_provenance,
    _evaluate_eligibility,
    _fails_policy_gate,
    _passes_data_role_gate,
    _score_relevance,
    run_pipeline,
)
from job_hunter.storage import JobStore
from tests.test_pipeline import FakeSource, make_settings


class FakeNotifier:
    def __init__(self) -> None:
        self.sent = 0
        self.alerts: list[str] = []

    def send(self, job: JobRecord) -> bool:
        self.sent += 1
        self.alerts.append(_format_alert(job))
        return True


def make_pipeline_settings(db_path: str, job_target_type: str = "all") -> Settings:
    base = make_settings(db_path)
    return replace(
        base,
        job_target_type=job_target_type,
        min_data_signal_count=1,
    )


class FullTimeModuleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.management_regexes = [
            re.compile(p, re.IGNORECASE) for p in MANAGEMENT_TITLE_PATTERNS.values()
        ]

    def test_management_title_rejection(self) -> None:
        management_titles = [
            "VP of Engineering",
            "Vice President, Engineering",
            "Director of Software Engineering",
            "Senior Director of Data Science",
            "Engineering Manager",
            "Software Development Manager",
            "Head of AI",
            "Head of Distributed Systems",
            "Staff Software Engineer",
            "Staff Backend Engineer",
            "Principal Software Engineer",
            "Principal Distributed Systems Engineer",
        ]
        for title in management_titles:
            with self.subTest(title=title):
                self.assertTrue(
                    any(rx.search(title) for rx in self.management_regexes),
                    f"Expected '{title}' to be rejected by management regexes",
                )

        allowed_titles = [
            "Junior Software Engineer",
            "Associate Backend Engineer",
            "Software Engineer",
            "Software Engineer I",
            "Software Engineer II",
            "Senior Software Engineer",
            "Senior Backend Engineer",
            "Senior Distributed Systems Engineer",
            "Machine Learning Engineer",
            "Data Engineer",
            "Distributed Systems Engineer",
            "Platform Engineer",
            "Infrastructure Engineer",
        ]
        for title in allowed_titles:
            with self.subTest(title=title):
                self.assertFalse(
                    any(rx.search(title) for rx in self.management_regexes),
                    f"Expected '{title}' NOT to be rejected by management regexes",
                )

    def test_detect_job_type_from_provenance(self) -> None:
        # Explicit internship titles
        self.assertEqual(
            _detect_job_type_from_provenance("github_repo", "", "Software Engineer Intern", ""),
            "internship",
        )
        self.assertEqual(
            _detect_job_type_from_provenance("lever", "", "Data Science Internship", ""),
            "internship",
        )
        self.assertEqual(
            _detect_job_type_from_provenance("greenhouse", "", "Software Co-op", ""),
            "internship",
        )

        # Handshake query URLs
        self.assertEqual(
            _detect_job_type_from_provenance(
                "handshake",
                "https://app.joinhandshake.com/job-search/1?jobType=1",
                "Software Engineer",
                "",
            ),
            "full_time",
        )
        self.assertEqual(
            _detect_job_type_from_provenance(
                "handshake",
                "https://app.joinhandshake.com/job-search/1?jobType=3",
                "Software Engineer",
                "",
            ),
            "internship",
        )

        # LinkedIn query URLs
        self.assertEqual(
            _detect_job_type_from_provenance(
                "linkedin",
                "https://www.linkedin.com/jobs/search/?f_JT=F&keywords=software",
                "Software Engineer",
                "",
            ),
            "full_time",
        )
        self.assertEqual(
            _detect_job_type_from_provenance(
                "linkedin",
                "https://www.linkedin.com/jobs/search/?f_JT=I&keywords=software",
                "Software Engineer",
                "",
            ),
            "internship",
        )

        # Default IC titles without intern keyword are full-time
        self.assertEqual(
            _detect_job_type_from_provenance("ashby", "", "Backend Engineer", ""),
            "full_time",
        )
        self.assertEqual(
            _detect_job_type_from_provenance("greenhouse", "", "Distributed Systems Engineer", ""),
            "full_time",
        )

    def test_full_time_policy_gate(self) -> None:
        # Full-time jobs should NOT be rejected for mentions of undergraduate or student restrictions
        undergrad_only_record = JobRecord(
            source="linkedin",
            external_id="ft-undergrad",
            url="https://example.com/ft-undergrad",
            title="Software Engineer - University Grad",
            company="Stripe",
            location="Remote - US",
            job_type="full_time",
            is_internship=False,
            posted_at=datetime.now(timezone.utc).isoformat(),
            description="Must be currently enrolled in an undergraduate degree program.",
        )
        reject_rx = [re.compile(r"\bdoctoral\b", re.IGNORECASE)]
        # For internship: rejected by builtin policy (mentions_undergraduate_only)
        self.assertTrue(_fails_policy_gate(undergrad_only_record, reject_rx, is_full_time=False))
        # For full-time: allowed!
        self.assertFalse(_fails_policy_gate(undergrad_only_record, reject_rx, is_full_time=True))

    def test_full_time_eligibility_enforcement(self) -> None:
        # Visa / clearance restrictions MUST still trigger rejection for full-time jobs
        visa_blocked_record = JobRecord(
            source="linkedin",
            external_id="ft-visa",
            url="https://example.com/ft-visa",
            title="Software Engineer",
            company="Defense Corp",
            location="Remote - US",
            job_type="full_time",
            is_internship=False,
            posted_at=datetime.now(timezone.utc).isoformat(),
            description="Must be a US Citizen. Only US Citizens are eligible for security clearance. No visa sponsorship.",
        )
        status, conf, neg_reasons, pos_reasons = _evaluate_eligibility(visa_blocked_record)
        self.assertEqual(status, "reject")
        self.assertEqual(conf, 0.0)
        self.assertTrue(len(neg_reasons) > 0)

    def test_full_time_passes_data_role_gate(self) -> None:
        ft_backend = JobRecord(
            source="greenhouse",
            external_id="ft-3",
            url="https://example.com/ft-3",
            title="Distributed Systems Engineer",
            company="Datadog",
            location="New York, NY",
            job_type="full_time",
            is_internship=False,
            posted_at=datetime.now(timezone.utc).isoformat(),
            description="Build scalable high throughput telemetry engines in Go and Rust.",
        )
        data_rx = [re.compile(r"\bdata\b", re.IGNORECASE)]
        non_data_rx = [re.compile(r"\bsales\b", re.IGNORECASE)]
        backend_rx = [
            re.compile(p, re.IGNORECASE)
            for p in FULL_TIME_BACKEND_ADJACENT_TITLE_PATTERNS.values()
        ]

        self.assertTrue(
            _passes_data_role_gate(
                ft_backend,
                data_rx,
                non_data_rx,
                1,
                full_time_backend_adjacent_title_regexes=backend_rx,
            )
        )

    def test_alert_formatting_full_time_vs_internship(self) -> None:
        ft_job = JobRecord(
            source="github_new_grad_repo",
            external_id="ft-alert",
            url="https://example.com/alert-ft",
            title="Software Engineer - New Grad",
            company="Meta",
            location="Menlo Park, CA",
            job_type="full_time",
            is_internship=False,
            relevance_score=4.5,
            eligibility_status="eligible",
            eligibility_confidence=0.9,
            posted_at=datetime.now(timezone.utc).isoformat(),
        )
        alert_text = _format_alert(ft_job)
        self.assertIn("[Full-Time Alert]", alert_text)
        self.assertNotIn("[Internship Alert]", alert_text)

        intern_job = JobRecord(
            source="github_repo",
            external_id="intern-alert",
            url="https://example.com/alert-intern",
            title="Software Engineer Intern",
            company="Meta",
            location="Menlo Park, CA",
            job_type="internship",
            is_internship=True,
            relevance_score=4.5,
            eligibility_status="eligible",
            eligibility_confidence=0.9,
            posted_at=datetime.now(timezone.utc).isoformat(),
        )
        intern_alert = _format_alert(intern_job)
        self.assertIn("[Internship Alert]", intern_alert)
        self.assertNotIn("[Full-Time Alert]", intern_alert)

    def test_storage_job_type_persistence_and_sync(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            store = JobStore(str(db_path))

            ft_job = JobRecord(
                source="test",
                external_id="id-ft",
                url="https://example.com/id-ft",
                title="Backend Engineer",
                company="Acme",
                location="Remote",
                job_type="full_time",
                is_internship=False,
                posted_at=datetime.now(timezone.utc).isoformat(),
                description="Python and Postgres",
            )
            is_new = store.insert_job(ft_job, dedupe_key="id-ft")
            self.assertTrue(is_new)

            row = store._conn.execute(
                "SELECT job_type, is_internship FROM jobs WHERE external_id = ?",
                ("id-ft",),
            ).fetchone()
            self.assertEqual(row["job_type"], "full_time")
            self.assertEqual(row["is_internship"], 0)

            intern_job = JobRecord(
                source="test",
                external_id="id-intern",
                url="https://example.com/id-intern",
                title="Backend Engineer Intern",
                company="Acme",
                location="Remote",
                job_type="internship",
                is_internship=True,
                posted_at=datetime.now(timezone.utc).isoformat(),
                description="Python and Postgres",
            )
            is_new_intern = store.insert_job(intern_job, dedupe_key="id-intern")
            self.assertTrue(is_new_intern)

            row_intern = store._conn.execute(
                "SELECT job_type, is_internship FROM jobs WHERE external_id = ?",
                ("id-intern",),
            ).fetchone()
            self.assertEqual(row_intern["job_type"], "internship")
            self.assertEqual(row_intern["is_internship"], 1)

    def test_pipeline_job_type_filtering(self) -> None:
        now_str = datetime.now(timezone.utc).isoformat()
        raw_intern = {
            "source": "fake",
            "external_id": "intern-1",
            "url": "https://example.com/intern-1",
            "title": "Machine Learning Engineer Intern",
            "company": "Company A",
            "location": "Remote - US",
            "posted_at": now_str,
            "description": "Python, PyTorch, ML data pipelines. Open to CPT/OPT.",
            "skills": ["python", "pytorch", "machine learning"],
        }
        raw_ft = {
            "source": "fake",
            "external_id": "ft-1",
            "url": "https://example.com/ft-1",
            "title": "Software Engineer",
            "company": "Company B",
            "location": "Remote - US",
            "posted_at": now_str,
            "description": "Distributed systems, Python, backend infrastructure. Open to sponsorship.",
            "skills": ["python", "distributed systems", "backend"],
        }
        raw_mgmt = {
            "source": "fake",
            "external_id": "mgmt-1",
            "url": "https://example.com/mgmt-1",
            "title": "Engineering Manager",
            "company": "Company C",
            "location": "Remote - US",
            "posted_at": now_str,
            "description": "Lead backend teams. Open to sponsorship.",
            "skills": ["management"],
        }

        # Case 1: job_target_type = "all" -> both intern and FT persisted, mgmt rejected
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test_all.db"
            store = JobStore(str(db_path))
            notifier = FakeNotifier()
            settings = make_pipeline_settings(str(db_path), job_target_type="all")

            with patch("job_hunter.pipeline.build_sources", return_value=[FakeSource([raw_intern, raw_ft, raw_mgmt])]):
                outcome = run_pipeline(settings, store, notifier)

            self.assertEqual(outcome.persisted_count, 2)
            self.assertEqual(notifier.sent, 2)
            self.assertTrue(any("[Full-Time Alert]" in a for a in notifier.alerts))
            self.assertTrue(any("[Internship Alert]" in a for a in notifier.alerts))

        # Case 2: job_target_type = "internship" -> only intern persisted
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test_intern.db"
            store = JobStore(str(db_path))
            notifier = FakeNotifier()
            settings = make_pipeline_settings(str(db_path), job_target_type="internship")

            with patch("job_hunter.pipeline.build_sources", return_value=[FakeSource([raw_intern, raw_ft, raw_mgmt])]):
                outcome = run_pipeline(settings, store, notifier)

            self.assertEqual(outcome.persisted_count, 1)
            self.assertEqual(notifier.sent, 1)
            self.assertTrue(all("[Internship Alert]" in a for a in notifier.alerts))

        # Case 3: job_target_type = "full_time" -> only FT persisted
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test_ft.db"
            store = JobStore(str(db_path))
            notifier = FakeNotifier()
            settings = make_pipeline_settings(str(db_path), job_target_type="full_time")

            with patch("job_hunter.pipeline.build_sources", return_value=[FakeSource([raw_intern, raw_ft, raw_mgmt])]):
                outcome = run_pipeline(settings, store, notifier)

            self.assertEqual(outcome.persisted_count, 1)
            self.assertEqual(notifier.sent, 1)
            self.assertTrue(all("[Full-Time Alert]" in a for a in notifier.alerts))


if __name__ == "__main__":
    unittest.main()
