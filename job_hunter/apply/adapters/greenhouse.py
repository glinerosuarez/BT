from __future__ import annotations

from urllib.parse import urlparse

from job_hunter.apply.resolver import AnswerResolver, ResolutionError
from job_hunter.apply.types import AnswerResolution, Blocker, StepSnapshot, SubmitResult

_EMPTY_SELECT_VALUES = {"", "select...", "select"}
_CONFIRMATION_MARKERS = (
    "application submitted",
    "thank you for applying",
    "your application has been submitted",
)


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
            current_value = self._normalized_current_value(field_type=field_type, current_value=str(field.get("current_value") or ""))
            if current_value:
                continue
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
                resolution = self._resolve_field_value(
                    resolver=resolver,
                    question_text=question_text,
                    field_name=field_name,
                    field_type=field_type,
                )
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
        verification_blocker = self._detect_email_verification_blocker(page)
        if verification_blocker is not None:
            return self._blocked(
                verification_blocker.reason,
                page,
                steps,
                field_name=verification_blocker.field_name,
                field_type=verification_blocker.field_type,
                question_text=verification_blocker.question_text,
                details=verification_blocker.details,
            )
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
        if callable(extractor):
            return list(extractor())
        return page.evaluate(
            """
            () => {
              const visible = (el) => {
                const style = window.getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
              };
              const fields = [];
              let counter = 0;
              const pushField = (el, fieldType, extra = {}) => {
                if (!visible(el) || el.disabled) return;
                if (el.getAttribute('aria-hidden') === 'true') return;
                if ((el.className || '').includes('requiredInput')) return;
                counter += 1;
                el.setAttribute('data-jobhunter-field-index', String(counter));
                const id = el.getAttribute('id') || '';
                const label = id
                  ? document.querySelector(`label[for="${id}"]`)
                  : null;
                const questionText = (label?.textContent || el.getAttribute('aria-label') || '').trim();
                const currentValue = fieldType === 'select-one'
                  ? (
                      el.closest('.select__container')?.querySelector('.select__single-value')?.textContent ||
                      el.getAttribute('value') ||
                      ''
                    ).trim()
                  : (el.value || '').trim();
                fields.push({
                  selector: `[data-jobhunter-field-index="${counter}"]`,
                  field_name: el.getAttribute('name') || id || '',
                  field_type: fieldType,
                  question_text: questionText,
                  required: el.required || el.getAttribute('aria-required') === 'true' || extra.required === true,
                  current_value: currentValue,
                });
              };

              for (const el of Array.from(document.querySelectorAll('input, textarea'))) {
                const type = (el.getAttribute('type') || '').toLowerCase();
                if (type === 'hidden') continue;
                if (type === 'file') {
                  const group = el.closest('[role="group"]');
                  pushField(el, 'file', { required: group?.getAttribute('aria-required') === 'true' });
                  continue;
                }
                if (el.getAttribute('role') === 'combobox') {
                  pushField(el, 'select-one');
                  continue;
                }
                if (type === 'checkbox') {
                  pushField(el, 'checkbox');
                  continue;
                }
                if (!['', 'text', 'email', 'tel'].includes(type)) continue;
                pushField(el, 'text');
              }

              return fields;
            }
            """
        )

    def _set_field(self, page, field: dict[str, object], value: str) -> None:
        setter = getattr(page, "set_field", None)
        if callable(setter):
            setter(field, value)
            return
        selector = str(field.get("selector") or "")
        field_type = str(field.get("field_type") or "text")
        if field_type == "file":
            locator = page.locator(selector)
            input_id = ""
            try:
                input_id = locator.get_attribute("id") or ""
            except Exception:
                input_id = ""
            if input_id:
                attach_button = page.locator(f"label[for='{input_id}']").locator("xpath=preceding-sibling::button[1]")
                if attach_button.count() > 0:
                    with page.expect_file_chooser() as chooser_info:
                        attach_button.first.click()
                    chooser_info.value.set_files(value)
                    page.wait_for_timeout(1500)
                    return
            locator.set_input_files(value)
            try:
                locator.dispatch_event("change")
            except Exception:
                pass
            try:
                locator.dispatch_event("input")
            except Exception:
                pass
            page.wait_for_timeout(1500)
        elif field_type == "select-one":
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "on"}:
                value = "Yes"
            elif normalized in {"false", "0", "no", "off"}:
                value = "No"
            locator = page.locator(selector)
            locator.click()
            locator.fill("")
            locator.fill(value)
            page.wait_for_timeout(500)
            locator.press("Enter")
            page.wait_for_timeout(500)
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
        for name in ("Submit application", "Submit Application"):
            locator = page.get_by_role("button", name=name)
            if locator.count() > 0:
                locator.last.click()
                page.wait_for_timeout(3000)
                return
        for selector in ("button[data-testid='btn-submit']", "button[type=submit]"):
            locator = page.locator(selector)
            if locator.count() > 0:
                locator.last.click()
                page.wait_for_timeout(3000)
                return

    def _extract_confirmation(self, page) -> dict[str, object]:
        extractor = getattr(page, "extract_confirmation", None)
        if callable(extractor):
            payload = dict(extractor() or {})
            if payload:
                return payload
        content = page.content().lower()
        if any(marker in content for marker in _CONFIRMATION_MARKERS):
            return {
                "message": "Application submitted",
                "url": getattr(page, "url", ""),
            }
        return {}

    def _has_login_wall(self, page) -> bool:
        checker = getattr(page, "detect_login_wall", None)
        return bool(checker()) if callable(checker) else False

    def _has_captcha(self, page) -> bool:
        checker = getattr(page, "detect_captcha", None)
        return bool(checker()) if callable(checker) else False

    def _has_unsupported_widget(self, page) -> bool:
        checker = getattr(page, "detect_unsupported_widget", None)
        return bool(checker()) if callable(checker) else False

    def complete_email_verification(self, *, page, code: str, steps: list[StepSnapshot]) -> SubmitResult:
        if hasattr(page, "fill_email_verification_code"):
            page.fill_email_verification_code(code)
        else:
            for index, char in enumerate(code[:8]):
                page.fill(f"#security-input-{index}", char)
                page.wait_for_timeout(50)
        steps.append(
            StepSnapshot(
                step_key="greenhouse:email_verification",
                step_label="Fill email verification code",
                status="completed",
                field_name="email_verification",
                field_type="verification_code",
                question_text="Email verification code",
                answer_source="gmail",
                answer_value="redacted",
            )
        )
        self._submit(page)
        verification_blocker = self._detect_email_verification_blocker(page)
        if verification_blocker is not None:
            return self._blocked(
                verification_blocker.reason,
                page,
                steps,
                field_name=verification_blocker.field_name,
                field_type=verification_blocker.field_type,
                question_text=verification_blocker.question_text,
                details=verification_blocker.details,
            )
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

    def _detect_email_verification_blocker(self, page) -> Blocker | None:
        if not hasattr(page, "evaluate"):
            return None
        payload = page.evaluate(
            """
            () => {
              const fieldset = document.querySelector('#email-verification');
              if (!fieldset) {
                return null;
              }
              const legend = (fieldset.querySelector('legend')?.textContent || '').trim();
              const inputs = Array.from(fieldset.querySelectorAll('input')).length;
              return {
                reason: 'email_verification_required',
                field_name: 'email_verification',
                field_type: 'verification_code',
                question_text: legend || 'Email verification required',
                details: { digits: inputs },
              };
            }
            """
        )
        if not payload:
            return None
        return Blocker(
            reason=str(payload.get("reason") or "email_verification_required"),
            field_name=str(payload.get("field_name") or ""),
            field_type=str(payload.get("field_type") or ""),
            question_text=str(payload.get("question_text") or ""),
            details=dict(payload.get("details") or {}),
        )

    def _resolve_field_value(self, *, resolver: AnswerResolver, question_text: str, field_name: str, field_type: str):
        lowered_question = question_text.lower()
        lowered_field = field_name.lower()
        if "bound by any agreements" in lowered_question:
            return resolver.resolve(question_text=question_text, field_name="agreements_restriction", field_type=field_type)
        if lowered_field == "first_name" or "first name" in lowered_question:
            full_name = resolver.profile.identity.full_name.strip().split()
            first_name = full_name[0] if full_name else ""
            return AnswerResolution(answer=first_name, source="structured:identity.full_name")
        if lowered_field == "last_name" or "last name" in lowered_question:
            full_name = resolver.profile.identity.full_name.strip().split()
            last_name = " ".join(full_name[1:]) if len(full_name) > 1 else (full_name[0] if full_name else "")
            return AnswerResolution(answer=last_name, source="structured:identity.full_name")
        return resolver.resolve(question_text=question_text, field_name=field_name, field_type=field_type)

    def _normalized_current_value(self, *, field_type: str, current_value: str) -> str:
        normalized = current_value.strip()
        if field_type == "select-one" and normalized.lower() in _EMPTY_SELECT_VALUES:
            return ""
        return normalized

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
