from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from job_hunter.apply.resolver import AnswerResolver, ResolutionError
from job_hunter.apply.types import Blocker, StepSnapshot, SubmitResult
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

_EASY_APPLY_LABELS = (
    "Easy Apply",
    "Solicitud sencilla",
)
_NEXT_LABELS = (
    "Next",
    "Siguiente",
    "Continue",
    "Continuar",
    "Review",
    "Revisar",
    "Save",
    "Guardar",
)
_SUBMIT_LABELS = (
    "Submit application",
    "Enviar solicitud",
)
_DELETE_EDUCATION_LABELS = (
    "Delete education",
    "Eliminar educación",
)
_EDIT_EDUCATION_LABELS = (
    "Edit, education",
    "Editar, educación",
)
_CONFIRM_DELETE_LABELS = (
    "Delete",
    "Eliminar",
)
_UPLOAD_RESUME_LABELS = (
    "Upload resume",
    "Subir currículum",
    "Cargar currículum",
)
_DISMISS_LABELS = (
    "Dismiss",
    "Descartar",
)
_TOP_CHOICE_MARKERS = (
    "mark this job as a top choice",
    "top choice",
)
_EMPTY_SELECT_PLACEHOLDERS = {
    "",
    "month",
    "year",
    "mes",
    "año",
}
_KNOWN_RADIO_QUESTIONS: tuple[tuple[str, str], ...] = (
    ("require sponsorship", "work_authorization.requires_future_sponsorship"),
    ("employment visa status", "work_authorization.requires_future_sponsorship"),
    ("authorized to work", "work_authorization.us_work_authorized"),
    ("work authorization", "work_authorization.us_work_authorized"),
)


