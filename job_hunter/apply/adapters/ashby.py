from __future__ import annotations

import re
from urllib.parse import urlparse

from job_hunter.apply.resolver import AnswerResolver, ResolutionError
from job_hunter.apply.types import AnswerResolution, Blocker, StepSnapshot, SubmitResult


_CONFIRMATION_MARKERS = (
    "application submitted",
    "thank you for applying",
    "your application has been submitted",
    "we have received your application",
    "application received",
    "thank you for submitting",
    "your application was successfully submitted",
)


class AshbyAdapter:
    """Submit public Ashby application forms without guessing required answers."""

    adapter_name = "ashby"

    def is_ashby_target(self, url: str, page=None) -> bool:
        host = urlparse(url).netloc.lower()
        if "ashbyhq.com" in host:
            return True
        checker = getattr(page, "detect_ashby", None) if page is not None else None
        return bool(checker()) if callable(checker) else False

    def submit(self, *, page, resolver: AnswerResolver, context) -> SubmitResult:
        steps: list[StepSnapshot] = []
        self._open_application_form(page)
        # A manual CAPTCHA handoff may have completed the submit while the
        # session stayed open. Recognize that terminal page before attempting
        # to refill or click anything else.
        existing_confirmation = self._extract_rendered_confirmation(page)
        if existing_confirmation:
            return SubmitResult(
                status="submitted",
                current_url=getattr(page, "url", ""),
                confirmation_payload=existing_confirmation,
                steps=steps,
                adapter_name=self.adapter_name,
            )
        if self._has_login_wall(page):
            return self._blocked("login_wall", page, steps)
        if self._has_captcha_challenge(page):
            return self._blocked("captcha", page, steps)

        for _ in range(3):
            blocker, filled_count = self._fill_required_fields(
                page=page,
                resolver=resolver,
                context=context,
                steps=steps,
            )
            if blocker is not None:
                return blocker
            if filled_count == 0:
                break

        if not self._submit(page):
            return self._blocked("unsupported_widget", page, steps)
        self._wait_for_submission_state(page)
        if self._has_captcha_challenge(page):
            return self._blocked("captcha", page, steps)
        submission_failure = self._submission_failure(page)
        if submission_failure is not None:
            return self._blocked(submission_failure, page, steps)
        confirmation = self._extract_confirmation(page)
        if not confirmation and self._has_unsatisfied_invisible_recaptcha(page):
            return self._blocked("captcha", page, steps)
        if not confirmation:
            return self._blocked("ambiguous_confirmation", page, steps)
        return SubmitResult(
            status="submitted",
            current_url=getattr(page, "url", ""),
            confirmation_payload=confirmation,
            steps=steps,
            adapter_name=self.adapter_name,
        )

    def _open_application_form(self, page) -> None:
        # Test doubles expose fields directly; live Ashby postings begin on a landing page.
        if callable(getattr(page, "extract_fields", None)):
            return
        try:
            if page.locator("#_systemfield_resume, input[name='_systemfield_email']").count() > 0:
                return
            apply_button = page.get_by_role("button", name="Apply for this Job", exact=True)
            apply_button.wait_for(state="visible", timeout=5000)
            apply_button.click()
            self._wait(page, 750)
        except Exception:
            return

    def _fill_required_fields(self, *, page, resolver: AnswerResolver, context, steps: list[StepSnapshot]) -> tuple[SubmitResult | None, int]:
        filled_count = 0
        for field in self._extract_fields(page):
            field_name = str(field.get("field_name") or "")
            question_text = str(field.get("question_text") or field.get("label") or field_name).strip()
            field_type = str(field.get("field_type") or "text")
            required = bool(field.get("required", True))
            if not required or str(field.get("current_value") or "").strip():
                continue

            if field_type == "unsupported":
                return (
                    self._blocked(
                        "unsupported_widget",
                        page,
                        steps,
                        field_name=field_name,
                        field_type=field_type,
                        question_text=question_text,
                    ),
                    filled_count,
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
                filled_count += 1
                continue

            try:
                resolution = self._resolve_field_value(
                    resolver=resolver,
                    question_text=question_text,
                    field_name=field_name,
                    field_type=field_type,
                )
            except ResolutionError as exc:
                return (
                    self._blocked(
                        exc.blocker.reason,
                        page,
                        steps,
                        field_name=field_name,
                        field_type=field_type,
                        question_text=question_text,
                        details=exc.blocker.details,
                    ),
                    filled_count,
                )

            try:
                self._set_field(page, field, resolution.answer)
            except Exception as exc:
                return (
                    self._blocked(
                        "unsupported_widget",
                        page,
                        steps,
                        field_name=field_name,
                        field_type=field_type,
                        question_text=question_text,
                        details={"error": str(exc)},
                    ),
                    filled_count,
                )
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
            filled_count += 1
        return None, filled_count

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
                return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
              };
              // Ashby uses its public class on basic controls but only a
              // data-field-entry-id on some radio and checkbox groups.
              const fieldEntries = Array.from(document.querySelectorAll(
                '.ashby-application-form-field-entry, [data-field-entry-id]'
              ));
              const labelFor = (el, entry) => {
                const id = el.getAttribute('id') || '';
                if (id) {
                  const lbl = entry?.querySelector(`label[for="${CSS.escape(id)}"]`)?.textContent || document.querySelector(`label[for="${CSS.escape(id)}"]`)?.textContent;
                  if (lbl) return lbl.trim();
                  if (id.includes('degree')) return 'Degree';
                  if (id.includes('major') || id.includes('discipline')) return 'Field of Study';
                  if (id.includes('isCurrent')) return 'Still Student?';
                }
                const placeholder = el.getAttribute('placeholder') || '';
                if (/school/i.test(placeholder)) return 'School';
                
                if (el.tagName === 'SELECT' && (entry?.getAttribute('data-field-path') === '_systemfield_education_history' || entry?.textContent?.includes('Education History'))) {
                  const selects = Array.from(entry.querySelectorAll('select'));
                  const sIdx = selects.indexOf(el);
                  if (sIdx === 0) return 'Start date month';
                  if (sIdx === 1) return 'Start date year';
                  if (sIdx === 2) return 'End date month';
                  if (sIdx === 3) return 'End date year';
                }
                
                return (
                  entry?.querySelector('.ashby-application-form-question-title, label')?.textContent ||
                  el.getAttribute('aria-label') ||
                  ''
                ).trim();
              };

              const fields = [];
              let counter = 0;
              const selectorFor = (el) => {
                const id = el.getAttribute('id') || '';
                if (id) return `#${CSS.escape(id)}`;
                counter += 1;
                el.setAttribute('data-jobhunter-ashby-field', String(counter));
                return `[data-jobhunter-ashby-field="${counter}"]`;
              };
              for (const entry of fieldEntries) {
                if (!visible(entry)) continue;
                const yesNo = entry.querySelector('div[class*="yesno"]');
                if (yesNo) {
                  const input = yesNo.querySelector('input');
                  const buttons = Array.from(yesNo.querySelectorAll('button')).filter(visible);
                  if (!input || !buttons.length) continue;
                  counter += 1;
                  yesNo.setAttribute('data-jobhunter-ashby-choice', String(counter));
                  const selected = buttons.find((button) => button.getAttribute('aria-pressed') === 'true' || /selected|active/i.test(button.className));
                  fields.push({
                    selector: `[data-jobhunter-ashby-choice="${counter}"]`,
                    field_name: input.getAttribute('name') || '',
                    field_type: 'yes-no',
                    question_text: (entry.querySelector('label')?.textContent || '').trim(),
                    required: input.required || entry.querySelector('label[class*="required"]') !== null,
                    current_value: (selected?.textContent || '').trim(),
                    options: buttons.map((button) => ({ label: (button.textContent || '').trim() })),
                  });
                  continue;
                }
                const radioInputs = Array.from(entry.querySelectorAll('input[type="radio"]'));
                if (radioInputs.length) {
                  const options = radioInputs.map((input) => {
                    const id = input.getAttribute('id') || '';
                    return {
                      label: (entry.querySelector(`label[for="${CSS.escape(id)}"]`)?.textContent || '').trim(),
                      checked: input.checked,
                    };
                  }).filter((option) => option.label);
                  if (options.length) {
                    fields.push({
                      selector: selectorFor(entry),
                      field_name: radioInputs[0].getAttribute('name') || entry.getAttribute('data-field-path') || '',
                      field_type: 'radio-group',
                      question_text: (entry.querySelector('.ashby-application-form-question-title, label')?.textContent || '').trim(),
                      required: radioInputs.some((input) => input.required) || entry.querySelector('label[class*="required"]') !== null,
                      current_value: (options.find((option) => option.checked)?.label || '').trim(),
                      options,
                    });
                    continue;
                  }
                }
                const isEducation = entry.getAttribute('data-field-path') === '_systemfield_education_history' || entry.querySelector('.ashby-application-form-input-education-add') !== null;
                const checkboxInputs = Array.from(entry.querySelectorAll('input[type="checkbox"]'));
                if (checkboxInputs.length && !isEducation) {
                  const options = checkboxInputs.map((input) => {

                    const id = input.getAttribute('id') || '';
                    return {
                      label: (entry.querySelector(`label[for="${CSS.escape(id)}"]`)?.textContent || '').trim(),
                      checked: input.checked,
                    };
                  }).filter((option) => option.label);
                  if (options.length) {
                    fields.push({
                      selector: selectorFor(entry),
                      field_name: checkboxInputs[0].getAttribute('name') || entry.getAttribute('data-field-path') || '',
                      field_type: options.length === 1 ? 'checkbox' : 'checkbox-group',
                      question_text: (entry.querySelector('.ashby-application-form-question-title, label')?.textContent || '').trim(),
                      required: checkboxInputs.some((input) => input.required) || entry.querySelector('label[class*="required"]') !== null,
                      current_value: options.filter((option) => option.checked).map((option) => option.label).join(' || '),
                      options,
                    });
                    continue;
                  }
                }
                let hasHandledField = false;
                let hasUnsupportedControl = false;
                for (const el of Array.from(entry.querySelectorAll('input, textarea, select'))) {
                  const inputType = (el.getAttribute('type') || '').toLowerCase();
                  if (inputType === 'hidden') continue;
                  if (inputType === 'checkbox' || inputType === 'radio') {
                    hasUnsupportedControl = true;
                    continue;
                  }
                  if (inputType === 'file' && !el.getAttribute('id')) continue;
                  const fieldType = inputType === 'file'
                    ? 'file'
                    : (el.tagName === 'TEXTAREA'
                      ? 'textarea'
                      : (el.tagName === 'SELECT'
                        ? 'select-one'
                        : (el.getAttribute('role') === 'combobox' || el.getAttribute('aria-autocomplete') === 'list'
                          ? 'combobox'
                          : 'text')));
                  fields.push({
                    selector: selectorFor(el),
                    field_name: el.getAttribute('name') || el.getAttribute('id') || '',
                    field_type: fieldType,
                    question_text: labelFor(el, entry),
                    required: el.required || entry.querySelector('label[class*="required"]') !== null,
                    current_value: (el.value || '').trim(),
                  });
                  hasHandledField = true;
                }
                if (!hasHandledField && (hasUnsupportedControl || entry.querySelector('[role="combobox"], [role="listbox"]'))) {
                  fields.push({
                    selector: '',
                    field_name: entry.getAttribute('data-field-path') || '',
                    field_type: 'unsupported',
                    question_text: (entry.querySelector('label')?.textContent || '').trim(),
                    required: entry.querySelector('label[class*="required"]') !== null,
                    current_value: '',
                  });
                }
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
            locator.set_input_files(value)
            page.wait_for_function("selector => document.querySelector(selector)?.files?.length > 0", arg=selector)
            self._wait(page, 1000)
            return
        if field_type == "yes-no":
            normalized = value.strip().lower()
            if normalized not in {"yes", "no"}:
                raise RuntimeError(f"Ashby yes/no field requires Yes or No, got {value!r}")
            button = page.locator(selector).get_by_role("button", name=normalized.capitalize(), exact=True)
            if button.count() == 0:
                raise RuntimeError("Could not locate Ashby yes/no option")
            button.click()
            self._wait(page, 250)
            return
        if field_type == "select-one":
            sel = page.locator(selector)
            try:
                sel.select_option(label=value)
            except Exception:
                try:
                    sel.select_option(value=value)
                except Exception:
                    month_map = {
                        "01": "January", "1": "January", "02": "February", "2": "February",
                        "03": "March", "3": "March", "04": "April", "4": "April",
                        "05": "May", "5": "May", "06": "June", "6": "June",
                        "07": "July", "7": "July", "08": "August", "8": "August",
                        "09": "September", "9": "September", "10": "October",
                        "11": "November", "12": "December"
                    }
                    m_label = month_map.get(value.strip())
                    if m_label:
                        sel.select_option(label=m_label)
                    else:
                        raise
            return
        if field_type == "combobox":
            self._set_combobox_field(page, selector, value)
            return
        if field_type == "checkbox":
            if field.get("options"):
                self._set_option_group(page, selector, field, value, multiple=False)
            else:
                desired = value.strip().lower() in {"1", "true", "yes", "on"}
                page.locator(selector).set_checked(desired)
            return
        if field_type in {"radio-group", "checkbox-group"}:
            self._set_option_group(page, selector, field, value, multiple=field_type == "checkbox-group")
            return
        page.locator(selector).fill(value)


    def _set_combobox_field(self, page, selector: str, value: str) -> None:
        input_field = page.locator(selector)
        input_field.fill(value)
        # Ashby asynchronously mounts its location results after the keystroke.
        # Poll the semantic option role instead of depending on a CSS class or
        # accepting the first keyboard-highlighted suggestion.
        for _ in range(12):
            options = page.locator("[role='option']:visible")
            labels = [label.strip() for label in options.all_text_contents() if label.strip()]
            matches = self._matching_option_labels(labels, value, multiple=False)
            if matches:
                option = options.filter(has_text=matches[0]).first
                if option.count() > 0:
                    option.click()
                    self._wait(page, 250)
                    return
            self._wait(page, 250)
        raise RuntimeError(f"Could not find an Ashby combobox option matching {value!r}")

    def _set_option_group(
        self,
        page,
        selector: str,
        field: dict[str, object],
        value: str,
        *,
        multiple: bool,
    ) -> None:
        options = field.get("options")
        labels = [str(option.get("label") or "").strip() for option in options if isinstance(option, dict)] if isinstance(options, list) else []
        matches = self._matching_option_labels(labels, value, multiple=multiple)
        if not matches:
            raise RuntimeError(f"Could not find an Ashby option matching {value!r}")
        group = page.locator(selector)
        for label in matches:
            option = group.get_by_text(label, exact=True)
            if option.count() == 0:
                raise RuntimeError(f"Could not locate Ashby option {label!r}")
            option.click()
            self._wait(page, 150)

    @staticmethod
    def _matching_option_labels(labels: list[str], value: str, *, multiple: bool) -> list[str]:
        requested = [item.strip() for item in value.split("||") if item.strip()]
        if not multiple:
            requested = requested[:1]
        matches: list[str] = []
        for item in requested:
            normalized_item = _normalize_option(item)
            exact = next((label for label in labels if _normalize_option(label) == normalized_item), None)
            if exact is not None:
                matches.append(exact)
                continue
            # Sponsorship questions sometimes express a negative response as
            # a specific immigration status or a plain "None" option.
            if normalized_item in {"no", "false"}:
                negative = next(
                    (
                        label
                        for label in labels
                        if _normalize_option(label) in {"none", "not applicable", "n a"}
                    ),
                    None,
                )
                if negative is not None:
                    matches.append(negative)
                    continue
            item_tokens = set(_option_tokens(item))
            token_match = next(
                (
                    label
                    for label in labels
                    if (label_tokens := set(_option_tokens(label)))
                    and (label_tokens <= item_tokens or item_tokens <= label_tokens)
                ),
                None,
            )
            if token_match is None:
                return []
            matches.append(token_match)
        return matches

    def _resolve_field_value(self, *, resolver: AnswerResolver, question_text: str, field_name: str, field_type: str) -> AnswerResolution:
        lowered_name = field_name.lower()
        if lowered_name == "_systemfield_name":
            return AnswerResolution(answer=resolver.profile.identity.full_name, source="structured:identity.full_name")
        if lowered_name == "_systemfield_email":
            return AnswerResolution(answer=resolver.profile.identity.email, source="structured:identity.email")
        if "linkedin" in question_text.lower():
            return AnswerResolution(answer=resolver.profile.identity.linkedin_url, source="structured:identity.linkedin_url")
        return resolver.resolve_for_portal(
            portal=self.adapter_name,
            question_text=question_text,
            field_name=field_name,
            field_type=field_type,
        )

    def _submit(self, page) -> bool:
        submitter = getattr(page, "submit_application", None)
        if callable(submitter):
            submitter()
            return True
        try:
            button = page.get_by_role("button", name="Submit Application", exact=True)
            if button.count() == 0:
                return False
            button.click()
            return True
        except Exception:
            return False

    def _extract_confirmation(self, page) -> dict[str, object]:
        extractor = getattr(page, "extract_confirmation", None)
        if callable(extractor):
            payload = dict(extractor() or {})
            if payload:
                return payload
        return self._extract_rendered_confirmation(page)

    def _extract_rendered_confirmation(self, page) -> dict[str, object]:
        try:
            content = page.content().lower()
        except Exception:
            return {}
        if any(marker in content for marker in _CONFIRMATION_MARKERS):
            return {"message": "Application submitted", "url": getattr(page, "url", "")}
        return {}

    def _submission_failure(self, page) -> str | None:
        try:
            content = page.content().lower()
        except Exception:
            return None
        if "flagged as possible spam" in content or "couldn't submit your application" in content:
            return "submission_flagged"
        return None

    def _has_login_wall(self, page) -> bool:
        checker = getattr(page, "detect_login_wall", None)
        if callable(checker):
            return bool(checker())
        current_url = str(getattr(page, "url", "")).lower()
        return "login.ashbyhq.com" in current_url or "/login" in urlparse(current_url).path

    def _has_captcha_challenge(self, page) -> bool:
        checker = getattr(page, "detect_captcha", None)
        if callable(checker):
            return bool(checker())
        try:
            return bool(
                page.locator(
                    "iframe[title*='recaptcha challenge' i], iframe[title*='hcaptcha challenge' i], [data-hcaptcha-response]:not([style*='display: none'])"
                ).count()
            )
        except Exception:
            return False

    def _has_unsatisfied_invisible_recaptcha(self, page) -> bool:
        """Detect a failed invisible reCAPTCHA only after a submit attempt.

        Ashby commonly renders the invisible widget while the form is idle, so
        treating its presence as a blocker would prevent legitimate submits.
        An empty response after the submit wait, however, means the browser did
        not receive the required token and cannot prove submission occurred.
        """
        try:
            return bool(
                page.locator("textarea.g-recaptcha-response").evaluate_all(
                    "items => items.some(item => !String(item.value || '').trim())"
                )
            )
        except Exception:
            return False

    def _wait(self, page, milliseconds: int) -> None:
        waiter = getattr(page, "wait_for_timeout", None)
        if callable(waiter):
            waiter(milliseconds)

    def _wait_for_submission_state(self, page) -> None:
        try:
            page.wait_for_function(
                """
                () => {
                  const text = (document.body?.innerText || '').toLowerCase();
                  const markers = [
                    'application submitted',
                    'thank you for applying',
                    'your application has been submitted',
                    'we have received your application',
                    'application received',
                    'thank you for submitting',
                    'your application was successfully submitted',
                  ];
                  return markers.some((marker) => text.includes(marker)) ||
                    document.querySelector('[role="alert"], [data-testid*="error" i], .error-message') !== null;
                }
                """,
                timeout=10_000,
            )
        except Exception:
            pass

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


def _normalize_option(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _option_tokens(value: str) -> list[str]:
    normalized = value.lower()
    state_aliases = {
        "ca": "california",
        "ny": "new york",
        "nj": "new jersey",
        "dc": "district of columbia",
        "ma": "massachusetts",
        "tx": "texas",
        "wa": "washington",
    }
    tokens = re.findall(r"[a-z0-9]+", normalized)
    expanded: list[str] = []
    for token in tokens:
        expanded.extend(state_aliases.get(token, token).split())
    return expanded
