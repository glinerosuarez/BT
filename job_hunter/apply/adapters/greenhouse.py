from __future__ import annotations

from urllib.parse import urlparse

from job_hunter.apply.resolver import AnswerResolver, ResolutionError
from job_hunter.apply.types import Blocker, StepSnapshot, SubmitResult


class GreenhouseAdapter:
    adapter_name = "greenhouse"

    def is_greenhouse_target(self, url: str, page=None) -> bool:
        if "greenhouse" in urlparse(url).netloc.lower():
            return True
        checker = getattr(page, "detect_greenhouse", None) if page is not None else None
        return bool(checker()) if callable(checker) else False

    def submit(self, *, page, resolver: AnswerResolver, context) -> SubmitResult:
        if self._has_login_wall(page):
            return self._blocked("login_wall", page, [])
        if self._has_captcha(page):
            return self._blocked("captcha", page, [])
        if self._has_unsupported_widget(page):
            return self._blocked("unsupported_widget", page, [])
        steps: list[StepSnapshot] = []
        for field in self._extract_fields(page):
            question_text = str(field.get("question_text") or field.get("label") or field.get("field_name") or "").strip()
            field_name = str(field.get("field_name") or "")
            field_type = str(field.get("field_type") or "text")
            required = bool(field.get("required", True))
            if not required:
                continue
            if field_type == "file":
                upload_path = context.cover_letter_pdf_path if "cover" in question_text.lower() else context.resume_pdf_path
                self._set_field(page, field, upload_path)
                steps.append(
                    StepSnapshot(
                        step_key=f"upload:{field_name or question_text}",
                        step_label="Upload document",
                        status="completed",
                        field_name=field_name,
                        field_type=field_type,
                        question_text=question_text,
                        answer_source="artifact",
                        answer_value=upload_path,
                    )
                )
                continue
            try:
                resolution = resolver.resolve(question_text=question_text, field_name=field_name, field_type=field_type)
            except ResolutionError as exc:
                return self._blocked(
                    exc.blocker.reason,
                    page,
                    steps,
                    field_name=field_name,
                    field_type=field_type,
                    question_text=question_text,
                    details=exc.blocker.details,
                )
            self._set_field(page, field, resolution.answer)
            steps.append(
                StepSnapshot(
                    step_key=f"field:{field_name or question_text}",
                    step_label="Fill required field",
                    status="completed",
                    field_name=field_name,
                    field_type=field_type,
                    question_text=question_text,
                    answer_source=resolution.source,
                    answer_value=resolution.answer,
                )
            )
        self._submit(page)
        confirmation = self._extract_confirmation(page)
        if not confirmation:
            return self._blocked("ambiguous_confirmation", page, steps)
        return SubmitResult(
            status="submitted",
            current_url=getattr(page, "url", ""),
            confirmation_payload=confirmation,
            steps=steps,
            adapter_name=self.adapter_name,
        )

    def _extract_fields(self, page) -> list[dict[str, object]]:
        extractor = getattr(page, "extract_fields", None)
        return list(extractor()) if callable(extractor) else []

    def _set_field(self, page, field: dict[str, object], value: str) -> None:
        setter = getattr(page, "set_field", None)
        if callable(setter):
            setter(field, value)
            return
        selector = str(field.get("selector") or "")
        field_type = str(field.get("field_type") or "text")
        if field_type == "file":
            page.set_input_files(selector, value)
        elif field_type == "checkbox":
            desired = value.strip().lower() in {"1", "true", "yes", "on"}
            if bool(field.get("checked")) != desired:
                page.click(selector)
        else:
            page.fill(selector, value)

    def _submit(self, page) -> None:
        submitter = getattr(page, "submit_application", None)
        if callable(submitter):
            submitter()
            return
        page.click("button[type=submit]")

    def _extract_confirmation(self, page) -> dict[str, object]:
        extractor = getattr(page, "extract_confirmation", None)
        return dict(extractor() or {}) if callable(extractor) else {}

    def _has_login_wall(self, page) -> bool:
        checker = getattr(page, "detect_login_wall", None)
        return bool(checker()) if callable(checker) else False

    def _has_captcha(self, page) -> bool:
        checker = getattr(page, "detect_captcha", None)
        return bool(checker()) if callable(checker) else False

    def _has_unsupported_widget(self, page) -> bool:
        checker = getattr(page, "detect_unsupported_widget", None)
        return bool(checker()) if callable(checker) else False

    def _blocked(
        self,
        reason: str,
        page,
        steps: list[StepSnapshot],
        *,
        field_name: str = "",
        field_type: str = "",
        question_text: str = "",
        details: dict[str, object] | None = None,
    ) -> SubmitResult:
        return SubmitResult(
            status="blocked",
            current_url=getattr(page, "url", ""),
            blocker=Blocker(
                reason=reason,
                field_name=field_name,
                field_type=field_type,
                question_text=question_text,
                details=details or {},
            ),
            steps=steps,
            adapter_name=self.adapter_name,
        )
