from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from job_hunter.config import Settings
from job_hunter.pipeline import run_pipeline
from job_hunter.storage import JobStore
from job_hunter.stage2_report import main


class FakeSource:
    name = "fake"

    def __init__(self, payload):
        self.payload = payload

    def fetch(self, timeout_seconds: int):
        _ = timeout_seconds
        return self.payload


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
        data_role_title_patterns=[r"\b(machine learning|ml)\b", r"\bdata (science|scientist)\b", r"\bdata engineer(ing)?\b"],
        non_data_title_patterns=[r"\bdeveloper advocacy\b"],
        policy_reject_patterns=[r"\bph\.?d\.?\b"],
        min_data_signal_count=1,
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


class Stage2ReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "test.db")
        self.settings = make_settings(self.db_path)
        self.store = JobStore(self.db_path)
        payload = [
            {
                "source": "fake",
                "external_id": "job-1",
                "url": "https://example.com/job-1",
                "title": "Data Engineering Intern",
                "company": "Finz",
                "location": "Remote - US",
                "posted_at": "2026-06-16T00:00:00+00:00",
                "description": "Build production ML systems with Python and SQL for our internship program.",
                "skills": ["python", "sql"],
            }
        ]
        with patch("job_hunter.pipeline.build_sources", return_value=[FakeSource(payload)]):
            run_pipeline(self.settings, self.store, None)
        self.store.close()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_list_renders_shadow_rows(self) -> None:
        buffer = io.StringIO()
        with patch("job_hunter.stage2_report.load_settings", return_value=self.settings):
            with patch("sys.argv", ["stage2_report.py", "list", "--limit", "5"]):
                with redirect_stdout(buffer):
                    rc = main()
        output = buffer.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("Data Engineering Intern", output)
        self.assertIn("stage2=", output)

    def test_show_renders_job_text_snapshot(self) -> None:
        buffer = io.StringIO()
        with patch("job_hunter.stage2_report.load_settings", return_value=self.settings):
            with patch("sys.argv", ["stage2_report.py", "show", "--job-id", "1"]):
                with redirect_stdout(buffer):
                    rc = main()
        output = buffer.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("job_text_snapshot:", output)
        self.assertIn("TITLE: Data Engineering Intern", output)


if __name__ == "__main__":
    unittest.main()
