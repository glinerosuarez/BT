from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from job_hunter.config import Settings
from job_hunter.funnel_report import main
from job_hunter.models import PipelineOutcome, SourceRunStats
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
        title_blacklist_patterns=[],
        data_role_title_patterns=[],
        non_data_title_patterns=[],
        policy_reject_patterns=[],
        min_data_signal_count=2,
        greenhouse_token_file=None,
        lever_token_file=None,
        rss_feed_file=None,
        greenhouse_quarantine_file=None,
        lever_quarantine_file=None,
        rss_quarantine_file=None,
        source_failure_quarantine_threshold=2,
        source_restore_success_threshold=2,
        handshake_profile_dir=".handshake-profile",
        handshake_headless=True,
        handshake_max_results=25,
        handshake_page_timeout_seconds=30,
        usajobs_user_agent=None,
        usajobs_auth_key=None,
        usajobs_results_per_page=250,
        adzuna_app_id=None,
        adzuna_app_key=None,
        adzuna_country="us",
        adzuna_pages=2,
    )


class FunnelReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "test.db")
        self.settings = make_settings(self.db_path)
        self.store = JobStore(self.db_path)

    def tearDown(self) -> None:
        self.store.close()
        self.temp_dir.cleanup()

    def test_cli_prints_latest_run_report(self) -> None:
        outcome = PipelineOutcome(
            source_count=10,
            normalized_count=10,
            rejected_missing_core_fields_count=1,
            after_stage_1a_count=8,
            after_stage_1b_count=3,
            after_stage_1c_count=2,
            passed_filter_count=2,
            persisted_count=1,
            notified_count=0,
            duplicate_count=1,
            error_count=0,
            source_stats={
                "fake": SourceRunStats(
                    fetched_count=10,
                    normalized_count=10,
                    rejected_missing_core_fields_count=1,
                    rejected_age_count=2,
                    after_stage_1a_count=8,
                    rejected_internship_count=4,
                    rejected_us_scope_count=1,
                    rejected_title_blacklist_count=0,
                    rejected_data_role_count=0,
                    after_stage_1b_count=3,
                    rejected_policy_gate_count=1,
                    after_stage_1c_count=2,
                    rejected_eligibility_count=0,
                    rejected_relevance_count=0,
                    persisted_count=1,
                    notified_count=0,
                    duplicate_count=1,
                    error_count=0,
                )
            },
        )
        self.store.log_run(outcome)

        stdout = io.StringIO()
        with (
            patch("job_hunter.funnel_report.load_settings", return_value=self.settings),
            patch("sys.argv", ["funnel_report.py"]),
            redirect_stdout(stdout),
        ):
            exit_code = main()

        text = stdout.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("overall", text)
        self.assertIn("after_stage_1a=8 after_stage_1b=3 after_stage_1c=2", text)
        self.assertIn("fake", text)


if __name__ == "__main__":
    unittest.main()