class LinkedInEasyApplyAdapter:
    adapter_name = "linkedin"

    def is_easy_apply_available(self, page) -> bool:
        detector = getattr(page, "detect_easy_apply", None)
        if callable(detector):
            return bool(detector())
        try:
            content = page.content().lower()
            if "easy apply" in content or "solicitud sencilla" in content:
                return True
        except Exception:
            pass
        try:
            button = self._easy_apply_button(page)
            return button.count() > 0
        except Exception:
            return False

    def extract_external_apply_url(self, page) -> str:
        extractor = getattr(page, "extract_external_apply_url", None)
        if callable(extractor):
            url = self._normalize_external_apply_url(str(extractor() or "").strip())
            if url:
                return url
        if not hasattr(page, "locator"):
            return ""
        try:
            href = page.evaluate(
                """
                () => {
                  const candidates = Array.from(document.querySelectorAll('a[href], button, [role="button"]'));
                  const match = candidates.find((candidate) => {
                    const text = (candidate.innerText || candidate.getAttribute('aria-label') || '').trim().toLowerCase();
                    return text === 'apply' || text === 'solicitar' || text === 'apply now';
                  });
                  if (!match) return '';
                  if (match.href) return match.href;
                  const anchor = match.closest('a[href]');
                  return anchor ? anchor.href : '';
                }
                """
            )
            normalized = self._normalize_external_apply_url(str(href or "").strip())
            if normalized:
                return normalized
        except Exception:
            pass
        try:
            hrefs = page.locator("a[href]").evaluate_all("els => els.map(el => el.href).filter(Boolean)")
        except Exception:
            return ""
        for href in hrefs:
            normalized = self._normalize_external_apply_url(str(href or "").strip())
            if normalized:
                return normalized
        clicked_url = self._extract_external_apply_url_from_button_click(page)
        if clicked_url:
            return clicked_url
        return ""

    def _extract_external_apply_url_from_button_click(self, page) -> str:
        if not hasattr(page, "locator"):
            return ""
        button = self._external_apply_button(page)
        if button is None:
            return ""

        previous_url = str(getattr(page, "url", "") or "").strip()
        try:
            context = getattr(page, "context", None)
            existing_pages = list(getattr(context, "pages", [])) if context is not None else []
            button.click()
            wait = getattr(page, "wait_for_timeout", None)
            if callable(wait):
                wait(2000)
            current_url = self._normalize_external_apply_url(str(getattr(page, "url", "") or "").strip())
            if current_url:
                return current_url
            if context is not None:
                for candidate_page in getattr(context, "pages", []):
                    if candidate_page in existing_pages:
                        continue
                    candidate_url = self._normalize_external_apply_url(str(getattr(candidate_page, "url", "") or "").strip())
                    if candidate_url:
                        return candidate_url
            current_raw_url = str(getattr(page, "url", "") or "").strip()
            if current_raw_url and current_raw_url != previous_url:
                return self._normalize_external_apply_url(current_raw_url)
        except Exception:
            return ""
        return ""

    def _external_apply_button(self, page):
        selectors = [
            ('button', 'Apply'),
            ('button', 'Apply now'),
            ('a', 'Apply'),
            ('a', 'Apply now'),
            ('[role="button"]', 'Apply'),
            ('[role="button"]', 'Apply now'),
        ]
        for selector, label in selectors:
            try:
                candidate = page.locator(selector).filter(has_text=label).first
                if candidate.count() > 0:
                    return candidate
            except Exception:
                continue
        return None

    def _normalize_external_apply_url(self, url: str) -> str:
        candidate = url.strip()
        if not candidate:
            return ""
        parsed = urlparse(candidate)
        if "linkedin.com" in parsed.netloc and parsed.path.startswith("/safety/go"):
            wrapped = parse_qs(parsed.query).get("url", [""])[0].strip()
            if wrapped:
                candidate = unquote(wrapped)
                parsed = urlparse(candidate)
        if "linkedin.com" in parsed.netloc:
            return ""
        return candidate

    def submit(self, *, page, resolver: AnswerResolver, context) -> SubmitResult:
        if not self.is_easy_apply_available(page):
            return SubmitResult(
                status="blocked",
                current_url=getattr(page, "url", ""),
                blocker=Blocker(reason="linkedin_easy_apply_unavailable"),
                adapter_name=self.adapter_name,
            )

        self._open_easy_apply(page)
        steps: list[StepSnapshot] = []
        for _ in range(16):
            if self._has_login_wall(page):
                return self._blocked("login_wall", page, steps)
            if self._has_captcha(page):
                return self._blocked("captcha", page, steps)
            if self._has_unknown_submit_state(page):
                return self._blocked("ambiguous_submit_state", page, steps)
            if self._handle_delete_confirmation(page, steps):
                continue
            if self._handle_education_removal_flow(page, steps):
                continue
            if self._handle_review_after_education_fix(page, steps):
                continue
            validation_blocker = self._detect_profile_validation_blocker(page)
            if validation_blocker is not None:
                return self._blocked(
                    validation_blocker.reason,
                    page,
                    steps,
                    validation_blocker.field_name,
                    validation_blocker.field_type,
                    validation_blocker.question_text,
                    validation_blocker.details,
                )

            if self._handle_optional_top_choice(page, steps):
                action = self._advance(page)
                if action == "submit":
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
                if action == "next":
                    continue
                return self._blocked("ambiguous_submit_state", page, steps)

            radio_result = self._handle_known_radio_questions(page, resolver, steps)
            if radio_result is not None:
                if radio_result == "resolved":
                    action = self._advance(page)
                    if action == "submit":
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
                    if action == "next":
                        continue
                    return self._blocked("ambiguous_submit_state", page, steps)
                return radio_result

            if self._handle_resume_upload(page, context.resume_pdf_path, steps):
                action = self._advance(page)
                if action == "submit":
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
                if action == "next":
                    continue
                return self._blocked("ambiguous_submit_state", page, steps)

            radio_blocker = self._handle_radio_questions(page, resolver, steps)
            if radio_blocker is not None:
                if isinstance(radio_blocker, SubmitResult):
                    return radio_blocker

            self._handle_education_editor_fields(page, resolver, steps)

            for field in self._extract_fields(page):
                question_text = str(field.get("question_text") or field.get("label") or field.get("field_name") or "").strip()
                field_name = str(field.get("field_name") or "")
                field_type = str(field.get("field_type") or "text")
                required = bool(field.get("required", True))
                current_value = self._normalized_current_value(
                    field_type=field_type,
                    current_value=str(field.get("current_value") or ""),
                )
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
                if current_value:
                    steps.append(
                        StepSnapshot(
                            step_key=f"prefilled:{field_name or question_text}",
                            step_label="Use prefilled field",
                            status="completed",
                            field_name=field_name,
                            field_type=field_type,
                            question_text=question_text,
                            answer_source="prefilled",
                            answer_value=current_value,
                        )
                    )
                    continue
                if not required:
                    continue
                try:
                    resolution = self._resolve_field_value(
                        resolver=resolver,
                        question_text=question_text,
                        field_name=field_name,
                        field_type=field_type,
                    )
                except ResolutionError as exc:
                    return self._blocked(exc.blocker.reason, page, steps, field_name, field_type, question_text, exc.blocker.details)
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

            action = self._advance(page)
            if action == "submit":
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
            if action == "next":
                continue
            return self._blocked("ambiguous_submit_state", page, steps)
        return self._blocked("too_many_steps", page, steps)

    def _extract_fields(self, page) -> list[dict[str, object]]:
        extractor = getattr(page, "extract_fields", None)
        if callable(extractor):
            return list(extractor())
        return page.evaluate(
            """
            () => {
              const dialog = document.querySelector('dialog[open]') || document;
              const nodes = Array.from(dialog.querySelectorAll('input, select, textarea'));
              let counter = 0;
              const visible = (el) => {
                const style = window.getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                return style && style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
              };
              const firstText = (nodes) => {
                for (const node of nodes) {
                  const text = (node.textContent || '').trim();
                  if (text) return text;
                }
                return '';
              };
              const fields = [];
              for (const el of nodes) {
                if (el.type === 'radio') continue;
                if (!visible(el) || el.disabled) continue;
                const id = el.getAttribute('id') || '';
                let label = '';
                if (id) {
                  const linked = dialog.querySelector(`label[for="${id}"]`);
                  if (linked) label = linked.textContent || '';
                }
                if (!label) {
                  const parentLabel = el.closest('label');
                  if (parentLabel) label = parentLabel.textContent || '';
                }
                if (!label) {
                  label = el.getAttribute('aria-label') || '';
                }
                if (!label) {
                  const container = el.closest('div');
                  if (container) {
                    const text = Array.from(container.querySelectorAll('label, legend, div, span, p'))
                      .map((node) => (node.textContent || '').trim())
                      .filter(Boolean);
                    label = text[0] || '';
                  }
                }
                const tagName = el.tagName.toLowerCase();
                const rawType = (el.getAttribute('type') || tagName).toLowerCase();
                const fieldType = rawType === 'tel' ? 'text' : rawType;
                const currentValue = tagName === 'select'
                  ? ((el.options && el.selectedIndex >= 0 && el.options[el.selectedIndex]) ? el.options[el.selectedIndex].text : el.value || '')
                  : (el.value || '');
                const options = tagName === 'select'
                  ? Array.from(el.options || []).map((opt) => ({ value: opt.value || '', text: opt.textContent || '' }))
                  : [];
                counter += 1;
                el.setAttribute('data-jobhunter-field-index', String(counter));
                fields.push({
                  selector: `[data-jobhunter-field-index="${counter}"]`,
                  field_name: el.getAttribute('name') || id || '',
                  field_type: fieldType,
                  question_text: (label || '').trim(),
                  required: el.required || el.getAttribute('aria-required') === 'true',
                  current_value: (currentValue || '').trim(),
                  options,
                });
              }
              const groups = Array.from(dialog.querySelectorAll('fieldset')).filter((fieldset) => fieldset.querySelectorAll('input[type="radio"]').length > 0);
              for (const fieldset of groups) {
                const options = [];
                let selectedText = '';
                for (const input of Array.from(fieldset.querySelectorAll('input[type="radio"]'))) {
                  const wrapper = input.closest('div[role="button"]') || input.parentElement;
                  const text = (wrapper?.innerText || '').trim();
                  if (!text) continue;
                  counter += 1;
                  if (wrapper) {
                    wrapper.setAttribute('data-jobhunter-option-index', String(counter));
                  } else {
                    input.setAttribute('data-jobhunter-option-index', String(counter));
                  }
                  options.push({
                    selector: `[data-jobhunter-option-index="${counter}"]`,
                    text,
                    checked: input.checked,
                  });
                  if (input.checked) {
                    selectedText = text;
                  }
                }
                if (!options.length) continue;
                const fieldContainer = fieldset.parentElement?.parentElement || fieldset.parentElement || fieldset;
                const header = fieldContainer.previousElementSibling || fieldContainer.parentElement?.querySelector(':scope > div');
                const questionText = firstText(header ? Array.from(header.querySelectorAll('p, legend, label, span, div')) : []);
                if (!questionText) continue;
                fields.push({
                  selector: options[0].selector,
                  field_name: '',
                  field_type: 'radio',
                  question_text: questionText,
                  required: questionText.includes('*') || fieldset.getAttribute('aria-required') === 'true',
                  current_value: selectedText,
                  options,
                });
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
            page.set_input_files(selector, value)
        elif field_type == "radio":
            self._select_radio_value(page, field, value)
        elif field_type == "select-one":
            self._select_value(page, field, value)
        elif field_type == "checkbox":
            desired = value.strip().lower() in {"1", "true", "yes", "on"}
            if bool(field.get("checked")) != desired:
                page.click(selector)
        else:
            page.fill(selector, value)

    def _advance(self, page) -> str:
        submitter = getattr(page, "submit_application", None)
        if callable(submitter):
            submitter()
            return "submit"
        for _ in range(2):
            dialog = page.locator("dialog[open]").first
            for label in _SUBMIT_LABELS:
                button = dialog.get_by_role("button", name=label)
                if button.count() > 0:
                    self._click_dialog_button(page, button.first, label)
                    page.wait_for_timeout(3000)
                    return "submit"
            for label in _NEXT_LABELS:
                button = dialog.get_by_role("button", name=label)
                if button.count() > 0:
                    self._click_dialog_button(page, button.first, label)
                    page.wait_for_timeout(3000)
                    return "next"
            if self._dialog_has_loader(page):
                page.wait_for_timeout(3000)
                continue
        return ""

    def _dialog_has_loader(self, page) -> bool:
        return bool(
            page.evaluate(
                """
                () => {
                  const dialog = document.querySelector('dialog[open]') || document;
                  return Boolean(dialog.querySelector('[data-testid="loader"]'));
                }
                """
            )
        )

    def _extract_confirmation(self, page) -> dict[str, object]:
        extractor = getattr(page, "extract_confirmation", None)
        if callable(extractor):
            return dict(extractor() or {})
        body_text = page.locator("body").inner_text(timeout=10000)
        if "application submitted" in body_text.lower() or "solicitud enviada" in body_text.lower():
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

    def _has_unknown_submit_state(self, page) -> bool:
        checker = getattr(page, "detect_ambiguous_submit_state", None)
        return bool(checker()) if callable(checker) else False

    def _handle_resume_upload(self, page, resume_pdf_path: str, steps: list[StepSnapshot]) -> bool:
        if not hasattr(page, "get_by_role"):
            return False
        body_text = self._dialog_text(page)
        lowered = body_text.lower()
        on_resume_page = "resume*" in lowered or "upload resume" in lowered
        if not on_resume_page:
            return False

        dialog = page.locator("dialog[open]").first
        resume_name = Path(resume_pdf_path).name
        upload_button = None
        for label in _UPLOAD_RESUME_LABELS:
            candidate = dialog.get_by_role("button", name=label)
            if candidate.count() > 0:
                upload_button = candidate.first
                break
        if upload_button is not None:
            with page.expect_file_chooser() as chooser_info:
                self._click_dialog_button(page, upload_button, _UPLOAD_RESUME_LABELS[0], fallback_labels=_UPLOAD_RESUME_LABELS)
            chooser_info.value.set_files(resume_pdf_path)
            page.wait_for_timeout(4000)
            self._select_document_option(page, resume_name)
            steps.append(
                StepSnapshot(
                    step_key="resume:upload",
                    step_label="Upload tailored resume",
                    status="completed",
                    field_name="resume",
                    field_type="file",
                    question_text="Resume*",
                    answer_source="artifact",
                    answer_value=resume_pdf_path,
                )
            )
            return True

        if resume_name.lower() not in lowered:
            return False
        self._select_document_option(page, resume_name)
        steps.append(
            StepSnapshot(
                step_key="resume:select_uploaded",
                step_label="Select tailored resume",
                status="completed",
                field_name="resume",
                field_type="radio",
                question_text="Resume*",
                answer_source="artifact",
                answer_value=resume_pdf_path,
            )
        )
        return True

    def _easy_apply_button(self, page):
        for label in _EASY_APPLY_LABELS:
            button = page.get_by_role("button", name=label)
            if button.count() > 0:
                return button
        return page.locator("button").filter(has_text="Easy Apply")

    def _open_easy_apply(self, page) -> None:
        opener = getattr(page, "open_easy_apply", None)
        if callable(opener):
            opener()
            return
        if not hasattr(page, "get_by_role"):
            return
        button = self._easy_apply_button(page)
        if button.count() == 0:
            raise RuntimeError("LinkedIn Easy Apply button was not found.")
        button.first.click()
        page.wait_for_timeout(3000)

    def _resolve_field_value(self, *, resolver: AnswerResolver, question_text: str, field_name: str, field_type: str):
        lowered = question_text.lower()
        if "phone country code" in lowered:
            return resolver.resolve(question_text="country", field_name="identity.country", field_type=field_type)
        return resolver.resolve(question_text=question_text, field_name=field_name, field_type=field_type)

    def _normalized_current_value(self, *, field_type: str, current_value: str) -> str:
        normalized = current_value.strip()
        if field_type in {"select", "select-one"} and normalized.lower() in _EMPTY_SELECT_PLACEHOLDERS:
            return ""
        return normalized

    def _handle_delete_education_validation(self, page, steps: list[StepSnapshot]) -> bool:
        payload = page.evaluate(
            """
            () => {
              const dialogs = Array.from(document.querySelectorAll('dialog[open]'));
              const dialog = dialogs[dialogs.length - 1] || document;
              const text = (dialog.innerText || '').toLowerCase();
              if (!text.includes('edit education') && !text.includes('editar educación')) {
                return null;
              }
              const buttons = Array.from(dialog.querySelectorAll('[data-view-name="remove-section-unify"], button, [role="button"]'));
              const match = buttons.find((button) => {
                const text = (button.innerText || button.getAttribute('aria-label') || '').trim();
                return text === 'Delete education' || text === 'Eliminar educación';
              });
              if (!match) {
                return null;
              }
              return {
                label: (match.innerText || match.getAttribute('aria-label') || '').trim(),
              };
            }
            """
        )
        if not payload:
            return False
        dialogs = page.locator("dialog[open]")
        dialog = dialogs.nth(dialogs.count() - 1)
        delete_button = None
        candidate = dialog.locator("[data-view-name='remove-section-unify']")
        if candidate.count() > 0:
            delete_button = candidate.last
        for label in _DELETE_EDUCATION_LABELS:
            if delete_button is not None:
                break
            candidate = dialog.get_by_role("button", name=label)
            if candidate.count() > 0:
                delete_button = candidate.last
                break
        if delete_button is None:
            for label in _DELETE_EDUCATION_LABELS:
                candidate = dialog.locator("button, [role='button']").filter(has_text=label)
                if candidate.count() > 0:
                    delete_button = candidate.last
                    break
        if delete_button is None:
            return False
        for _ in range(2):
            try:
                delete_button.scroll_into_view_if_needed(timeout=1000)
            except Exception:
                pass
            self._click_dialog_button(page, delete_button, _DELETE_EDUCATION_LABELS[0], fallback_labels=_DELETE_EDUCATION_LABELS)
            page.wait_for_timeout(1500)
            if self._is_visible(page, "[data-view-name='confirm-remove-unify']"):
                break
            clicked = page.evaluate(
                """
                () => {
                  const buttons = Array.from(document.querySelectorAll('[data-view-name="remove-section-unify"]'));
                  const button = buttons[buttons.length - 1];
                  if (!button) return false;
                  button.scrollIntoView({ block: 'center' });
                  button.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
                  button.click();
                  return true;
                }
                """
            )
            if clicked:
                page.wait_for_timeout(1500)
        if not self._is_visible(page, "[data-view-name='confirm-remove-unify']"):
            return False
        steps.append(
            StepSnapshot(
                step_key="linkedin:delete_education",
                step_label="Delete invalid education entry",
                status="completed",
                field_name="education",
                field_type="profile_fixup",
                question_text="Education",
                answer_source="user_approved_fixup",
                answer_value="Delete education",
                payload=dict(payload),
            )
        )
        return True

    def _handle_education_removal_flow(self, page, steps: list[StepSnapshot]) -> bool:
        if self._handle_confirm_education_deletion(page, steps):
            return True
        if self._handle_delete_education_validation(page, steps):
            return True
        return self._handle_open_education_editor(page, steps)

    def _handle_open_education_editor(self, page, steps: list[StepSnapshot]) -> bool:
        payload = page.evaluate(
            """
            () => {
              const dialog = document.querySelector('dialog[open]') || document;
              const text = (dialog.innerText || '').toLowerCase();
              if (text.includes('edit education') || text.includes('editar educación')) {
                return null;
              }
              if (!text.includes('education') && !text.includes('educación')) {
                return null;
              }
              if (!text.includes('this field is required') && !text.includes('este campo es obligatorio')) {
                return null;
              }
              const buttons = Array.from(dialog.querySelectorAll('button[aria-label], [role="button"][aria-label]'))
                .map((button, index) => ({
                  index,
                  label: (button.getAttribute('aria-label') || '').trim(),
                }))
                .filter((button) => ['Edit, education', 'Editar, educación'].includes(button.label));
              if (!buttons.length) {
                return null;
              }
              return {
                count: buttons.length,
                label: buttons[buttons.length - 1].label,
              };
            }
            """
        )
        if not payload:
            return False
        dialog = page.locator("dialog[open]").first
        edit_button = None
        for label in _EDIT_EDUCATION_LABELS:
            candidate = dialog.get_by_role("button", name=label)
            if candidate.count() > 0:
                edit_button = candidate.last
                break
        if edit_button is None:
            return False
        self._click_dialog_button(page, edit_button, _EDIT_EDUCATION_LABELS[0], fallback_labels=_EDIT_EDUCATION_LABELS)
        page.wait_for_timeout(1000)
        steps.append(
            StepSnapshot(
                step_key="linkedin:edit_education",
                step_label="Open education editor",
                status="completed",
                field_name="education",
                field_type="profile_fixup",
                question_text="Education",
                answer_source="user_approved_fixup",
                answer_value="Edit education",
                payload=dict(payload),
            )
        )
        return True

    def _handle_review_after_education_fix(self, page, steps: list[StepSnapshot]) -> bool:
        if self._dialog_has_validation_error(page) or self._dialog_has_remove_education_button(page):
            return False
        payload = page.evaluate(
            """
            () => {
              const dialog = document.querySelector('dialog[open]') || document;
              const reviewButton = dialog.querySelector('[data-view-name="review-unify"]');
              if (!reviewButton) {
                return null;
              }
              const text = (reviewButton.innerText || reviewButton.getAttribute('aria-label') || '').trim();
              if (text !== 'Review' && text !== 'Revisar') {
                return null;
              }
              return { label: text };
            }
            """
        )
        if not payload:
            return False
        dialog = page.locator("dialog[open]").first
        button = dialog.locator("[data-view-name='review-unify']").first
        self._click_dialog_button(page, button, "Review", fallback_labels=("Review", "Revisar"))
        page.wait_for_timeout(2000)
        steps.append(
            StepSnapshot(
                step_key="linkedin:review_after_education_fix",
                step_label="Advance to review after education fix",
                status="completed",
                field_name="education",
                field_type="profile_fixup",
                question_text="Education review",
                answer_source="user_approved_fixup",
                answer_value=str(payload.get("label") or "Review"),
                payload=dict(payload),
            )
        )
        return True

    def _dialog_has_validation_error(self, page) -> bool:
        return bool(
            page.evaluate(
                """
                () => {
                  const dialog = document.querySelector('dialog[open]') || document;
                  const text = (dialog.innerText || '').toLowerCase();
                  return text.includes('this field is required') || text.includes('este campo es obligatorio');
                }
                """
            )
        )

    def _dialog_has_remove_education_button(self, page) -> bool:
        return bool(
            page.evaluate(
                """
                () => {
                  const dialogs = Array.from(document.querySelectorAll('dialog[open]'));
                  const dialog = dialogs[dialogs.length - 1] || document;
                  const text = (dialog.innerText || '').toLowerCase();
                  if (!text.includes('edit education') && !text.includes('editar educación')) {
                    return false;
                  }
                  return Boolean(dialog.querySelector('[data-view-name="remove-section-unify"]'));
                }
                """
            )
        )

    def _handle_education_editor_fields(self, page, resolver: AnswerResolver, steps: list[StepSnapshot]) -> None:
        dialog_text = self._dialog_text(page).lower()
        if "edit education" not in dialog_text and "editar educación" not in dialog_text:
            return
        for field in self._extract_fields(page):
            question_text = str(field.get("question_text") or field.get("label") or field.get("field_name") or "").strip()
            if question_text.lower() != "city":
                continue
            current_value = self._normalized_current_value(
                field_type=str(field.get("field_type") or "text"),
                current_value=str(field.get("current_value") or ""),
            )
            if current_value:
                continue
            resolution = resolver.resolve(question_text="city", field_name="identity.city", field_type="text")
            self._set_field(page, field, resolution.answer)
            steps.append(
                StepSnapshot(
                    step_key="education:city",
                    step_label="Fill education city",
                    status="completed",
                    field_name=str(field.get("field_name") or ""),
                    field_type=str(field.get("field_type") or "text"),
                    question_text=question_text,
                    answer_source=resolution.source,
                    answer_value=resolution.answer,
                )
            )

    def _dialog_contains_any(self, page, markers: tuple[str, ...]) -> bool:
        lowered_markers = [marker.lower() for marker in markers]
        return bool(
            page.evaluate(
                """
                ({ markers }) => {
                  const dialog = document.querySelector('dialog[open]');
                  if (!dialog) return false;
                  const text = (dialog.innerText || '').toLowerCase();
                  return markers.some((marker) => text.includes(marker));
                }
                """,
                {"markers": lowered_markers},
            )
        )

    def _is_visible(self, page, selector: str) -> bool:
        try:
            return page.locator(selector).last.is_visible(timeout=1000)
        except Exception:
            return False

    def _handle_delete_confirmation(self, page, steps: list[StepSnapshot]) -> bool:
        return self._handle_confirm_education_deletion(page, steps)

    def _handle_confirm_education_deletion(self, page, steps: list[StepSnapshot]) -> bool:
        if not self._is_visible(page, "[data-view-name='confirm-remove-unify']"):
            return False
        confirm_button = page.locator("[data-view-name='confirm-remove-unify']").last
        try:
            confirm_button.scroll_into_view_if_needed(timeout=1000)
        except Exception:
            pass
        self._click_dialog_button(
            page,
            confirm_button,
            _CONFIRM_DELETE_LABELS[0],
            fallback_labels=_CONFIRM_DELETE_LABELS,
        )
        page.wait_for_timeout(2000)
        if self._is_visible(page, "[data-view-name='confirm-remove-unify']"):
            clicked = page.evaluate(
                """
                () => {
                  const buttons = Array.from(document.querySelectorAll('[data-view-name="confirm-remove-unify"]'));
                  const button = buttons[buttons.length - 1];
                  if (!button) return false;
                  button.scrollIntoView({ block: 'center' });
                  button.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
                  button.click();
                  return true;
                }
                """
            )
            if clicked:
                page.wait_for_timeout(2000)
        if self._is_visible(page, "[data-view-name='confirm-remove-unify']"):
            return False
        steps.append(
            StepSnapshot(
                step_key="linkedin:confirm_delete_education",
                step_label="Confirm education deletion",
                status="completed",
                field_name="education",
                field_type="profile_fixup",
                question_text="Delete education confirmation",
                answer_source="user_approved_fixup",
                answer_value="Delete",
            )
        )
        return True

    def _handle_delete_confirmation_legacy(self, page, steps: list[StepSnapshot]) -> bool:
        payload = page.evaluate(
            """
            () => {
              const dialogs = Array.from(document.querySelectorAll('dialog[open]'));
              const dialog = dialogs[dialogs.length - 1] || document;
              const text = (dialog.innerText || '').toLowerCase();
              if (!text.includes('eliminar de tu solicitud') && !text.includes('remove from your application')) {
                return null;
              }
              return { question: text };
            }
            """
        )
        if not payload:
            return False
        dialogs = page.locator("dialog[open]")
        dialog = dialogs.nth(dialogs.count() - 1)
        confirm_button = None
        for label in _CONFIRM_DELETE_LABELS:
            candidate = dialog.get_by_role("button", name=label)
            if candidate.count() > 0:
                confirm_button = candidate
                break
        if confirm_button is None:
            for label in _CONFIRM_DELETE_LABELS:
                candidate = dialog.locator("button, [role='button']").filter(has_text=label)
                if candidate.count() > 0:
                    confirm_button = candidate
                    break
        clicked = False
        if confirm_button is not None:
            try:
                confirm_button.first.scroll_into_view_if_needed(timeout=1000)
            except Exception:
                pass
            self._click_dialog_button(
                page,
                confirm_button.first,
                _CONFIRM_DELETE_LABELS[0],
                fallback_labels=_CONFIRM_DELETE_LABELS,
                close_dialog_text=("eliminar de tu solicitud", "remove from your application"),
            )
            clicked = True
        if not clicked or self._dialog_contains_any(page, ("eliminar de tu solicitud", "remove from your application")):
            clicked = bool(
                page.evaluate(
                    """
                    () => {
                      const dialogs = Array.from(document.querySelectorAll('dialog[open]'));
                      const dialog = dialogs[dialogs.length - 1] || document;
                      const buttons = Array.from(dialog.querySelectorAll('button, [role="button"]'));
                      for (const button of buttons) {
                        const text = (button.innerText || button.getAttribute('aria-label') || '').trim();
                        if (text !== 'Eliminar' && text !== 'Delete') continue;
                        button.scrollIntoView({ block: 'center' });
                        button.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
                        button.click();
                        return true;
                      }
                      return false;
                    }
                    """
                )
            )
            if not clicked:
                return False
        page.wait_for_timeout(2000)
        if self._dialog_contains_any(page, ("eliminar de tu solicitud", "remove from your application")):
            return False
        steps.append(
            StepSnapshot(
                step_key="linkedin:confirm_delete_education",
                step_label="Confirm education deletion",
                status="completed",
                field_name="education",
                field_type="profile_fixup",
                question_text="Delete education confirmation",
                answer_source="user_approved_fixup",
                answer_value="Delete",
            )
        )
        return True

    def _detect_profile_validation_blocker(self, page) -> Blocker | None:
        payload = page.evaluate(
            """
            () => {
              const dialog = document.querySelector('dialog[open]') || document;
              const text = (dialog.innerText || '').toLowerCase();
              if (!text.includes('this field is required') && !text.includes('este campo es obligatorio')) {
                return null;
              }
              const selects = Array.from(dialog.querySelectorAll('select'));
              const placeholders = selects
                .map((el) => {
                  const selected = el.selectedIndex >= 0 ? (el.options[el.selectedIndex]?.text || el.value || '') : '';
                  return selected.trim();
                })
                .filter((value) => ['Month', 'Year', 'Mes', 'Año'].includes(value));
              if (!placeholders.length) {
                return null;
              }
              const heading = Array.from(dialog.querySelectorAll('h2, h3, p, span, div'))
                .map((node) => (node.textContent || '').trim())
                .find((value) => value === 'Education' || value === 'Dates attended' || value === 'Work experience');
              return {
                reason: 'linkedin_profile_validation_required',
                field_name: '',
                field_type: 'select',
                question_text: heading || 'LinkedIn profile field',
                details: {
                  placeholders,
                },
              };
            }
            """
        )
        if not payload:
            return None
        return Blocker(
            reason=str(payload.get("reason") or "linkedin_profile_validation_required"),
            field_name=str(payload.get("field_name") or ""),
            field_type=str(payload.get("field_type") or ""),
            question_text=str(payload.get("question_text") or ""),
            details=dict(payload.get("details") or {}),
        )

    def _select_value(self, page, field: dict[str, object], value: str) -> None:
        selector = str(field.get("selector") or "")
        options = field.get("options") or []
        normalized_target = value.strip().lower()
        for option in options:
            text = str(option.get("text") or "").strip()
            raw_value = str(option.get("value") or "").strip()
            if text.lower() == normalized_target or raw_value.lower() == normalized_target:
                page.select_option(selector, value=raw_value)
                return
            if normalized_target and normalized_target in text.lower():
                page.select_option(selector, value=raw_value)
                return
        if options:
            first_value = str(options[0].get("value") or "").strip()
            if first_value:
                page.select_option(selector, value=first_value)

    def _select_radio_value(self, page, field: dict[str, object], value: str) -> None:
        options = field.get("options") or []
        normalized_target = value.strip().lower()
        aliases = {normalized_target}
        if normalized_target == "true":
            aliases.update({"yes", "on", "1"})
        elif normalized_target == "false":
            aliases.update({"no", "off", "0"})
        for option in options:
            text = str(option.get("text") or "").strip()
            lowered_text = text.lower()
            if lowered_text in aliases or any(alias and alias in lowered_text for alias in aliases):
                page.click(str(option.get("selector") or ""))
                page.wait_for_timeout(500)
                return
        for alias in aliases:
            if alias in {"yes", "no"} and self._click_dialog_option_text(page, alias.title()):
                return

    def _handle_known_radio_questions(self, page, resolver: AnswerResolver, steps: list[StepSnapshot]) -> str | SubmitResult | None:
        dialog_text = self._dialog_text(page)
        lowered = dialog_text.lower()
        if "yes" not in lowered or "no" not in lowered:
            return None

        question_text = ""
        structured_key = ""
        for marker, candidate_key in _KNOWN_RADIO_QUESTIONS:
            if marker in lowered:
                structured_key = candidate_key
                question_text = self._find_question_text(dialog_text, marker)
                break
        if not structured_key:
            return None

        try:
            resolution = resolver.resolve(
                question_text=question_text or structured_key,
                field_name=structured_key,
                field_type="radio",
            )
        except ResolutionError as exc:
            return self._blocked(exc.blocker.reason, page, steps, structured_key, "radio", question_text, exc.blocker.details)

        answer_text = "Yes" if resolution.answer.strip().lower() in {"1", "true", "yes", "on"} else "No"
        if not self._click_dialog_option_text(page, answer_text):
            return self._blocked("missing_required_answer", page, steps, structured_key, "radio", question_text, {"expected_option": answer_text})
        steps.append(
            StepSnapshot(
                step_key=f"field:{structured_key}",
                step_label="Fill required field",
                status="completed",
                field_name=structured_key,
                field_type="radio",
                question_text=question_text,
                answer_source=resolution.source,
                answer_value=resolution.answer,
            )
        )
        return "resolved"

    def _find_question_text(self, dialog_text: str, marker: str) -> str:
        for line in dialog_text.splitlines():
            text = line.strip()
            if marker in text.lower():
                return text
        return marker

    def _click_dialog_option_text(self, page, option_text: str) -> bool:
        clicked = page.evaluate(
            """
            ({ optionText }) => {
              const dialogs = Array.from(document.querySelectorAll('dialog[open]'));
              const dialog = dialogs[dialogs.length - 1] || document;
              const loweredTarget = optionText.toLowerCase();
              const radioOptions = Array.from(dialog.querySelectorAll('[role="radio"]'));
              for (const option of radioOptions) {
                const text = (option.innerText || '').trim().toLowerCase();
                if (text !== loweredTarget) continue;
                option.click();
                return true;
              }
              const radios = Array.from(dialog.querySelectorAll('input[type="radio"]'));
              for (const input of radios) {
                const wrapper = input.closest('div[role="button"]') || input.parentElement;
                const text = (wrapper?.innerText || '').trim().toLowerCase();
                if (text !== loweredTarget) continue;
                if (wrapper) {
                  wrapper.click();
                } else {
                  input.click();
                }
                return true;
              }
              return false;
            }
            """,
            {"optionText": option_text},
        )
        if clicked:
            page.wait_for_timeout(500)
        return bool(clicked)

    def _click_dialog_button(
        self,
        page,
        locator,
        label: str,
        fallback_labels: tuple[str, ...] | None = None,
        close_dialog_text: tuple[str, ...] | None = None,
    ) -> None:
        labels = fallback_labels or (label,)
        close_markers = tuple(text.lower() for text in (close_dialog_text or ()))

        def dialog_still_open() -> bool:
            if not close_markers:
                return False
            return bool(
                page.evaluate(
                    """
                    ({ closeMarkers }) => {
                      const dialogs = Array.from(document.querySelectorAll('dialog[open]'));
                      const dialog = dialogs[dialogs.length - 1];
                      if (!dialog) return false;
                      const text = (dialog.innerText || '').toLowerCase();
                      return closeMarkers.some((marker) => text.includes(marker));
                    }
                    """,
                    {"closeMarkers": list(close_markers)},
                )
            )

        def dom_click() -> bool:
            return bool(
                page.evaluate(
                    """
                    ({ labels }) => {
                      const dialogs = Array.from(document.querySelectorAll('dialog[open]'));
                      const dialog = dialogs[dialogs.length - 1] || document;
                      const loweredTargets = labels.map((label) => label.toLowerCase());
                      const buttons = Array.from(dialog.querySelectorAll('button, [role="button"]'));
                      for (const button of buttons) {
                        const text = (button.innerText || button.getAttribute('aria-label') || '').trim().toLowerCase();
                        if (!loweredTargets.includes(text)) continue;
                        button.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
                        button.click();
                        return true;
                      }
                      return false;
                    }
                    """,
                    {"labels": list(labels)},
                )
            )

        try:
            locator.click(timeout=1000)
        except Exception:
            pass
        else:
            if not dialog_still_open():
                return

        try:
            locator.click(timeout=1000, force=True)
        except Exception:
            pass
        else:
            if not dialog_still_open():
                return

        try:
            locator.dispatch_event("click")
        except Exception:
            pass
        else:
            if not dialog_still_open():
                return

        try:
            locator.evaluate(
                """
                (button) => {
                  button.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
                  button.click();
                }
                """
            )
        except Exception:
            pass
        else:
            if not dialog_still_open():
                return

        if dom_click() and not dialog_still_open():
            return

        try:
            locator.click(timeout=1000, force=True)
        except PlaywrightTimeoutError:
            if not dom_click():
                raise

    def _select_document_option(self, page, document_name: str) -> bool:
        if not hasattr(page, "evaluate"):
            return False
        selected = page.evaluate(
            """
            ({ documentName }) => {
              const dialog = document.querySelector('dialog[open]') || document;
              const loweredName = documentName.toLowerCase();
              const inputs = Array.from(dialog.querySelectorAll('input[type="radio"]'));
              for (const input of inputs) {
                const container = input.closest('div[role="button"]') || input.parentElement;
                const text = (container?.innerText || '').toLowerCase();
                if (!text.includes(loweredName)) continue;
                if (!input.checked) {
                  if (container) {
                    container.click();
                  } else {
                    input.click();
                  }
                }
                return true;
              }
              return false;
            }
            """,
            {"documentName": document_name},
        )
        page.wait_for_timeout(1000)
        return bool(selected)

    def _handle_radio_questions(self, page, resolver: AnswerResolver, steps: list[StepSnapshot]) -> SubmitResult | None:
        groups = page.evaluate(
            """
            () => {
              const dialog = document.querySelector('dialog[open]') || document;
              const groups = [];
              let counter = 0;
              const firstQuestion = (column, optionTexts) => {
                const candidates = Array.from(column.querySelectorAll('p, legend, label, span, div'))
                  .map((node) => (node.textContent || '').trim())
                  .filter(Boolean);
                return candidates.find((text) => {
                  const lowered = text.toLowerCase();
                  if (optionTexts.some((option) => option === text)) return false;
                  return text.includes('?') || text.endsWith('*') || lowered.includes('authorized') || lowered.includes('sponsorship');
                }) || '';
              };
              for (const fieldset of Array.from(dialog.querySelectorAll('fieldset'))) {
                const radios = Array.from(fieldset.querySelectorAll('input[type="radio"]'));
                if (!radios.length) continue;
                const options = [];
                let currentValue = '';
                for (const input of radios) {
                  const wrapper = input.closest('div[role="button"]') || input.parentElement;
                  const text = (wrapper?.innerText || '').trim();
                  if (!text) continue;
                  counter += 1;
                  if (wrapper) {
                    wrapper.setAttribute('data-jobhunter-radio-index', String(counter));
                  } else {
                    input.setAttribute('data-jobhunter-radio-index', String(counter));
                  }
                  options.push({
                    selector: `[data-jobhunter-radio-index="${counter}"]`,
                    text,
                    checked: input.checked,
                  });
                  if (input.checked) currentValue = text;
                }
                if (!options.length) continue;
                const column = fieldset.closest('[data-testid="lazy-column"]') || fieldset.parentElement || fieldset;
                const questionText = firstQuestion(column, options.map((option) => option.text));
                if (!questionText) continue;
                groups.push({
                  question_text: questionText,
                  field_type: 'radio',
                  field_name: '',
                  required: questionText.includes('*'),
                  current_value: currentValue,
                  options,
                });
              }
              return groups;
            }
            """
        )
        for group in groups:
            question_text = str(group.get("question_text") or "").strip()
            current_value = str(group.get("current_value") or "").strip()
            required = bool(group.get("required", True))
            if current_value or not required:
                continue
            try:
                resolution = self._resolve_field_value(
                    resolver=resolver,
                    question_text=question_text,
                    field_name="",
                    field_type="radio",
                )
            except ResolutionError as exc:
                return self._blocked(exc.blocker.reason, page, steps, "", "radio", question_text, exc.blocker.details)
            self._select_radio_value(page, group, resolution.answer)
            steps.append(
                StepSnapshot(
                    step_key=f"field:{question_text}",
                    step_label="Fill required field",
                    status="completed",
                    field_type="radio",
                    question_text=question_text,
                    answer_source=resolution.source,
                    answer_value=resolution.answer,
                )
            )
        return None

    def _handle_optional_top_choice(self, page, steps: list[StepSnapshot]) -> bool:
        lowered = self._dialog_text(page).lower()
        if not any(marker in lowered for marker in _TOP_CHOICE_MARKERS):
            return False
        steps.append(
            StepSnapshot(
                step_key="linkedin:skip_top_choice",
                step_label="Skip optional top choice",
                status="completed",
                question_text="Mark this job as a top choice (Optional)",
                answer_source="skip_optional",
                answer_value="",
            )
        )
        return True

    def _dialog_text(self, page) -> str:
        if not hasattr(page, "locator"):
            return ""
        dialog = page.locator("dialog[open]").first
        if dialog.count() == 0:
            return page.locator("body").inner_text(timeout=10000)
        return dialog.inner_text(timeout=10000)

    def _blocked(
        self,
        reason: str,
        page,
        steps: list[StepSnapshot],
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
