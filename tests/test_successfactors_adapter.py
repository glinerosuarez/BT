from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from job_hunter.apply.adapters.base import AdapterContext
from job_hunter.apply.adapters.successfactors import SuccessFactorsAdapter
from job_hunter.apply.profile_loader import load_application_inputs
from job_hunter.apply.resolver import AnswerResolver


class FakeLocator:
    def __init__(self, page, selector: str, exists: bool = True):
        self.page = page
        self.selector = selector
        self._exists = exists
        self._checked = False
        self._value = ""

    def count(self) -> int:
        return 1 if self._exists else 0

    def is_visible(self) -> bool:
        return self._exists

    def click(self) -> None:
        self.page.clicked_selectors.append(self.selector)

    def fill(self, value: str) -> None:
        self._value = value
        self.page.filled_fields[self.selector] = value

    def check(self, *, force: bool = False) -> None:
        self._checked = True
        self.page.checked_fields[self.selector] = True

    def uncheck(self, *, force: bool = False) -> None:
        self._checked = False
        self.page.checked_fields[self.selector] = False

    def select_option(self, *, value: str = "", label: str = "") -> None:
        self.page.selected_options[self.selector] = label or value

    def set_input_files(self, path: str) -> None:
        self.page.uploaded_files[self.selector] = path

    def scroll_into_view_if_needed(self) -> None:
        pass

    def dispatch_event(self, event: str) -> None:
        pass

    @property
    def first(self):
        return self


class FakeSuccessFactorsPage:
    def __init__(
        self,
        *,
        url: str,
        content_html: str = "",
        fields: list[dict[str, object]] | None = None,
        confirmation: dict[str, object] | None = None,
        has_login_wall: bool = False,
        present_selectors: set[str] | None = None,
    ):
        self.url = url
        self._content = content_html
        self._fields = fields or []
        self._confirmation = confirmation or {}
        self._has_login_wall = has_login_wall
        self._present_selectors = present_selectors

        self.clicked_selectors: list[str] = []
        self.filled_fields: dict[str, str] = {}
        self.selected_options: dict[str, str] = {}
        self.checked_fields: dict[str, bool] = {}
        self.uploaded_files: dict[str, str] = {}

    def content(self) -> str:
        return self._content

    def locator(self, selector: str) -> FakeLocator:
        if self._present_selectors is not None:
            exists = selector in self._present_selectors
        else:
            exists = not any(
                btn in selector
                for btn in ("a.apply", "a.dialogApplyBtn", "/talentcommunity/apply/", "/career?", "Apply now")
            )
        return FakeLocator(self, selector, exists=exists)

    def get_by_role(self, role: str, name: str) -> FakeLocator:
        return FakeLocator(self, f"role={role}[name={name}]", exists=True)

    def wait_for_timeout(self, ms: int) -> None:
        pass

    def wait_for_load_state(self, state: str, timeout: int = 0) -> None:
        pass

    def extract_fields(self) -> list[dict[str, object]]:
        return self._fields

    def extract_confirmation(self) -> dict[str, object]:
        return self._confirmation

    def detect_login_wall(self) -> bool:
        return self._has_login_wall


