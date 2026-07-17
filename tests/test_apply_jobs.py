from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from job_hunter.apply.adapters.greenhouse import GreenhouseAdapter
from job_hunter.apply.adapters.handshake import HandshakeAdapter
from job_hunter.apply.adapters.handshake_fellow import HandshakeFellowAdapter
from job_hunter.apply.adapters.icims import ICIMSAdapter
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
    def __init__(self, *, url: str, fields=None, confirmation=None, easy_apply=True, greenhouse=True, icims=False) -> None:
        self.url = url
        self._fields = list(fields or [])
        self._confirmation = dict(confirmation or {})
        self._easy_apply = easy_apply
        self._greenhouse = greenhouse
        self._icims = icims
        self._login_wall = False
        self._captcha = False
        self._unsupported_widget = False
        self._ambiguous_submit = False
        self._candidate_profile = False
        self._candidate_questions = False
        self._question_stage_kind = "candidate"
        self.external_url = ""
        self.values: dict[str, str] = {}
        self.submitted = False
        self.verification_code = ""
        self._frame_texts: list[str] = []
        self.clicked_buttons: list[str] = []
        self.professional_experience_controls: list[dict[str, object]] = []
        self.failed_field_names: set[str] = set()
        self.switch_to_questions_after_update = False

    def goto(self, url: str, *, wait_until: str = "domcontentloaded") -> None:
        self.url = url

    def content(self) -> str:
        return "easy apply" if self._easy_apply else "external apply"

    def evaluate(self, script: str, *args):
        return None

    def extract_fields(self):
        return self._fields

    def set_field(self, field, value: str) -> None:
        if str(field.get("field_name") or "") in self.failed_field_names:
            raise RuntimeError("simulated field interaction failure")
        self.values[str(field.get("field_name") or field.get("question_text"))] = value

    def set_input_files(self, selector: str, upload_path: str) -> None:
        self.values[f"file:{selector}"] = upload_path

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

    def detect_icims(self) -> bool:
        return self._icims

    def detect_candidate_profile(self) -> bool:
        return self._candidate_profile

    def detect_candidate_questions(self) -> bool:
        return self._candidate_questions

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

    def frame_texts(self) -> list[str]:
        return list(self._frame_texts)

    def screenshot(self, *, path: str, full_page: bool = True) -> None:
        Path(path).write_bytes(b"fake-screenshot")

    def click_button(self, label: str) -> bool:
        self.clicked_buttons.append(label)
        if label == "Update Profile":
            if self.switch_to_questions_after_update:
                self._candidate_profile = False
                self._candidate_questions = True
                self._question_stage_kind = "candidate"
                self.url = "https://uscareers-medpace.icims.com/jobs/12767/questions?global=1"
                self._frame_texts = ["Candidate Questions\nPlease answer the following questions\nSubmit"]
            else:
                self._confirmation = {"message": "Application submitted"}
        if label == "Submit":
            if self._candidate_questions and self._question_stage_kind == "candidate":
                self._question_stage_kind = "job_specific"
                self.url = "https://uscareers-medpace.icims.com/jobs/12767/questions"
                self._frame_texts = ["Job Specific Questions\nRequired field\nSubmit"]
                self._fields = [
                    {"field_name": "hear_about", "question_text": "How did you hear about this position?", "field_type": "select-one", "required": True, "current_value": "Job Board"},
                    {"field_name": "hear_about_other", "question_text": "If Other, please specify", "field_type": "select-one", "required": False, "current_value": "LinkedIn"},
                    {"field_name": "undergrad_gpa", "question_text": "What is/was your undergraduate GPA on a 4.0 scale?", "field_type": "select-one", "required": True, "current_value": ""},
                ]
            else:
                self._confirmation = {"message": "Application submitted"}
        return True

    def document_file_inputs(self, section_name: str) -> list[str]:
        return [f"{section_name.lower().replace(' ', '_')}_input"]

    def extract_professional_experience_controls(self):
        return list(self.professional_experience_controls)


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
        self.open_calls: list[str] = []
        self.headless_calls: list[bool | None] = []

    def open(self, *, adapter_name: str, headless: bool | None = None):
        self.open_calls.append(adapter_name)
        self.headless_calls.append(headless)
        return FakeSession(self.page)


class FakeContext:
    def __init__(self, pages) -> None:
        self.pages = pages


class FakeButtonLocator:
    def __init__(self, page, *, available: bool) -> None:
        self._page = page
        self._available = available

    @property
    def first(self):
        return self

    def count(self) -> int:
        return 1 if self._available else 0

    def click(self) -> None:
        self._page.trigger_external_apply_button()


class FakeInteractiveLinkedInPage:
    def __init__(self, *, external_target_url: str) -> None:
        self.url = "https://www.linkedin.com/jobs/view/1"
        self._external_target_url = external_target_url
        self.context = FakeContext([self])

    def evaluate(self, script: str):
        return ""

    def locator(self, selector: str):
        page = self

        class _Locator:
            def __init__(self, *, available: bool = False) -> None:
                self._available = available

            def evaluate_all(self, script: str):
                return []

            def filter(self, *, has_text: str):
                return _Locator(available=self._available and has_text in {"Apply", "Apply now"})

            @property
            def first(self):
                return self

            def count(self) -> int:
                return 1 if self._available else 0

            def click(self) -> None:
                page.trigger_external_apply_button()

        return _Locator(available=selector in {"button", "a", '[role="button"]'})

    def wait_for_timeout(self, milliseconds: int) -> None:
        return None

    def trigger_external_apply_button(self) -> None:
        popup = type("PopupPage", (), {"url": self._external_target_url})()
        self.context.pages.append(popup)


