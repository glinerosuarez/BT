from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from job_hunter.apply.adapters.greenhouse import GreenhouseAdapter
from job_hunter.apply.adapters.linkedin import LinkedInEasyApplyAdapter
from job_hunter.apply.email_codes import extract_verification_code
from job_hunter.apply.profile_loader import ProfileValidationError, load_application_inputs
from job_hunter.apply.resolver import AnswerResolver, ResolutionError
from job_hunter.apply.service import ApplicationService
from job_hunter.apply.types import Blocker, SubmitResult
from job_hunter.config import Settings
from job_hunter.models import JobRecord
from job_hunter.storage import JobStore
from job_hunter.tailoring.service import TailoringService
from job_hunter.tailoring.types import TailoringResult


class FakeProvider:
    provider_name = "anthropic"
    model_name = "fake-claude"

    def generate(self, *, profile, job_context) -> TailoringResult:
        return TailoringResult(
            resume_markdown="# Resume\n",
            cover_letter_markdown="# Cover Letter\n",
            highlight_requirements=["Python"],
            evidence_map=[{"job_requirement": "Python", "profile_evidence": "Python"}],
            provider_name=self.provider_name,
            model_name=self.model_name,
        )


class FakePage:
    def __init__(self, *, url: str, fields=None, confirmation=None, easy_apply=True, greenhouse=True) -> None:
        self.url = url
        self._fields = list(fields or [])
        self._confirmation = dict(confirmation or {})
        self._easy_apply = easy_apply
        self._greenhouse = greenhouse
        self._login_wall = False
        self._captcha = False
        self._unsupported_widget = False
        self._ambiguous_submit = False
        self.external_url = ""
        self.values: dict[str, str] = {}
        self.submitted = False
        self.verification_code = ""

    def goto(self, url: str, *, wait_until: str = "domcontentloaded") -> None:
        self.url = url

    def content(self) -> str:
        return "easy apply" if self._easy_apply else "external apply"

    def extract_fields(self):
        return self._fields

    def set_field(self, field, value: str) -> None:
        self.values[str(field.get("field_name") or field.get("question_text"))] = value

    def submit_application(self) -> None:
        self.submitted = True

    def extract_confirmation(self):
        return self._confirmation

    def detect_easy_apply(self) -> bool:
        return self._easy_apply

    def extract_external_apply_url(self) -> str:
        return self.external_url

    def detect_greenhouse(self) -> bool:
        return self._greenhouse

    def detect_login_wall(self) -> bool:
        return self._login_wall

    def detect_captcha(self) -> bool:
        return self._captcha

    def detect_unsupported_widget(self) -> bool:
        return self._unsupported_widget

    def detect_ambiguous_submit_state(self) -> bool:
        return self._ambiguous_submit

    def fill_email_verification_code(self, code: str) -> None:
        self.verification_code = code
        if code:
            self._confirmation = {"message": "Application submitted"}


class FakeSession:
    def __init__(self, page: FakePage) -> None:
        self.page = page

    def new_page(self) -> FakePage:
        return self.page

    def close(self) -> None:
        return None


class FakeBrowserManager:
    def __init__(self, page: FakePage) -> None:
        self.page = page

    def open(self, *, adapter_name: str):
        return FakeSession(self.page)


class ApplyJobsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = str(self.root / "test.db")
        self.profile_root = self.root / "profiles"
        self.output_root = self.root / "artifacts"
        self._write_profile("default")
        self.settings = make_settings(self.db_path, str(self.profile_root), str(self.output_root))
        self.store = JobStore(self.db_path)
        self.store.insert_job(
            JobRecord(
                source="linkedin",
                external_id="li-1",
                url="https://www.linkedin.com/jobs/view/1",
                title="ML Intern",
                company="Acme",
                location="Remote",
                is_internship=True,
                posted_at="2026-06-30T00:00:00+00:00",
                description="Build ML systems.",
                ingested_at="2026-06-30T01:00:00+00:00",
                profile_match_score=0.95,
                profile_match_label="pass",
                job_text_version="job_text_v1",
                job_text_snapshot="TITLE: ML Intern",
            ),
            "li-1",
        )
        self.store.insert_job(
            JobRecord(
                source="greenhouse",
                external_id="gh-1",
                url="https://boards.greenhouse.io/acme/jobs/1",
                title="Data Intern",
                company="Beta",
                location="Remote",
                is_internship=True,
                posted_at="2026-06-30T00:00:00+00:00",
                description="Analyze data.",
                ingested_at="2026-06-30T01:00:00+00:00",
                profile_match_score=0.72,
                profile_match_label="review",
            ),
            "gh-1",
        )
        self.tailoring_service = TailoringService(settings=self.settings, store=self.store, provider=FakeProvider())
        with patch("job_hunter.tailoring.service._render_markdown_pdf", side_effect=self._write_fake_pdf):
            self.tailoring_service.generate_for_job(job_id=1, profile_name="default")
            self.tailoring_service.generate_for_job(job_id=2, profile_name="default")

    def tearDown(self) -> None:
        self.store.close()
        self.temp_dir.cleanup()

    def test_schema_migration_creates_application_tables(self) -> None:
        tables = self.store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('application_runs', 'application_steps')"
        ).fetchall()
        self.assertEqual({row["name"] for row in tables}, {"application_runs", "application_steps"})

    def test_profile_loader_rejects_missing_required_fields(self) -> None:
        broken_dir = self.profile_root / "broken"
        broken_dir.mkdir(parents=True, exist_ok=True)
        (broken_dir / "application_profile.json").write_text(json.dumps({"identity": {}}), encoding="utf-8")
        (broken_dir / "application_answers.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(ProfileValidationError):
            load_application_inputs(str(self.profile_root), "broken")

    def test_answer_resolver_precedence(self) -> None:
        profile, answers = load_application_inputs(str(self.profile_root), "default")
        answers.field_defaults["veteran_status"] = "Decline"
        resolver = AnswerResolver(profile=profile, answers=answers)
        self.assertEqual(resolver.resolve(question_text="What is your full name?").source, "structured:identity.full_name")
        self.assertEqual(
            resolver.resolve(question_text="Are you willing to complete a background check?").source,
            "override:exact",
        )
        self.assertEqual(
            resolver.resolve(question_text="How did you hear about us?").source,
            "override:contains",
        )
        self.assertEqual(
            resolver.resolve(question_text="Please share your pronouns").source,
            "override:regex",
        )
        self.assertEqual(
            resolver.resolve(question_text="veteran status", field_name="veteran_status").source,
            "default:veteran_status",
        )
        with self.assertRaises(ResolutionError):
            resolver.resolve(question_text="What is your favorite database?")

    def test_submit_refuses_non_pass_without_force(self) -> None:
        service = self._service(FakePage(url="https://boards.greenhouse.io/acme/jobs/1", easy_apply=False))
        with self.assertRaises(RuntimeError):
            service.submit_job(job_id=2, profile_name="default", force=False)

    def test_missing_tailoring_artifact_triggers_generation(self) -> None:
        service = self._service(
            FakePage(
                url="https://www.linkedin.com/jobs/view/1",
                fields=[{"field_name": "identity.email", "question_text": "Email", "field_type": "text", "required": True}],
                confirmation={"message": "Submitted"},
            )
        )
        run = service.submit_job(job_id=1, profile_name="default", force=False)
        artifact = self.store.find_latest_tailoring_artifact(job_id=1, profile_name="default")
        self.assertIsNotNone(artifact)
        self.assertEqual(run.status, "submitted")

    def test_force_allows_non_pass_jobs(self) -> None:
        service = self._service(
            FakePage(
                url="https://boards.greenhouse.io/acme/jobs/1",
                fields=[{"field_name": "identity.full_name", "question_text": "Full name", "field_type": "text", "required": True}],
                confirmation={"message": "Submitted"},
                easy_apply=False,
            )
        )
        run = service.submit_job(job_id=2, profile_name="default", force=True)
        self.assertEqual(run.status, "submitted")

    def test_duplicate_submitted_run_is_skipped_unless_forced(self) -> None:
        page = FakePage(
            url="https://www.linkedin.com/jobs/view/1",
            fields=[{"field_name": "identity.email", "question_text": "Email", "field_type": "text", "required": True}],
            confirmation={"message": "Submitted"},
        )
        service = self._service(page)
        first = service.submit_job(job_id=1, profile_name="default", force=False)
        second = service.submit_job(job_id=1, profile_name="default", force=False)
        third = service.submit_job(job_id=1, profile_name="default", force=True)
        self.assertEqual(first.status, "submitted")
        self.assertEqual(second.status, "skipped")
        self.assertEqual(third.status, "submitted")

    def test_linkedin_adapter_uploads_and_confirms(self) -> None:
        adapter = LinkedInEasyApplyAdapter()
        page = FakePage(
            url="https://www.linkedin.com/jobs/view/1",
            fields=[
                {"field_name": "identity.email", "question_text": "Email", "field_type": "text", "required": True},
                {"field_name": "resume", "question_text": "Resume", "field_type": "file", "required": True},
                {"field_name": "cover_letter", "question_text": "Cover Letter", "field_type": "file", "required": True},
            ],
            confirmation={"message": "Application submitted"},
        )
        result = adapter.submit(page=page, resolver=self._resolver(), context=self._adapter_context())
        self.assertEqual(result.status, "submitted")
        self.assertTrue(page.submitted)
        self.assertIn("resume", page.values)

    def test_linkedin_adapter_blocks_on_unknown_required_question(self) -> None:
        adapter = LinkedInEasyApplyAdapter()
        page = FakePage(
            url="https://www.linkedin.com/jobs/view/1",
            fields=[{"field_name": "favorite_snack", "question_text": "Favorite snack", "field_type": "text", "required": True}],
            confirmation={},
        )
        result = adapter.submit(page=page, resolver=self._resolver(), context=self._adapter_context())
        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.blocker.reason, "missing_required_answer")

    def test_greenhouse_adapter_handles_common_fields_and_confirmation(self) -> None:
        adapter = GreenhouseAdapter()
        page = FakePage(
            url="https://boards.greenhouse.io/acme/jobs/1",
            fields=[
                {"field_name": "identity.full_name", "question_text": "Full name", "field_type": "text", "required": True},
                {"field_name": "identity.email", "question_text": "Email", "field_type": "text", "required": True},
                {"field_name": "resume", "question_text": "Resume", "field_type": "file", "required": True},
            ],
            confirmation={"application_id": "gh-123"},
            easy_apply=False,
        )
        result = adapter.submit(page=page, resolver=self._resolver(), context=self._adapter_context())
        self.assertEqual(result.status, "submitted")
        self.assertEqual(result.confirmation_payload["application_id"], "gh-123")

    def test_greenhouse_adapter_blocks_on_login_wall(self) -> None:
        adapter = GreenhouseAdapter()
        page = FakePage(url="https://boards.greenhouse.io/acme/jobs/1", easy_apply=False)
        page._login_wall = True
        result = adapter.submit(page=page, resolver=self._resolver(), context=self._adapter_context())
        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.blocker.reason, "login_wall")

    def test_service_completes_greenhouse_email_verification_from_gmail_code(self) -> None:
        class FakeEmailCodeClient:
            def is_enabled(self) -> bool:
                return True

            def poll_for_greenhouse_code(self, *, recipient_email: str, requested_at):
                return "AB12CD34"

        class VerificationGreenhouseAdapter(GreenhouseAdapter):
            def submit(self, *, page, resolver, context):
                return SubmitResult(
                    status="blocked",
                    current_url=page.url,
                    blocker=Blocker(
                        reason="email_verification_required",
                        field_name="email_verification",
                        field_type="verification_code",
                        question_text="Enter verification code",
                        details={"digits": 8},
                    ),
                    steps=[],
                    adapter_name=self.adapter_name,
                )

        page = FakePage(url="https://boards.greenhouse.io/acme/jobs/1", easy_apply=False)
        with patch("job_hunter.tailoring.service._render_markdown_pdf", side_effect=self._write_fake_pdf):
            service = ApplicationService(
                settings=self.settings,
                store=self.store,
                tailoring_service=self.tailoring_service,
                browser_manager=FakeBrowserManager(page),
                greenhouse_adapter=VerificationGreenhouseAdapter(),
                email_code_client=FakeEmailCodeClient(),
            )
        run = service.submit_job(job_id=2, profile_name="default", force=True)
        self.assertEqual(run.status, "submitted")
        self.assertEqual(page.verification_code, "AB12CD34")

    def test_service_persists_gmail_verification_error_when_polling_fails(self) -> None:
        class FailingEmailCodeClient:
            def is_enabled(self) -> bool:
                return True

            def poll_for_greenhouse_code(self, *, recipient_email: str, requested_at):
                raise RuntimeError("Gmail API request failed: 403 SERVICE_DISABLED")

        class VerificationGreenhouseAdapter(GreenhouseAdapter):
            def submit(self, *, page, resolver, context):
                return SubmitResult(
                    status="blocked",
                    current_url=page.url,
                    blocker=Blocker(
                        reason="email_verification_required",
                        field_name="email_verification",
                        field_type="verification_code",
                        question_text="Enter verification code",
                        details={"digits": 8},
                    ),
                    steps=[],
                    adapter_name=self.adapter_name,
                )

        page = FakePage(url="https://boards.greenhouse.io/acme/jobs/1", easy_apply=False)
        with patch("job_hunter.tailoring.service._render_markdown_pdf", side_effect=self._write_fake_pdf):
            service = ApplicationService(
                settings=self.settings,
                store=self.store,
                tailoring_service=self.tailoring_service,
                browser_manager=FakeBrowserManager(page),
                greenhouse_adapter=VerificationGreenhouseAdapter(),
                email_code_client=FailingEmailCodeClient(),
            )
        run = service.submit_job(job_id=2, profile_name="default", force=True)
        self.assertEqual(run.status, "blocked")
        shown = service.show_run(run.application_run_id)
        self.assertEqual(
            shown["blocked_payload"]["details"]["gmail_verification_error"],
            "Gmail API request failed: 403 SERVICE_DISABLED",
        )
        self.assertEqual(shown["steps"][-1]["step_key"], "greenhouse:email_verification:gmail")
        self.assertEqual(shown["steps"][-1]["status"], "failed")

    def test_extract_verification_code_returns_recent_8_char_code(self) -> None:
        text = "Hi Gabriel, Copy and paste this code into the security code field on your application: oKGwtMpC"
        self.assertEqual(extract_verification_code(text), "oKGwtMpC")

    def test_resume_retries_blocked_run(self) -> None:
        blocked_page = FakePage(
            url="https://www.linkedin.com/jobs/view/1",
            fields=[{"field_name": "favorite_snack", "question_text": "Favorite snack", "field_type": "text", "required": True}],
            confirmation={},
        )
        service = self._service(blocked_page)
        blocked = service.submit_job(job_id=1, profile_name="default", force=False)
        self.assertEqual(blocked.status, "blocked")

        resumed_page = FakePage(
            url="https://www.linkedin.com/jobs/view/1",
            fields=[{"field_name": "identity.email", "question_text": "Email", "field_type": "text", "required": True}],
            confirmation={"message": "ok"},
        )
        service.browser_manager = FakeBrowserManager(resumed_page)
        resumed = service.resume(application_run_id=blocked.application_run_id)
        self.assertEqual(resumed.status, "submitted")

    def test_list_and_show_render_blocker_and_confirmation(self) -> None:
        page = FakePage(
            url="https://www.linkedin.com/jobs/view/1",
            fields=[{"field_name": "identity.email", "question_text": "Email", "field_type": "text", "required": True}],
            confirmation={"message": "Submitted"},
        )
        service = self._service(page)
        run = service.submit_job(job_id=1, profile_name="default", force=False)
        runs = service.list_runs(status="submitted", limit=5)
        shown = service.show_run(run.application_run_id)
        self.assertEqual(runs[0]["status"], "submitted")
        self.assertEqual(shown["confirmation_payload"]["message"], "Submitted")

    def test_cli_show_json_and_notifications_disabled(self) -> None:
        page = FakePage(
            url="https://www.linkedin.com/jobs/view/1",
            fields=[{"field_name": "identity.email", "question_text": "Email", "field_type": "text", "required": True}],
            confirmation={"message": "Submitted"},
        )
        service = self._service(page)
        run = service.submit_job(job_id=1, profile_name="default", force=False)
        buffer = io.StringIO()
        with patch("job_hunter.apply_jobs.load_settings", return_value=self.settings):
            with patch("job_hunter.apply_jobs.build_application_service", return_value=service):
                with patch("sys.argv", ["apply_jobs.py", "show", "--application-id", str(run.application_run_id), "--format", "json"]):
                    from job_hunter.apply_jobs import main

                    with redirect_stdout(buffer):
                        rc = main()
        self.assertEqual(rc, 0)
        payload = json.loads(buffer.getvalue())
        self.assertEqual(payload["application_id"], run.application_run_id)

    def _service(self, page: FakePage) -> ApplicationService:
        with patch("job_hunter.tailoring.service._render_markdown_pdf", side_effect=self._write_fake_pdf):
            return ApplicationService(
                settings=self.settings,
                store=self.store,
                tailoring_service=self.tailoring_service,
                browser_manager=FakeBrowserManager(page),
            )

    def _resolver(self) -> AnswerResolver:
        profile, answers = load_application_inputs(str(self.profile_root), "default")
        return AnswerResolver(profile=profile, answers=answers)

    def _adapter_context(self):
        with patch("job_hunter.tailoring.service._render_markdown_pdf", side_effect=self._write_fake_pdf):
            artifact = self.tailoring_service.generate_for_job(job_id=1, profile_name="default")
        row = self.store.get_tailoring_artifact(artifact.artifact_id)
        service = self._service(FakePage(url="https://example.com"))
        return service._build_adapter_context(load_application_inputs(str(self.profile_root), "default")[0], str(row["output_dir"]))

    def _write_profile(self, profile_name: str) -> None:
        profile_dir = self.profile_root / profile_name
        profile_dir.mkdir(parents=True, exist_ok=True)
        (profile_dir / "resume.md").write_text("# Resume\n", encoding="utf-8")
        (profile_dir / "cover_letter.md").write_text("# Cover Letter\n", encoding="utf-8")
        (self.profile_root / "default").mkdir(parents=True, exist_ok=True)
        (self.profile_root / "default" / "preferences.md").write_text("Prefer ML roles.\n", encoding="utf-8")
        (profile_dir / "application_profile.json").write_text(
            json.dumps(
                {
                    "identity": {
                        "full_name": "Ada Lovelace",
                        "email": "ada@example.com",
                        "phone": "555-0100",
                        "city": "New York",
                        "region": "NY",
                        "country": "USA",
                        "linkedin_url": "https://linkedin.com/in/ada",
                        "github_url": "https://github.com/ada",
                        "portfolio_url": "https://ada.dev",
                    },
                    "work_authorization": {
                        "us_work_authorized": True,
                        "requires_future_sponsorship": False,
                        "cpt": False,
                        "opt": True,
                    },
                    "education": {
                        "school": "Example University",
                        "degree": "BS",
                        "major": "Computer Science",
                        "graduation_date": "2027-05",
                        "gpa": "3.9",
                    },
                    "employment": {
                        "current_company": "Example Co",
                        "current_title": "Research Intern",
                        "years_experience": "2",
                    },
                    "uploads": {},
                    "preferences": {
                        "salary_min_usd": "50000",
                        "remote_ok": True,
                        "relocation_ok": True,
                    },
                }
            ),
            encoding="utf-8",
        )
        (profile_dir / "application_answers.json").write_text(
            json.dumps(
                {
                    "question_overrides": [
                        {
                            "match_type": "exact",
                            "pattern": "are you willing to complete a background check?",
                            "answer": "Yes",
                        },
                        {
                            "match_type": "contains",
                            "pattern": "hear about us",
                            "answer": "LinkedIn",
                        },
                        {
                            "match_type": "regex",
                            "pattern": "pronouns?",
                            "answer": "They/them",
                        },
                    ],
                    "field_defaults": {},
                }
            ),
            encoding="utf-8",
        )

    def _write_fake_pdf(self, markdown_text: str, output_path: Path, title: str) -> None:
        output_path.write_bytes(b"%PDF-1.4\n%fake\n")


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
        tailoring_output_root=str(Path(output_root) / "tailoring"),
        tailoring_provider="anthropic",
        tailoring_anthropic_model="claude-fake",
        tailoring_batch_default_limit=10,
        apply_provider="anthropic",
        apply_anthropic_model="claude-fake",
        apply_browser_profile_dir=str(Path(output_root) / "browser"),
        apply_headless=True,
        apply_page_timeout_seconds=30,
        apply_batch_default_limit=5,
        apply_output_root=str(Path(output_root) / "applications"),
        apply_gmail_verification_enabled=False,
        apply_gmail_access_token=None,
        apply_gmail_refresh_token=None,
        apply_gmail_client_id=None,
        apply_gmail_client_secret=None,
        apply_gmail_poll_timeout_seconds=30,
        apply_gmail_poll_interval_seconds=1,
        apply_gmail_sender_filter="greenhouse",
        use_linkedin=True,
        linkedin_search_urls=[],
        linkedin_profile_dir=str(Path(output_root) / "linkedin-profile"),
        linkedin_headless=True,
        linkedin_max_results=25,
        linkedin_page_timeout_seconds=30,
        linkedin_fetch_details=True,
    )