class TestSuccessFactorsAdapter(unittest.TestCase):
    def setUp(self):
        self.adapter = SuccessFactorsAdapter()
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.resume_pdf = self.tmp_dir / "resume.pdf"
        self.resume_pdf.write_bytes(b"%PDF-fake-resume")
        self.cover_letter_pdf = self.tmp_dir / "cover_letter.pdf"
        self.cover_letter_pdf.write_bytes(b"%PDF-fake-cover")

        profile, answers = load_application_inputs("profiles", "ml_eng_intern")
        self.profile = profile
        self.resolver = AnswerResolver(profile=profile, answers=answers)
        self.context = AdapterContext(
            resume_pdf_path=str(self.resume_pdf),
            cover_letter_pdf_path=str(self.cover_letter_pdf),
            output_dir=self.tmp_dir,
            profile=self.profile,
        )

    def test_target_detection(self):
        self.assertTrue(self.adapter.is_successfactors_target("https://career4.successfactors.com/careers?company=ametekinc"))
        self.assertTrue(self.adapter.is_successfactors_target("https://jobs.company.jobs2web.com/job/123"))
        self.assertTrue(self.adapter.is_successfactors_target("https://jobs.ametek.com/talentcommunity/apply/1420689600/?locale=en_US"))
        self.assertTrue(self.adapter.is_successfactors_target("https://careers.ametek.com/job/123?career_company=ametekinc"))

        self.assertFalse(self.adapter.is_successfactors_target("https://boards.greenhouse.io/openai/jobs/123"))
        self.assertFalse(self.adapter.is_successfactors_target("https://jobs.ashbyhq.com/anthropic/456"))

    def test_login_wall_blocks_with_bootstrap_required(self):
        page = FakeSuccessFactorsPage(
            url="https://career4.successfactors.com/careers?company=ametekinc",
            content_html="Career Opportunities: Sign In\nAlready have an account?\nEnter your email address and password",
            has_login_wall=True,
        )
        result = self.adapter.submit(page=page, resolver=self.resolver, context=self.context)
        self.assertEqual(result.status, "blocked")
        self.assertIsNotNone(result.blocker)
        self.assertEqual(result.blocker.reason, "candidate_account_bootstrap_required")

    def test_form_filling_and_submission(self):
        fields = [
            {
                "selector": "input[type='file'][data-jh-idx='0']",
                "field_name": "resume",
                "field_type": "file",
                "label": "Resume",
                "required": True,
            },
            {
                "selector": "input[data-jh-idx='1']",
                "field_name": "firstName",
                "field_type": "text",
                "label": "First Name",
                "required": True,
            },
            {
                "selector": "input[data-jh-idx='2']",
                "field_name": "lastName",
                "field_type": "text",
                "label": "Last Name",
                "required": True,
            },
            {
                "selector": "select[data-jh-idx='3']",
                "field_name": "country",
                "field_type": "select-one",
                "label": "Country",
                "required": True,
                "options": [{"value": "US", "label": "United States"}],
            },
            {
                "selector": "input[data-jh-radio-group='4']",
                "field_name": "authorized",
                "field_type": "radio-group",
                "label": "Are you authorized to work in the United States?",
                "required": True,
                "options": [
                    {"selector": "input[data-jh-idx='4_0']", "label": "Yes", "value": "1"},
                    {"selector": "input[data-jh-idx='4_1']", "label": "No", "value": "0"},
                ],
            },
        ]
        page = FakeSuccessFactorsPage(
            url="https://career4.successfactors.com/careers?company=ametekinc/apply",
            content_html="Application Form",
            fields=fields,
        )

        def _on_submit():
            page._fields = []
            page._confirmation = {"message": "Application submitted", "source": "successfactors"}

        page.submit_application = _on_submit

        result = self.adapter.submit(page=page, resolver=self.resolver, context=self.context)
        self.assertEqual(result.status, "submitted")
        self.assertEqual(page.uploaded_files.get("input[type='file'][data-jh-idx='0']"), str(self.resume_pdf))
        self.assertEqual(page.filled_fields.get("input[data-jh-idx='1']"), "Gabriel")
        self.assertEqual(page.filled_fields.get("input[data-jh-idx='2']"), "Linero")
        self.assertEqual(page.selected_options.get("select[data-jh-idx='3']"), "United States")
        self.assertTrue(page.checked_fields.get("input[data-jh-idx='4_0']"))

    def test_confirmation_extraction_on_arrival(self):
        page = FakeSuccessFactorsPage(
            url="https://career4.successfactors.com/careers?company=ametekinc/status",
            content_html="Thank you for applying! Your application has been received.",
        )
        result = self.adapter.submit(page=page, resolver=self.resolver, context=self.context)
        self.assertEqual(result.status, "submitted")
        self.assertEqual(result.adapter_name, "successfactors")


if __name__ == "__main__":
    unittest.main()