class FakeSelectLocator:
    def __init__(self, *, options: list[dict[str, object]], fail_select_option: bool = False) -> None:
        self.options = [dict(option) for option in options]
        self.fail_select_option = fail_select_option
        self.selected_value = ""
        self.events_dispatched = 0

    def select_option(self, *, label: str | None = None, value: str | None = None, index: int | None = None) -> None:
        if self.fail_select_option:
            raise RuntimeError("select_option failed")
        if index is not None:
            option = self.options[index]
            self.selected_value = str(option.get("value") or "")
            return
        target = value if value is not None else label
        for option in self.options:
            if option.get("value") == target or option.get("label") == target:
                self.selected_value = str(option.get("value") or "")
                return
        raise RuntimeError("option not found")

    def evaluate(self, script: str, arg=None):
        if "Array.from(el.options).map" in script:
            if "index" in script:
                return [dict(option) for option in self.options]
            return [{"value": str(option.get("value") or ""), "label": str(option.get("label") or "")} for option in self.options]
        if "dispatchEvent" in script and "optionIndex" not in script:
            self.events_dispatched += 1
            return True
        if "optionIndex" in script:
            option = self.options[int(arg)]
            self.selected_value = str(option.get("value") or "")
            self.events_dispatched += 1
            return True
        raise RuntimeError("unexpected evaluate script")


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

    def test_answer_resolver_classifies_supported_intents(self) -> None:
        resolver = self._resolver()
        self.assertEqual(
            resolver.classify_intent(question_text="Please provide your consent for the storage, processing, and use of your data for employment.*"),
            "consent_required",
        )
        self.assertEqual(
            resolver.classify_intent(question_text="What is your current location?"),
            "current_location",
        )
        self.assertEqual(
            resolver.classify_intent(question_text="Are you currently authorized to work in the United States?"),
            "work_auth_us",
        )

    def test_answer_resolver_exposes_capability_matrix_rows(self) -> None:
        resolver = self._resolver()
        capability = resolver.capability_for_field(
            portal="linkedin",
            question_text="Please provide your consent for the storage, processing, and use of your data for employment.*",
            field_type="checkbox-group",
        )
        self.assertIsNotNone(capability)
        self.assertEqual(capability.submit_policy, "safe_autofill_if_single_option")

        capability = resolver.capability_for_field(
            portal="greenhouse",
            question_text="Are you currently authorized to work in the United States?",
            field_type="radio-group",
        )
        self.assertIsNotNone(capability)
        self.assertEqual(capability.resolver_mode, "structured_boolean_yes_no")

    def test_answer_resolver_resolves_via_capability_matrix(self) -> None:
        resolver = self._resolver()
        resolution = resolver.resolve_for_portal(
            portal="linkedin",
            question_text="Please provide your consent for the storage, processing, and use of your data for employment.*",
            field_type="checkbox-group",
        )
        self.assertEqual(resolution.answer, "Yes")
        self.assertIn("capability:linkedin:consent_required", resolution.source)

        resolution = resolver.resolve_for_portal(
            portal="greenhouse",
            question_text="Are you currently authorized to work in the United States?",
            field_type="radio-group",
        )
        self.assertEqual(resolution.answer, "Yes")
        self.assertIn("capability:greenhouse:work_auth_us", resolution.source)

    def test_answer_resolver_computes_education_fields(self) -> None:
        profile, answers = load_application_inputs(str(self.profile_root), "default")
        resolver = AnswerResolver(profile=profile, answers=answers)
        self.assertEqual(
            resolver.resolve(question_text="Degree*", field_name="degree--0").answer,
            "Bachelor's Degree",
        )
        self.assertEqual(
            resolver.resolve(question_text="Discipline*", field_name="discipline--0").answer,
            "Computer Science",
        )
        profile, answers = load_application_inputs(str(self.profile_root), "default")
        profile.education.degree = "M.S."
        resolver = AnswerResolver(profile=profile, answers=answers)
        self.assertEqual(
            resolver.resolve(question_text="Degree*", field_name="degree--0").answer,
            "Master's Degree",
        )
        self.assertEqual(
            resolver.resolve(question_text="Start date year*", field_name="start-year--0").answer,
            "2025",
        )
        self.assertEqual(
            resolver.resolve(question_text="End date year*", field_name="end-year--0").answer,
            "2027",
        )
        self.assertEqual(
            resolver.resolve(question_text="End date month*", field_name="end-month--0").answer,
            "May",
        )
        self.assertEqual(
            resolver.resolve(question_text="What is your current location?").answer,
            "New York, NY",
        )

    def test_answer_resolver_does_not_map_course_credit_prompt_to_school_name(self) -> None:
        profile, answers = load_application_inputs(str(self.profile_root), "default")
        answers.question_overrides.append(
            type(answers.question_overrides[0])(
                match_type="contains",
                pattern="are you seeking course credit for this internship",
                answer="No, I am not seeking course credit for this internship.",
            )
        )
        resolver = AnswerResolver(profile=profile, answers=answers)

        resolution = resolver.resolve(
            question_text=(
                "Are you seeking course credit for this internship, and if so, please list any "
                "requirements your school has in terms of hours, supervision, projects, etc.*"
            ),
            field_name="question_17156310008",
            field_type="text",
        )

        self.assertEqual(resolution.answer, "No, I am not seeking course credit for this internship.")
        self.assertEqual(resolution.source, "override:contains")

    def test_answer_resolver_computes_professional_experience_fields(self) -> None:
        resolver = self._resolver()
        self.assertEqual(
            resolver.resolve(question_text="professional experience Employer", field_name="employer", field_type="text").answer,
            "Example Co",
        )
        self.assertEqual(
            resolver.resolve(question_text="professional experience Title", field_name="title", field_type="text").answer,
            "Research Intern",
        )
        self.assertEqual(
            resolver.resolve(
                question_text="professional experience Start Date (Month / Day / Year)",
                field_name="exp-start-month",
                field_type="select-month",
            ).answer,
            "January",
        )
        self.assertEqual(
            resolver.resolve(
                question_text="professional experience Start Date (Month / Day / Year)",
                field_name="-1_PersonProfileFields.rcf3214_Year",
                field_type="text",
            ).answer,
            "2024",
        )
        self.assertEqual(
            resolver.resolve(
                question_text="professional experience Country*",
                field_name="country",
                field_type="select-one",
            ).answer,
            "United States",
        )
        self.assertEqual(
            resolver.resolve(
                question_text="professional experience State/Province*",
                field_name="state",
                field_type="select-one",
            ).answer,
            "New York",
        )
        self.assertEqual(
            resolver.resolve(question_text="Reason for Leaving", field_name="reason", field_type="text").answer,
            "Current role",
        )

    def test_answer_resolver_computes_icims_candidate_question_fields(self) -> None:
        resolver = self._resolver()
        self.assertEqual(
            resolver.resolve(
                question_text="Are you legally authorized to work in the United States?",
                field_name="work_auth",
                field_type="select-one",
            ).answer,
            "Yes",
        )
        self.assertEqual(
            resolver.resolve(
                question_text="Will you now, or in the future, require Medpace Inc. to commence ('sponsor') an immigration application in order to employ you?",
                field_name="sponsorship",
                field_type="select-one",
            ).answer,
            "No, I hold a current US Work Visa",
        )
        self.assertEqual(
            resolver.resolve(
                question_text="Please indicate your current type of US Work Visa.",
                field_name="visa_type",
                field_type="select-one",
            ).answer,
            "F-1 OPT",
        )
        self.assertEqual(
            resolver.resolve(
                question_text="Please list the expiration date of your current US Work Visa (DD/MM/YYYY). Mark N/A if not applicable.",
                field_name="visa_expiration",
                field_type="text",
            ).answer,
            "01/05/2027",
        )
        self.assertEqual(
            resolver.resolve(
                question_text="What is/was your undergraduate GPA on a 4.0 scale?",
                field_name="undergrad_gpa",
                field_type="select-one",
            ).answer,
            "3.9",
        )

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

    def test_linkedin_adapter_handles_required_consent_checkbox_group(self) -> None:
        adapter = LinkedInEasyApplyAdapter()
        page = FakePage(
            url="https://www.linkedin.com/jobs/view/1",
            fields=[
                {
                    "field_name": "consent",
                    "question_text": "Please provide your consent for the storage, processing, and use of your data for employment.*",
                    "field_type": "checkbox-group",
                    "required": True,
                    "current_value": "",
                    "options": [
                        {
                            "selector": "#consent-retain",
                            "text": "Traackr has my consent to retain my data for the purpose of considering me for employment.",
                        }
                    ],
                }
            ],
            confirmation={"message": "Application submitted"},
        )

        result = adapter.submit(page=page, resolver=self._resolver(), context=self._adapter_context())

        self.assertEqual(result.status, "submitted")
        self.assertEqual(page.values["consent"], "Yes")

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

    def test_linkedin_adapter_extracts_wrapped_external_apply_url(self) -> None:
        adapter = LinkedInEasyApplyAdapter()
        page = FakePage(url="https://www.linkedin.com/jobs/view/1", easy_apply=False)
        page.external_url = (
            "https://www.linkedin.com/safety/go/?url="
            "https%3A%2F%2Fjob-boards%2Egreenhouse%2Eio%2Fpodium81%2Fjobs%2F7939921%3Fgh_src%3D8b0de3d81"
            "&urlhash=abc&mt=xyz&isSdui=true"
        )
        self.assertEqual(
            adapter.extract_external_apply_url(page),
            "https://job-boards.greenhouse.io/podium81/jobs/7939921?gh_src=8b0de3d81",
        )

    def test_linkedin_adapter_extracts_external_apply_url_from_apply_button_popup(self) -> None:
        adapter = LinkedInEasyApplyAdapter()
        page = FakeInteractiveLinkedInPage(
            external_target_url="https://job-boards.greenhouse.io/neuralink/jobs/12345"
        )

        result = adapter.extract_external_apply_url(page)

        self.assertEqual(result, "https://job-boards.greenhouse.io/neuralink/jobs/12345")

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

    def test_handshake_adapter_uploads_documents_and_confirms(self) -> None:
        adapter = HandshakeAdapter()
        page = FakePage(
            url="https://app.joinhandshake.com/jobs/111",
            confirmation={"application_id": "hs-123"},
            easy_apply=False,
            greenhouse=False,
            icims=False,
        )

        result = adapter.submit(page=page, resolver=self._resolver(), context=self._adapter_context())

        self.assertEqual(result.status, "submitted")
        self.assertEqual(result.confirmation_payload["application_id"], "hs-123")
        self.assertEqual(page.values["file:attach_your_resume_input"], self._adapter_context().resume_pdf_path)
        self.assertEqual(page.values["file:attach_your_cover_letter_input"], self._adapter_context().cover_letter_pdf_path)
        self.assertTrue(page.submitted)

    def test_handshake_adapter_recognizes_native_post_submit_job_page(self) -> None:
        adapter = HandshakeAdapter()

        class HandshakeSubmittedPage(FakePage):
            def content(self) -> str:
                return (
                    "Applied on July 10, 2026 "
                    "Withdraw application "
                    "Application submitted!"
                )

        page = HandshakeSubmittedPage(
            url="https://app.joinhandshake.com/jobs/111",
            easy_apply=False,
            greenhouse=False,
            icims=False,
        )

        result = adapter.submit(page=page, resolver=self._resolver(), context=self._adapter_context())

        self.assertEqual(result.status, "submitted")
        self.assertEqual(result.confirmation_payload["source"], "handshake")

    def test_greenhouse_adapter_handles_required_choice_groups(self) -> None:
        adapter = GreenhouseAdapter()
        page = FakePage(
            url="https://boards.greenhouse.io/acme/jobs/1",
            fields=[
                {
                    "field_name": "work_auth",
                    "question_text": "Are you currently authorized to work in the United States?",
                    "field_type": "radio-group",
                    "required": True,
                    "current_value": "",
                    "options": [{"selector": "#work-auth-yes", "label": "Yes"}, {"selector": "#work-auth-no", "label": "No"}],
                },
                {
                    "field_name": "onsite",
                    "question_text": "I understand that this position requires me to work on-site.",
                    "field_type": "radio-group",
                    "required": True,
                    "current_value": "",
                    "options": [{"selector": "#onsite-yes", "label": "Yes"}, {"selector": "#onsite-no", "label": "No"}],
                },
                {
                    "field_name": "hear_about_us",
                    "question_text": "How did you hear about us?",
                    "field_type": "checkbox-group",
                    "required": True,
                    "current_value": "",
                    "options": [{"selector": "#hear-linkedin", "label": "LinkedIn"}, {"selector": "#hear-friend", "label": "Friend"}],
                },
            ],
            confirmation={"application_id": "gh-choices"},
            easy_apply=False,
        )

        result = adapter.submit(page=page, resolver=self._resolver(), context=self._adapter_context())

        self.assertEqual(result.status, "submitted")
        self.assertEqual(page.values["work_auth"], "Yes")
        self.assertEqual(page.values["onsite"], "Yes")
        self.assertEqual(page.values["hear_about_us"], "LinkedIn")

    def test_greenhouse_adapter_detects_grnh_shortlink_target(self) -> None:
        adapter = GreenhouseAdapter()
        self.assertTrue(adapter.is_greenhouse_target("https://grnh.se/73689a903us"))

    def test_greenhouse_adapter_blocks_on_login_wall(self) -> None:
        adapter = GreenhouseAdapter()
        page = FakePage(url="https://boards.greenhouse.io/acme/jobs/1", easy_apply=False)
        page._login_wall = True
        result = adapter.submit(page=page, resolver=self._resolver(), context=self._adapter_context())
        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.blocker.reason, "login_wall")

    def test_icims_adapter_detects_target_and_blocks_on_captcha(self) -> None:
        adapter = ICIMSAdapter()
        page = FakePage(url="https://uscareers-medpace.icims.com/jobs/12767/login", easy_apply=False, greenhouse=False, icims=True)
        page._frame_texts = [
            "Welcome page\nEnter Your Information\nSoftware Powered by iCIMS",
            "Protected by hCaptcha\nVerify",
        ]
        result = adapter.submit(page=page, resolver=self._resolver(), context=self._adapter_context())
        self.assertTrue(adapter.is_icims_target(page.url, page=page))
        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.blocker.reason, "captcha")
        self.assertEqual(result.blocker.details["provider"], "hcaptcha")
        self.assertIn("frame_texts", result.blocker.details)

    def test_icims_candidate_profile_uses_artifact_uploads(self) -> None:
        adapter = ICIMSAdapter()
        page = FakePage(url="https://uscareers-medpace.icims.com/jobs/12767/candidate", easy_apply=False, greenhouse=False, icims=True)
        page._candidate_profile = True
        page._fields = [
            {
                "field_name": "first_name",
                "question_text": "First Name",
                "field_type": "text",
                "required": True,
                "current_value": "",
            },
            {
                "field_name": "email",
                "question_text": "Email",
                "field_type": "text",
                "required": True,
                "current_value": "",
            },
        ]
        context = self._adapter_context()

        result = adapter.submit(page=page, resolver=self._resolver(), context=context)

        self.assertEqual(result.status, "submitted")
        self.assertEqual(page.values["file:resume_input"], context.resume_pdf_path)
        self.assertEqual(page.values["file:cover_letter_input"], context.cover_letter_pdf_path)
        self.assertEqual(page.values["first_name"], "Ada")
        self.assertEqual(page.values["email"], "ada@example.com")
        self.assertIn("Update Profile", page.clicked_buttons)

    def test_icims_professional_experience_name_based_controls_fill(self) -> None:
        adapter = ICIMSAdapter()
        page = FakePage(url="https://uscareers-medpace.icims.com/jobs/12767/candidate", easy_apply=False, greenhouse=False, icims=True)
        page._candidate_profile = True
        page._fields = []
        page.professional_experience_controls = [
            {"selector": "#country", "field_name": "-1_PersonProfileFields.rcf3218", "field_type": "select-one", "question_text": "Country", "current_value": ""},
            {"selector": "#state", "field_name": "-1_PersonProfileFields.rcf3217", "field_type": "select-one", "question_text": "State/Province", "current_value": ""},
            {"selector": "#start-month", "field_name": "-1_PersonProfileFields.rcf3214_Month", "field_type": "select-month", "question_text": "Start Date (Month / Day / Year)", "current_value": ""},
            {"selector": "#start-day", "field_name": "-1_PersonProfileFields.rcf3214_Date", "field_type": "select-day", "question_text": "Start Date (Month / Day / Year)", "current_value": ""},
            {"selector": "#start-year", "field_name": "-1_PersonProfileFields.rcf3214_Year", "field_type": "text", "question_text": "Start Date (Month / Day / Year)", "current_value": ""},
            {"selector": "#contact", "field_name": "-1_PersonProfileFields.rcf3269", "field_type": "select-one", "question_text": "May We Contact", "current_value": ""},
        ]
        context = self._adapter_context()

        result = adapter.submit(page=page, resolver=self._resolver(), context=context)

        self.assertEqual(result.status, "submitted")
        self.assertEqual(page.values["-1_PersonProfileFields.rcf3218"], "United States")
        self.assertEqual(page.values["-1_PersonProfileFields.rcf3217"], "New York")
        self.assertEqual(page.values["-1_PersonProfileFields.rcf3214_Month"], "January")
        self.assertEqual(page.values["-1_PersonProfileFields.rcf3214_Date"], "1")
        self.assertEqual(page.values["-1_PersonProfileFields.rcf3214_Year"], "2024")
        self.assertEqual(page.values["-1_PersonProfileFields.rcf3269"], "No")

    def test_icims_professional_experience_dropdowns_create_manual_checkpoint(self) -> None:
        adapter = ICIMSAdapter()
        page = FakePage(url="https://uscareers-medpace.icims.com/jobs/12767/candidate", easy_apply=False, greenhouse=False, icims=True)
        page._candidate_profile = True
        page._fields = []
        page.failed_field_names = {"-1_PersonProfileFields.rcf3218"}
        page.professional_experience_controls = [
            {"selector": "#country", "field_name": "-1_PersonProfileFields.rcf3218", "field_type": "select-one", "question_text": "Country", "current_value": ""},
        ]

        result = adapter.submit(page=page, resolver=self._resolver(), context=self._adapter_context())

        self.assertEqual(result.status, "blocked")
        self.assertIsNotNone(result.blocker)
        self.assertEqual(result.blocker.reason, "manual_checkpoint_required")
        self.assertEqual(result.blocker.details["checkpoint"], "professional_experience_dropdowns")

    def test_icims_set_select_falls_back_to_dom_selection(self) -> None:
        adapter = ICIMSAdapter()
        locator = FakeSelectLocator(
            options=[
                {"index": 0, "value": "", "label": "Please select"},
                {"index": 1, "value": "US", "label": "United States"},
            ],
            fail_select_option=True,
        )

        result = adapter._set_select(locator, "United States")

        self.assertTrue(result)
        self.assertEqual(locator.selected_value, "US")
        self.assertGreaterEqual(locator.events_dispatched, 1)

    def test_icims_candidate_profile_is_not_misclassified_as_login_wall(self) -> None:
        adapter = ICIMSAdapter()
        page = FakePage(url="https://uscareers-medpace.icims.com/jobs/12767/candidate", easy_apply=False, greenhouse=False, icims=True)
        page._candidate_profile = True
        page._frame_texts = ["Candidate Profile\nUpdate Profile\nResume"]
        page._fields = [
            {
                "field_name": "password",
                "question_text": "Password",
                "field_type": "password",
                "required": True,
                "current_value": "",
            }
        ]

        result = adapter.submit(page=page, resolver=self._resolver(), context=self._adapter_context())

        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.blocker.reason, "account_setup_required")

    def test_icims_candidate_questions_submit_and_capture_confirmation(self) -> None:
        adapter = ICIMSAdapter()
        page = FakePage(url="https://uscareers-medpace.icims.com/jobs/12767/questions?global=1", easy_apply=False, greenhouse=False, icims=True)
        page._candidate_questions = True
        page._frame_texts = ["Candidate Questions\nPlease answer the following questions\nSubmit"]
        page._fields = [
            {"field_name": "work_auth", "question_text": "Are you legally authorized to work in the United States?", "field_type": "select-one", "required": True, "current_value": ""},
            {"field_name": "over_18", "question_text": "Are you over 18?", "field_type": "select-one", "required": True, "current_value": ""},
            {"field_name": "employed_before", "question_text": "Have you previously been employed by Medpace?", "field_type": "select-one", "required": True, "current_value": ""},
            {"field_name": "interviewed_before", "question_text": "Have you ever interviewed with Medpace?", "field_type": "select-one", "required": True, "current_value": ""},
            {"field_name": "relatives", "question_text": "Do you have any relatives employed by Medpace?", "field_type": "select-one", "required": True, "current_value": ""},
            {"field_name": "sponsorship", "question_text": "Will you now, or in the future, require Medpace Inc. to commence ('sponsor') an immigration application in order to employ you?", "field_type": "select-one", "required": True, "current_value": ""},
            {"field_name": "visa_type", "question_text": "Please indicate your current type of US Work Visa.", "field_type": "select-one", "required": True, "current_value": ""},
            {"field_name": "visa_expiration", "question_text": "Please list the expiration date of your current US Work Visa (DD/MM/YYYY). Mark N/A if not applicable.", "field_type": "text", "required": True, "current_value": ""},
        ]

        result = adapter.submit(page=page, resolver=self._resolver(), context=self._adapter_context())

        self.assertEqual(result.status, "submitted")
        self.assertEqual(page.values["work_auth"], "Yes")
        self.assertEqual(page.values["over_18"], "Yes")
        self.assertEqual(page.values["sponsorship"], "No, I hold a current US Work Visa")
        self.assertEqual(page.values["visa_type"], "F-1 OPT")
        self.assertEqual(page.values["visa_expiration"], "01/05/2027")
        self.assertIn("Submit", page.clicked_buttons)

    def test_icims_candidate_profile_hands_off_to_candidate_questions(self) -> None:
        adapter = ICIMSAdapter()
        page = FakePage(url="https://uscareers-medpace.icims.com/jobs/12767/candidate", easy_apply=False, greenhouse=False, icims=True)
        page._candidate_profile = True
        page.switch_to_questions_after_update = True
        page._fields = [
            {"field_name": "work_auth", "question_text": "Are you legally authorized to work in the United States?", "field_type": "select-one", "required": True, "current_value": ""},
            {"field_name": "over_18", "question_text": "Are you over 18?", "field_type": "select-one", "required": True, "current_value": ""},
        ]

        result = adapter.submit(page=page, resolver=self._resolver(), context=self._adapter_context())

        self.assertEqual(result.status, "submitted")
        self.assertIn("Update Profile", page.clicked_buttons)
        self.assertIn("Submit", page.clicked_buttons)

    def test_icims_candidate_questions_hands_off_to_job_specific_questions(self) -> None:
        adapter = ICIMSAdapter()
        page = FakePage(url="https://uscareers-medpace.icims.com/jobs/12767/questions?global=1", easy_apply=False, greenhouse=False, icims=True)
        page._candidate_questions = True
        page._question_stage_kind = "candidate"
        page._frame_texts = ["Candidate Questions\nPlease answer the following questions\nSubmit"]
        page._fields = [
            {"field_name": "work_auth", "question_text": "Are you legally authorized to work in the United States?", "field_type": "select-one", "required": True, "current_value": ""},
            {"field_name": "over_18", "question_text": "Are you over 18?", "field_type": "select-one", "required": True, "current_value": ""},
        ]

        result = adapter.submit(page=page, resolver=self._resolver(), context=self._adapter_context())

        self.assertEqual(result.status, "submitted")
        self.assertEqual(page.values["undergrad_gpa"], "3.9")
        self.assertGreaterEqual(page.clicked_buttons.count("Submit"), 2)

    def test_icims_extract_confirmation_matches_medpace_submission_banner(self) -> None:
        adapter = ICIMSAdapter()
        class BannerPage:
            url = "https://uscareers-medpace.icims.com/jobs/12767/job?mode=submit_apply"

            def frame_texts(self):
                return [
                    "Your application was submitted successfully. Thank you for applying.\n"
                    "You are currently submitted to this job."
                ]

        page = BannerPage()

        confirmation = adapter._extract_confirmation(page)

        self.assertIn("submitted successfully", confirmation["message"].lower())
        self.assertEqual(confirmation["source"], "icims")

    def test_icims_submit_short_circuits_on_confirmation_page(self) -> None:
        adapter = ICIMSAdapter()

        class BannerPage:
            url = "https://uscareers-medpace.icims.com/jobs/12767/job?mode=submit_apply"

            def frame_texts(self):
                return [
                    "Your application was submitted successfully. Thank you for applying.\n"
                    "You are currently submitted to this job."
                ]

        result = adapter.submit(page=BannerPage(), resolver=self._resolver(), context=self._adapter_context())

        self.assertEqual(result.status, "submitted")
        self.assertIn("application submitted", result.confirmation_payload["message"].lower())

    def test_icims_extract_confirmation_uses_submit_apply_url_heuristic(self) -> None:
        adapter = ICIMSAdapter()

        class UrlOnlyPage:
            url = "https://uscareers-medpace.icims.com/jobs/12767/job?mode=submit_apply"

        confirmation = adapter._extract_confirmation(UrlOnlyPage())

        self.assertEqual(confirmation["source"], "icims")
        self.assertEqual(confirmation["inference"], "url-based")

    def test_service_routes_linkedin_external_apply_to_icims_adapter(self) -> None:
        page = FakePage(
            url="https://www.linkedin.com/jobs/view/1",
            easy_apply=False,
            greenhouse=False,
            icims=True,
        )
        page.external_url = "https://uscareers-medpace.icims.com/jobs/12767/login?iis=Job%20Board&iisn=LinkedIn"
        page._frame_texts = [
            "Welcome page\nEnter Your Information\nSoftware Powered by iCIMS",
            "Protected by hCaptcha\nVerify",
        ]
        service = self._service(page)
        run = service.submit_job(job_id=1, profile_name="default", force=True)
        shown = service.show_run(run.application_run_id)
        self.assertEqual(run.status, "blocked")
        self.assertEqual(shown["adapter_name"], "icims")
        self.assertEqual(shown["blocked_reason"], "captcha")
        self.assertIn("frame_texts", shown["blocked_payload"]["details"])

    def test_service_routes_linkedin_external_apply_to_workday_adapter(self) -> None:
        page = FakePage(
            url="https://www.linkedin.com/jobs/view/1",
            easy_apply=False,
            greenhouse=False,
            icims=False,
        )
        page.external_url = (
            "https://iherb.wd5.myworkdayjobs.com/Careers/job/Home-Office-CA/"
            "Software-Development-Intern_R107025-1?source=LinkedIn"
        )
        service = self._service(page)
        run = service.submit_job(job_id=1, profile_name="default", force=True)
        shown = service.show_run(run.application_run_id)
        self.assertEqual(run.status, "blocked")
        self.assertEqual(shown["adapter_name"], "workday")
        self.assertTrue(shown["blocked_reason"] in {"apply_button_missing", "unsupported_widget"})

    def test_service_reuses_previous_external_target_when_linkedin_guest_page_hides_it(self) -> None:
        first_page = FakePage(
            url="https://www.linkedin.com/jobs/view/1",
            easy_apply=False,
            greenhouse=False,
            icims=True,
        )
        first_page.external_url = "https://uscareers-medpace.icims.com/jobs/12767/login?iis=Job%20Board&iisn=LinkedIn"
        first_page._frame_texts = [
            "Welcome page\nEnter Your Information\nSoftware Powered by iCIMS",
            "Protected by hCaptcha\nVerify",
        ]
        service = self._service(first_page)
        first_run = service.submit_job(job_id=1, profile_name="default", force=True)
        self.assertEqual(first_run.status, "blocked")

        second_page = FakePage(
            url="https://www.linkedin.com/jobs/view/1",
            easy_apply=False,
            greenhouse=False,
            icims=True,
        )
        second_page._frame_texts = [
            "Welcome page\nEnter Your Information\nSoftware Powered by iCIMS",
            "Protected by hCaptcha\nVerify",
        ]
        service.browser_manager = FakeBrowserManager(second_page)
        second_run = service.submit_job(job_id=1, profile_name="default", force=True)
        shown = service.show_run(second_run.application_run_id)
        self.assertEqual(second_run.status, "blocked")
        self.assertEqual(shown["adapter_name"], "icims")
        self.assertEqual(
            shown["target_url"],
            "https://uscareers-medpace.icims.com/jobs/12767/login?iis=Job%20Board&iisn=LinkedIn",
        )

    def test_service_uses_stored_external_apply_url_before_reopening_linkedin(self) -> None:
        external_url = "https://uscareers-medpace.icims.com/jobs/12767/login?iis=Job%20Board&iisn=LinkedIn"
        self.store._conn.execute(
            "UPDATE jobs SET source_metadata = ? WHERE id = 1",
            (json.dumps({"external_apply_url": external_url}),),
        )
        self.store._conn.commit()

        page = FakePage(
            url="https://www.linkedin.com/jobs/view/1",
            easy_apply=False,
            greenhouse=False,
            icims=True,
        )
        page._frame_texts = [
            "Welcome page\nEnter Your Information\nSoftware Powered by iCIMS",
            "Protected by hCaptcha\nVerify",
        ]
        service = self._service(page)
        run = service.submit_job(job_id=1, profile_name="default", force=True)
        shown = service.show_run(run.application_run_id)
        self.assertEqual(run.status, "blocked")
        self.assertEqual(shown["adapter_name"], "icims")
        self.assertEqual(shown["target_url"], external_url)
        self.assertEqual(page.url, external_url)

    def test_service_routes_handshake_external_apply_to_greenhouse_adapter(self) -> None:
        self.store._conn.execute(
            """
            INSERT INTO jobs (
                dedupe_key, source, external_id, url, title, company, location, is_internship, posted_at,
                description, compensation_type, work_auth_signals, sponsorship_signals, skills, ingested_at,
                relevance_score, eligibility_confidence, eligibility_status, relevance_hits, profile_match_score,
                profile_match_label, source_metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "handshake-gh",
                "handshake",
                "hs-1",
                "https://app.joinhandshake.com/jobs/111",
                "Data Intern",
                "Handshake Co",
                "Remote",
                1,
                "2026-07-10",
                "desc",
                "unknown",
                "[]",
                "[]",
                "[]",
                "2026-07-10T00:00:00+00:00",
                0.0,
                0.0,
                "eligible",
                "[]",
                0.9,
                "pass",
                json.dumps({"external_apply_url": "https://job-boards.greenhouse.io/acme/jobs/1"}),
            ),
        )
        self.store._conn.commit()
        handshake_job_id = int(self.store._conn.execute("SELECT id FROM jobs WHERE dedupe_key = 'handshake-gh'").fetchone()[0])

        page = FakePage(
            url="https://app.joinhandshake.com/jobs/111",
            easy_apply=False,
            greenhouse=True,
            confirmation={"message": "Application submitted"},
        )
        service = self._service(page)

        run = service.submit_job(job_id=handshake_job_id, profile_name="default", force=True)
        shown = service.show_run(run.application_run_id)

        self.assertEqual(run.status, "submitted")
        self.assertEqual(shown["adapter_name"], "greenhouse")

    def test_service_routes_native_handshake_to_adapter(self) -> None:
        self.store.insert_job(
            JobRecord(
                source="handshake",
                external_id="hs-native",
                url="https://app.joinhandshake.com/jobs/555",
                title="Data Fellowship",
                company="Handshake Co",
                location="Remote",
                is_internship=True,
                posted_at="2026-06-30T00:00:00+00:00",
                description="Apply in Handshake.",
                ingested_at="2026-06-30T01:00:00+00:00",
                profile_match_score=0.9,
                profile_match_label="pass",
                job_text_version="job_text_v1",
                job_text_snapshot="TITLE: Data Fellowship",
            ),
            "handshake-native",
        )
        handshake_job_id = int(self.store._conn.execute("SELECT id FROM jobs WHERE dedupe_key = 'handshake-native'").fetchone()[0])

        page = FakePage(
            url="https://app.joinhandshake.com/jobs/555",
            confirmation={"application_id": "hs-native-123"},
            easy_apply=False,
            greenhouse=False,
            icims=False,
        )
        service = self._service(page)

        run = service.submit_job(job_id=handshake_job_id, profile_name="default", force=True)
        shown = service.show_run(run.application_run_id)

        self.assertEqual(run.status, "submitted")
        self.assertEqual(shown["adapter_name"], "handshake")

    def test_service_ignores_previous_handshake_internal_target_when_resolving_native_flow(self) -> None:
        self.store.insert_job(
            JobRecord(
                source="handshake",
                external_id="hs-native-stale",
                url="https://app.joinhandshake.com/jobs/556",
                title="Data Fellowship",
                company="Handshake Co",
                location="Remote",
                is_internship=True,
                posted_at="2026-06-30T00:00:00+00:00",
                description="Apply in Handshake.",
                ingested_at="2026-06-30T01:00:00+00:00",
                profile_match_score=0.9,
                profile_match_label="pass",
                job_text_version="job_text_v1",
                job_text_snapshot="TITLE: Data Fellowship",
            ),
            "handshake-native-stale-target",
        )
        handshake_job_id = int(self.store._conn.execute("SELECT id FROM jobs WHERE dedupe_key = 'handshake-native-stale-target'").fetchone()[0])
        self.store.create_application_run(
            job_id=handshake_job_id,
            profile_name="default",
            tailoring_artifact_id=1,
            adapter_name="handshake_fellow",
            source="handshake",
            target_url="https://ai.joinhandshake.com/fellow/dashboard",
            current_url="https://ai.joinhandshake.com/fellow/dashboard",
            status="blocked",
            output_dir=str(self.output_root / "applications" / "default" / "pending"),
            blocked_reason="handshake_fellow_dashboard_only",
        )

        page = FakePage(
            url="https://app.joinhandshake.com/jobs/556",
            confirmation={"application_id": "hs-native-556"},
            easy_apply=False,
            greenhouse=False,
            icims=False,
        )
        service = self._service(page)

        run = service.submit_job(job_id=handshake_job_id, profile_name="default", force=True)
        shown = service.show_run(run.application_run_id)

        self.assertEqual(run.status, "submitted")
        self.assertEqual(shown["adapter_name"], "handshake")
        self.assertEqual(shown["target_url"], "https://app.joinhandshake.com/jobs/556")

    def test_service_keeps_native_handshake_flow_even_when_page_exposes_fellow_link(self) -> None:
        self.store.insert_job(
            JobRecord(
                source="handshake",
                external_id="hs-native-fellow-link",
                url="https://app.joinhandshake.com/jobs/557",
                title="Data Fellowship",
                company="Handshake Co",
                location="Remote",
                is_internship=True,
                posted_at="2026-06-30T00:00:00+00:00",
                description="Apply in Handshake.",
                ingested_at="2026-06-30T01:00:00+00:00",
                profile_match_score=0.9,
                profile_match_label="pass",
                job_text_version="job_text_v1",
                job_text_snapshot="TITLE: Data Fellowship",
            ),
            "handshake-native-fellow-link",
        )
        handshake_job_id = int(self.store._conn.execute("SELECT id FROM jobs WHERE dedupe_key = 'handshake-native-fellow-link'").fetchone()[0])

        class NativeHandshakePageWithFellowLink(FakePage):
            def evaluate(self, script: str, *args):
                if "document.querySelectorAll('a[href]')" in script:
                    return "https://ai.joinhandshake.com/fellow/dashboard"
                return super().evaluate(script, *args)

        page = NativeHandshakePageWithFellowLink(
            url="https://app.joinhandshake.com/jobs/557",
            confirmation={"application_id": "hs-native-557"},
            easy_apply=False,
            greenhouse=False,
            icims=False,
        )
        service = self._service(page)

        run = service.submit_job(job_id=handshake_job_id, profile_name="default", force=True)
        shown = service.show_run(run.application_run_id)

        self.assertEqual(run.status, "submitted")
        self.assertEqual(shown["adapter_name"], "handshake")
        self.assertEqual(shown["target_url"], "https://app.joinhandshake.com/jobs/557")

    def test_service_persists_blocked_run_for_unsupported_handshake_target(self) -> None:
        self.store._conn.execute(
            """
            INSERT INTO jobs (
                dedupe_key, source, external_id, url, title, company, location, is_internship, posted_at,
                description, compensation_type, work_auth_signals, sponsorship_signals, skills, ingested_at,
                relevance_score, eligibility_confidence, eligibility_status, relevance_hits, profile_match_score,
                profile_match_label, source_metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "handshake-unsupported",
                "handshake",
                "hs-2",
                "https://app.joinhandshake.com/jobs/222",
                "Data Intern",
                "Handshake Co",
                "Remote",
                1,
                "2026-07-10",
                "desc",
                "unknown",
                "[]",
                "[]",
                "[]",
                "2026-07-10T00:00:00+00:00",
                0.0,
                0.0,
                "eligible",
                "[]",
                0.9,
                "pass",
                json.dumps({}),
            ),
        )
        self.store._conn.commit()
        handshake_job_id = int(self.store._conn.execute("SELECT id FROM jobs WHERE dedupe_key = 'handshake-unsupported'").fetchone()[0])

        page = FakePage(
            url="https://app.joinhandshake.com/jobs/222",
            easy_apply=False,
            greenhouse=False,
            icims=False,
        )
        service = self._service(page)

        run = service.submit_job(job_id=handshake_job_id, profile_name="default", force=True)
        shown = service.show_run(run.application_run_id)

        self.assertEqual(run.status, "blocked")
        self.assertEqual(shown["adapter_name"], "unsupported")
        self.assertEqual(shown["status"], "blocked")
        self.assertEqual(shown["blocked_reason"], "handshake_native_unsupported")

    def test_service_classifies_handshake_fellow_as_specialized_unsupported_target(self) -> None:
        service = self._service(FakePage(url="https://app.joinhandshake.com/jobs/1", easy_apply=False, greenhouse=False, icims=False))

        blocker_reason, details = service._classify_unsupported_target(
            source="handshake",
            target_url="https://ai.joinhandshake.com/fellow-home",
            current_url="https://ai.joinhandshake.com/fellow/dashboard",
        )

        self.assertEqual(blocker_reason, "handshake_fellow_unsupported")
        self.assertEqual(details["portal_family"], "handshake_fellow")

    def test_handshake_fellow_adapter_blocks_on_dashboard_only_page(self) -> None:
        adapter = HandshakeFellowAdapter()
        page = FakePage(
            url="https://ai.joinhandshake.com/fellow/dashboard",
            easy_apply=False,
            greenhouse=False,
            icims=False,
        )

        result = adapter.submit(page=page, resolver=self._resolver(), context=self._adapter_context())

        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.adapter_name, "handshake_fellow")
        self.assertEqual(result.blocker.reason, "handshake_fellow_dashboard_only")

    def test_handshake_fellow_adapter_blocks_with_manual_checkpoint_for_application_context(self) -> None:
        adapter = HandshakeFellowAdapter()

        class ApplicationPage(FakePage):
            def content(self) -> str:
                return "Review your application before submission"

        page = ApplicationPage(
            url="https://ai.joinhandshake.com/fellow/application/review",
            easy_apply=False,
            greenhouse=False,
            icims=False,
        )

        result = adapter.submit(page=page, resolver=self._resolver(), context=self._adapter_context())

        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.blocker.reason, "manual_checkpoint_required")
        self.assertEqual(result.blocker.details["checkpoint"], "handshake_fellow_apply")

    def test_handshake_fellow_adapter_requires_confirmation_path_for_submission(self) -> None:
        adapter = HandshakeFellowAdapter()

        class ConfirmationLikePage(FakePage):
            def content(self) -> str:
                return "Application submitted"

        page = ConfirmationLikePage(
            url="https://ai.joinhandshake.com/fellow/dashboard",
            easy_apply=False,
            greenhouse=False,
            icims=False,
        )

        result = adapter.submit(page=page, resolver=self._resolver(), context=self._adapter_context())

        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.blocker.reason, "handshake_fellow_dashboard_only")

    def test_handshake_fellow_adapter_ignores_script_only_submission_strings(self) -> None:
        adapter = HandshakeFellowAdapter()

        class DashboardScriptPage(FakePage):
            def content(self) -> str:
                return """
                <html>
                  <body>
                    <h1>Awaiting a project match</h1>
                    <script>
                      window.__STATE__ = {"submissionCountByFormId": {}, "activeProjectName": null};
                    </script>
                  </body>
                </html>
                """

        page = DashboardScriptPage(
            url="https://ai.joinhandshake.com/fellow/dashboard",
            easy_apply=False,
            greenhouse=False,
            icims=False,
        )

        result = adapter.submit(page=page, resolver=self._resolver(), context=self._adapter_context())

        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.blocker.reason, "handshake_fellow_dashboard_only")

    def test_handshake_fellow_adapter_treats_dashboard_copy_as_non_application_context(self) -> None:
        adapter = HandshakeFellowAdapter()

        class DashboardCopyPage(FakePage):
            def content(self) -> str:
                return """
                <html>
                  <head><title>Projects | Handshake AI</title></head>
                  <body>
                    <h1>Awaiting a project match</h1>
                    <p>We'll let you know when a project matches your profile.</p>
                    <button>Update your profile</button>
                  </body>
                </html>
                """

        page = DashboardCopyPage(
            url="https://ai.joinhandshake.com/fellow/dashboard",
            easy_apply=False,
            greenhouse=False,
            icims=False,
        )

        result = adapter.submit(page=page, resolver=self._resolver(), context=self._adapter_context())

        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.blocker.reason, "handshake_fellow_dashboard_only")

    def test_service_uses_handshake_browser_profile_for_handshake_jobs(self) -> None:
        self.store._conn.execute(
            """
            INSERT INTO jobs (
                dedupe_key, source, external_id, url, title, company, location, is_internship, posted_at,
                description, compensation_type, work_auth_signals, sponsorship_signals, skills, ingested_at,
                relevance_score, eligibility_confidence, eligibility_status, relevance_hits, profile_match_score,
                profile_match_label, source_metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "handshake-profile-route",
                "handshake",
                "hs-3",
                "https://app.joinhandshake.com/jobs/333",
                "Data Intern",
                "Handshake Co",
                "Remote",
                1,
                "2026-07-10",
                "desc",
                "unknown",
                "[]",
                "[]",
                "[]",
                "2026-07-10T00:00:00+00:00",
                0.0,
                0.0,
                "eligible",
                "[]",
                0.9,
                "pass",
                json.dumps({}),
            ),
        )
        self.store._conn.commit()
        handshake_job_id = int(self.store._conn.execute("SELECT id FROM jobs WHERE dedupe_key = 'handshake-profile-route'").fetchone()[0])

        page = FakePage(
            url="https://app.joinhandshake.com/jobs/333",
            easy_apply=False,
            greenhouse=False,
            icims=False,
        )
        browser_manager = FakeBrowserManager(page)
        service = ApplicationService(
            settings=self.settings,
            store=self.store,
            tailoring_service=self.tailoring_service,
            browser_manager=browser_manager,
        )

        run = service.submit_job(job_id=handshake_job_id, profile_name="default", force=True)

        self.assertEqual(run.status, "blocked")
        self.assertEqual(browser_manager.open_calls[-1], "handshake")

    def test_service_routes_handshake_fellow_target_to_adapter(self) -> None:
        self.store._conn.execute(
            """
            INSERT INTO jobs (
                dedupe_key, source, external_id, url, title, company, location, is_internship, posted_at,
                description, compensation_type, work_auth_signals, sponsorship_signals, skills, ingested_at,
                relevance_score, eligibility_confidence, eligibility_status, relevance_hits, profile_match_score,
                profile_match_label, source_metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "handshake-fellow-route",
                "handshake",
                "hs-4",
                "https://app.joinhandshake.com/jobs/444",
                "Data Intern",
                "Handshake Co",
                "Remote",
                1,
                "2026-07-10",
                "desc",
                "unknown",
                "[]",
                "[]",
                "[]",
                "2026-07-10T00:00:00+00:00",
                0.0,
                0.0,
                "eligible",
                "[]",
                0.9,
                "pass",
                json.dumps({"external_apply_url": "https://ai.joinhandshake.com/fellow-home"}),
            ),
        )
        self.store._conn.commit()
        handshake_job_id = int(self.store._conn.execute("SELECT id FROM jobs WHERE dedupe_key = 'handshake-fellow-route'").fetchone()[0])

        page = FakePage(
            url="https://ai.joinhandshake.com/fellow/dashboard",
            easy_apply=False,
            greenhouse=False,
            icims=False,
        )
        service = self._service(page)

        run = service.submit_job(job_id=handshake_job_id, profile_name="default", force=True)
        shown = service.show_run(run.application_run_id)

        self.assertEqual(run.status, "blocked")
        self.assertEqual(shown["adapter_name"], "handshake_fellow")
        self.assertEqual(shown["blocked_reason"], "handshake_fellow_dashboard_only")

    def test_resume_with_manual_gate_uses_handshake_fellow_message(self) -> None:
        class SubmittedHandshakeFellowAdapter(HandshakeFellowAdapter):
            def submit(self, *, page, resolver, context):
                return SubmitResult(
                    status="submitted",
                    current_url=page.url,
                    confirmation_payload={"message": "submitted after handshake fellow checkpoint"},
                    adapter_name=self.adapter_name,
                )

        page = FakePage(
            url="https://ai.joinhandshake.com/fellow/dashboard",
            easy_apply=False,
            greenhouse=False,
            icims=False,
            confirmation={"message": "Application submitted"},
        )
        service = self._service(page)
        run_id = service.store.create_application_run(
            job_id=1,
            profile_name="default",
            tailoring_artifact_id=1,
            adapter_name="handshake_fellow",
            source="handshake",
            target_url=page.url,
            current_url=page.url,
            status="blocked",
            output_dir=str(self.output_root / "applications" / "default" / "pending"),
            blocked_reason="manual_checkpoint_required",
            blocked_payload={
                "reason": "manual_checkpoint_required",
                "details": {"checkpoint": "handshake_fellow_apply"},
            },
        )
        service.browser_manager = FakeBrowserManager(page)
        service.handshake_fellow_adapter = SubmittedHandshakeFellowAdapter()
        manual_gate_messages: list[str] = []

        resumed = service.resume_with_manual_gate(
            application_run_id=run_id,
            notify=manual_gate_messages.append,
            wait_for_user=lambda: None,
        )

        self.assertEqual(resumed.status, "submitted")
        self.assertIn("Handshake Fellow application steps", manual_gate_messages[0])

    def test_resume_with_manual_gate_prefers_stored_current_url(self) -> None:
        class SubmittedICIMSAdapter(ICIMSAdapter):
            def submit(self, *, page, resolver, context):
                return SubmitResult(
                    status="submitted",
                    current_url=page.url,
                    confirmation_payload={"message": "submitted after manual gate"},
                    adapter_name=self.adapter_name,
                )

        first_page = FakePage(
            url="https://www.linkedin.com/jobs/view/1",
            easy_apply=False,
            greenhouse=False,
            icims=True,
        )
        first_page.external_url = "https://uscareers-medpace.icims.com/jobs/12767/login?iis=Job%20Board&iisn=LinkedIn"
        first_page._frame_texts = [
            "Welcome page\nEnter Your Information\nSoftware Powered by iCIMS",
            "Protected by hCaptcha\nVerify",
        ]
        service = self._service(first_page)
        blocked = service.submit_job(job_id=1, profile_name="default", force=True)
        self.assertEqual(blocked.status, "blocked")
        current_url = "https://uscareers-medpace.icims.com/jobs/12767/candidate?mode=apply"
        service.store.update_application_run(blocked.application_run_id, current_url=current_url)

        resumed_page = FakePage(
            url=current_url,
            easy_apply=False,
            greenhouse=False,
            icims=True,
        )
        manual_gate_messages: list[str] = []
        service.browser_manager = FakeBrowserManager(resumed_page)
        service.icims_adapter = SubmittedICIMSAdapter()
        resumed = service.resume_with_manual_gate(
            application_run_id=blocked.application_run_id,
            notify=manual_gate_messages.append,
            wait_for_user=lambda: None,
        )
        shown = service.show_run(resumed.application_run_id)
        self.assertEqual(resumed.status, "submitted")
        self.assertEqual(shown["target_url"], current_url)
        self.assertIn("Manual gate continuation opened in the browser.", manual_gate_messages[0])

    def test_resume_with_manual_gate_uses_checkpoint_specific_message(self) -> None:
        class SubmittedICIMSAdapter(ICIMSAdapter):
            def submit(self, *, page, resolver, context):
                return SubmitResult(
                    status="submitted",
                    current_url=page.url,
                    confirmation_payload={"message": "submitted after manual checkpoint"},
                    adapter_name=self.adapter_name,
                )

        page = FakePage(
            url="https://uscareers-medpace.icims.com/jobs/12767/candidate?mode=apply",
            easy_apply=False,
            greenhouse=False,
            icims=True,
        )
        service = self._service(page)
        run_id = service.store.create_application_run(
            job_id=1,
            profile_name="default",
            tailoring_artifact_id=1,
            adapter_name="icims",
            source="linkedin",
            target_url=page.url,
            current_url=page.url,
            status="blocked",
            output_dir=str(self.output_root / "applications" / "default" / "pending"),
            blocked_reason="manual_checkpoint_required",
            blocked_payload={
                "reason": "manual_checkpoint_required",
                "details": {"checkpoint": "professional_experience_dropdowns"},
            },
        )
        service.browser_manager = FakeBrowserManager(page)
        service.icims_adapter = SubmittedICIMSAdapter()
        manual_gate_messages: list[str] = []

        resumed = service.resume_with_manual_gate(
            application_run_id=run_id,
            notify=manual_gate_messages.append,
            wait_for_user=lambda: None,
        )

        self.assertEqual(resumed.status, "submitted")
        self.assertIn("Professional Experience dropdown fields", manual_gate_messages[0])

    def test_resume_with_manual_gate_uses_job_specific_gpa_message(self) -> None:
        class SubmittedICIMSAdapter(ICIMSAdapter):
            def submit(self, *, page, resolver, context):
                return SubmitResult(
                    status="submitted",
                    current_url=page.url,
                    confirmation_payload={"message": "submitted after gpa checkpoint"},
                    adapter_name=self.adapter_name,
                )

        page = FakePage(
            url="https://uscareers-medpace.icims.com/jobs/12767/questions",
            easy_apply=False,
            greenhouse=False,
            icims=True,
        )
        service = self._service(page)
        run_id = service.store.create_application_run(
            job_id=1,
            profile_name="default",
            tailoring_artifact_id=1,
            adapter_name="icims",
            source="linkedin",
            target_url=page.url,
            current_url=page.url,
            status="blocked",
            output_dir=str(self.output_root / "applications" / "default" / "pending"),
            blocked_reason="manual_checkpoint_required",
            blocked_payload={
                "reason": "manual_checkpoint_required",
                "details": {"checkpoint": "job_specific_questions_gpa"},
            },
        )
        service.browser_manager = FakeBrowserManager(page)
        service.icims_adapter = SubmittedICIMSAdapter()
        manual_gate_messages: list[str] = []

        resumed = service.resume_with_manual_gate(
            application_run_id=run_id,
            notify=manual_gate_messages.append,
            wait_for_user=lambda: None,
        )

        self.assertEqual(resumed.status, "submitted")
        self.assertIn("undergraduate GPA answer", manual_gate_messages[0])

    def test_handoff_job_pauses_in_visible_browser_and_resumes_same_session(self) -> None:
        class SubmittedGreenhouseAdapter(GreenhouseAdapter):
            def submit(self, *, page, resolver, context):
                return SubmitResult(
                    status="submitted",
                    current_url=page.url,
                    confirmation_payload={"message": f"submitted after handoff at {page.url}"},
                    adapter_name=self.adapter_name,
                )

        page = FakePage(
            url="https://www.linkedin.com/jobs/view/1",
            easy_apply=False,
            greenhouse=True,
        )
        page.external_url = "https://boards.greenhouse.io/acme/jobs/1"
        service = self._service(page)
        service.greenhouse_adapter = SubmittedGreenhouseAdapter()
        messages: list[str] = []

        def _user_takeover() -> None:
            page.url = "https://boards.greenhouse.io/acme/jobs/1/application"

        run = service.handoff_job(
            job_id=1,
            profile_name="default",
            force=True,
            notify=messages.append,
            wait_for_user=_user_takeover,
        )

        shown = service.show_run(run.application_run_id)
        self.assertEqual(run.status, "submitted")
        self.assertEqual(shown["current_url"], "https://boards.greenhouse.io/acme/jobs/1/application")
        self.assertEqual(service.browser_manager.open_calls[-1], "linkedin")
        self.assertEqual(service.browser_manager.headless_calls[-1], False)
        self.assertIn("Live handoff opened in the browser", messages[0])

    def test_blocked_run_persists_debug_screenshot(self) -> None:
        page = FakePage(url="https://uscareers-medpace.icims.com/jobs/12767/login", easy_apply=False, greenhouse=False, icims=True)
        page.external_url = "https://uscareers-medpace.icims.com/jobs/12767/login?iis=Job%20Board&iisn=LinkedIn"
        page._frame_texts = [
            "Welcome page\nEnter Your Information\nSoftware Powered by iCIMS",
            "Protected by hCaptcha\nVerify",
        ]
        service = self._service(page)
        run = service.submit_job(job_id=1, profile_name="default", force=True)
        screenshot_path = Path(run.output_dir) / "blocked.png"
        self.assertTrue(screenshot_path.exists())

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
