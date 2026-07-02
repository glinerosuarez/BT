from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from job_hunter.config import Settings
from job_hunter.models import JobRecord
from job_hunter.storage import JobStore
from job_hunter.tailor_jobs import main
from job_hunter.tailoring.service import TailoringService
from job_hunter.tailoring.types import TailoringResult


class FakeProvider:
    provider_name = "anthropic"
    model_name = "fake-claude"

    def __init__(self) -> None:
        self.calls: list[int] = []

    def generate(self, *, profile, job_context) -> TailoringResult:
        self.calls.append(job_context.job_id)
        return TailoringResult(
            resume_markdown=f"# Resume\n\nTailored for {job_context.company}\n",
            cover_letter_markdown=f"# Cover Letter\n\nTargeting {job_context.title}\n",
            highlight_requirements=["Python", "SQL"],
            evidence_map=[
                {
                    "job_requirement": "Python",
                    "profile_evidence": "Built Python data workflows.",
                }
            ],
            provider_name=self.provider_name,
            model_name=self.model_name,
        )


def make_settings(db_path: str, profile_root: str, output_root: str) -> Settings:
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
        source_probe_limit_per_run=5,
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
        tailoring_profile_root=profile_root,
        tailoring_output_root=output_root,
        tailoring_provider="anthropic",
        tailoring_anthropic_model="claude-fake",
        tailoring_batch_default_limit=10,
    )


class TailorJobsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = str(self.root / "test.db")
        self.profile_root = self.root / "profiles"
        self.output_root = self.root / "artifacts" / "tailoring"
        self._write_profile("default")
        self.settings = make_settings(self.db_path, str(self.profile_root), str(self.output_root))
        self.store = JobStore(self.db_path)
        self.store.insert_job(
            JobRecord(
                source="fake",
                external_id="job-1",
                url="https://example.com/job-1",
                title="Machine Learning Intern",
                company="Acme",
                location="Remote - US",
                is_internship=True,
                posted_at="2026-06-28T00:00:00+00:00",
                description="Build ML systems in Python and SQL.",
                ingested_at="2026-06-28T01:00:00+00:00",
                relevance_score=5.0,
                eligibility_confidence=0.7,
                eligibility_status="ambiguous",
                profile_match_score=0.88,
                profile_match_label="pass",
                job_text_version="job_text_v1",
                job_text_snapshot="TITLE: Machine Learning Intern\nSUMMARY:\n- Build ML systems in Python and SQL.",
            ),
            "dedupe-1",
        )
        self.store.insert_job(
            JobRecord(
                source="fake",
                external_id="job-2",
                url="https://example.com/job-2",
                title="Analytics Intern",
                company="Beta",
                location="New York, NY",
                is_internship=True,
                posted_at="2026-06-27T00:00:00+00:00",
                description="Work with dashboards and SQL.",
                ingested_at="2026-06-27T01:00:00+00:00",
                relevance_score=4.0,
                eligibility_confidence=0.7,
                eligibility_status="ambiguous",
                profile_match_score=0.52,
                profile_match_label="review",
            ),
            "dedupe-2",
        )

    def tearDown(self) -> None:
        self.store.close()
        self.temp_dir.cleanup()

    def test_generate_writes_artifacts_and_persists_row(self) -> None:
        buffer = io.StringIO()
        provider = FakeProvider()
        with patch("job_hunter.tailor_jobs.load_settings", return_value=self.settings):
            with patch("job_hunter.tailor_jobs._build_provider", return_value=provider):
                with patch("job_hunter.tailoring.service._render_markdown_pdf", side_effect=self._write_fake_pdf):
                    with patch("sys.argv", ["tailor_jobs.py", "generate", "--job-id", "1"]):
                        with redirect_stdout(buffer):
                            rc = main()

        self.assertEqual(rc, 0)
        payload = json.loads(buffer.getvalue().strip())
        self.assertEqual(payload["job_id"], 1)
        artifact_dir = Path(payload["output_dir"])
        self.assertTrue((artifact_dir / "resume.md").exists())
        self.assertTrue((artifact_dir / "cover_letter.md").exists())
        self.assertTrue((artifact_dir / "resume.pdf").exists())
        self.assertTrue((artifact_dir / "cover_letter.pdf").exists())
        self.assertTrue((artifact_dir / "metadata.json").exists())
        row = self.store.get_tailoring_artifact(payload["artifact_id"])
        self.assertIsNotNone(row)
        self.assertIn("Tailored for Acme", str(row["resume_markdown"]))
        self.assertEqual(provider.calls, [1])

    def test_batch_uses_stage2_candidates_and_skips_unchanged_rows(self) -> None:
        provider = FakeProvider()
        with patch("job_hunter.tailor_jobs.load_settings", return_value=self.settings):
            with patch("job_hunter.tailor_jobs._build_provider", return_value=provider):
                with patch("job_hunter.tailoring.service._render_markdown_pdf", side_effect=self._write_fake_pdf):
                    with patch("sys.argv", ["tailor_jobs.py", "generate", "--job-id", "1"]):
                        with redirect_stdout(io.StringIO()):
                            first_rc = main()
        self.assertEqual(first_rc, 0)

        buffer = io.StringIO()
        with patch("job_hunter.tailor_jobs.load_settings", return_value=self.settings):
            with patch("job_hunter.tailor_jobs._build_provider", return_value=provider):
                with patch("job_hunter.tailoring.service._render_markdown_pdf", side_effect=self._write_fake_pdf):
                    with patch("sys.argv", ["tailor_jobs.py", "batch", "--limit", "5"]):
                        with redirect_stdout(buffer):
                            rc = main()

        self.assertEqual(rc, 0)
        payload = json.loads(buffer.getvalue().strip())
        self.assertEqual(payload["processed_count"], 2)
        self.assertEqual(payload["success_count"], 2)
        self.assertEqual(provider.calls.count(1), 1)
        self.assertEqual(provider.calls.count(2), 1)
        reused = [row for row in payload["successes"] if row["job_id"] == 1][0]
        self.assertFalse(reused["created"])

    def test_show_renders_json_payload(self) -> None:
        provider = FakeProvider()
        with patch("job_hunter.tailor_jobs.load_settings", return_value=self.settings):
            with patch("job_hunter.tailor_jobs._build_provider", return_value=provider):
                with patch("job_hunter.tailoring.service._render_markdown_pdf", side_effect=self._write_fake_pdf):
                    with patch("sys.argv", ["tailor_jobs.py", "generate", "--job-id", "1"]):
                        with redirect_stdout(io.StringIO()):
                            rc = main()
        self.assertEqual(rc, 0)

        artifact_row = self.store.list_tailoring_artifacts(limit=1)[0]
        buffer = io.StringIO()
        with patch("job_hunter.tailor_jobs.load_settings", return_value=self.settings):
            with patch("sys.argv", ["tailor_jobs.py", "show", "--artifact-id", str(artifact_row["id"]), "--format", "json"]):
                with redirect_stdout(buffer):
                    rc = main()

        self.assertEqual(rc, 0)
        payload = json.loads(buffer.getvalue())
        self.assertEqual(payload["artifact_id"], int(artifact_row["id"]))
        self.assertIn("resume_markdown", payload)
        self.assertEqual(payload["highlight_requirements"], ["Python", "SQL"])

    def test_profile_loads_default_preferences_for_non_default_profile(self) -> None:
        self._write_profile("ml_eng_intern", profile_preferences="- Emphasize production ML.\n")
        service = TailoringService(settings=self.settings, store=self.store, provider=FakeProvider())
        profile = service.load_profile("ml_eng_intern")
        self.assertIn("Emphasize ML systems and production work.", profile.preferences_markdown)
        self.assertIn("Emphasize production ML.", profile.preferences_markdown)
        self.assertIn("Emphasize ML systems and production work.", profile.shared_preferences_markdown)
        self.assertIn("Emphasize production ML.", profile.profile_preferences_markdown)

    def test_build_job_context_extracts_company_context(self) -> None:
        self.store.insert_job(
            JobRecord(
                source="fake",
                external_id="job-3",
                url="https://example.com/job-3",
                title="AI Intern",
                company="Gamma",
                location="Remote",
                is_internship=True,
                posted_at="2026-06-29T00:00:00+00:00",
                description=(
                    "About Gamma Gamma builds data tools for healthcare. "
                    "Our values emphasize ownership and fast iteration. "
                    "The Role You will build ETL jobs."
                ),
                ingested_at="2026-06-29T01:00:00+00:00",
            ),
            "dedupe-3",
        )
        service = TailoringService(settings=self.settings, store=self.store, provider=FakeProvider())
        job_context = service.build_job_context(3)
        self.assertIn("About Gamma", job_context.company_context)
        self.assertIn("ownership and fast iteration", job_context.company_context)
        self.assertNotIn("You will build ETL jobs", job_context.company_context)

    def test_missing_profile_file_returns_error(self) -> None:
        incomplete_profile = self.profile_root / "missing_cover"
        incomplete_profile.mkdir(parents=True, exist_ok=True)
        (incomplete_profile / "resume.md").write_text("# Resume\n", encoding="utf-8")

        buffer = io.StringIO()
        with patch("job_hunter.tailor_jobs.load_settings", return_value=self.settings):
            with patch("job_hunter.tailor_jobs._build_provider", return_value=FakeProvider()):
                with patch("sys.argv", ["tailor_jobs.py", "generate", "--job-id", "1", "--profile", "missing_cover"]):
                    with redirect_stdout(buffer):
                        rc = main()

        self.assertEqual(rc, 1)
        self.assertIn("Missing required profile file", buffer.getvalue())

    def _write_profile(self, profile_name: str, profile_preferences: str | None = None) -> None:
        profile_dir = self.profile_root / profile_name
        profile_dir.mkdir(parents=True, exist_ok=True)
        (profile_dir / "resume.md").write_text(
            "# Experience\n\n- Built Python data workflows.\n",
            encoding="utf-8",
        )
        (profile_dir / "cover_letter.md").write_text(
            "# Cover Letter\n\nDear Hiring Team,\n",
            encoding="utf-8",
        )
        if profile_preferences is None:
            profile_preferences = "- Emphasize ML systems and production work.\n"
        (profile_dir / "preferences.md").write_text(profile_preferences, encoding="utf-8")

    def _write_fake_pdf(self, markdown_text: str, output_path: Path, *, title: str) -> None:
        _ = markdown_text, title
        output_path.write_bytes(b"%PDF-1.4\n%fake\n")


if __name__ == "__main__":
    unittest.main()
