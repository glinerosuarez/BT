from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, urlparse

from job_hunter.apply.adapters.greenhouse import GreenhouseAdapter
from job_hunter.apply.resolver import ResolutionError
from job_hunter.apply.types import StepSnapshot, SubmitResult


class PhenomAdapter(GreenhouseAdapter):
    """Submit Phenom-hosted application forms using their rendered controls.

    Phenom is commonly deployed in front of another ATS, so detection relies on
    the public application URL shape and rendered Phenom markers rather than an
    employer-specific hostname.
    """

    adapter_name = "phenom"

    def is_phenom_target(self, url: str, page=None) -> bool:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        path = parsed.path.lower()
        if "/apply" in path and "jobSeqNo" in query and "stepname" in query:
            return True
        checker = getattr(page, "detect_phenom", None) if page is not None else None
        if callable(checker):
            return bool(checker())
        if page is None:
            return False
        try:
            content = page.content().lower()
        except Exception:
            return False
        return "phenom" in content and any(marker in content for marker in ("apply", "jobseqno", "personal information"))

    def extract_underlying_apply_url(self, page) -> str:
        """Return the ATS endpoint declared by a Phenom job-detail page.

        Some employers use Phenom for discovery but delegate the actual form to
        another ATS. Following the declared ``applyUrl`` preserves the portal's
        intended flow and lets the specific adapter own the form interaction.
        """
        extractor = getattr(page, "extract_phenom_apply_url", None)
        if callable(extractor):
            return str(extractor() or "").strip()
        try:
            content = page.content()
        except Exception:
            return ""
        match = re.search(r'"applyUrl"\s*:\s*"((?:\\.|[^"\\])*)"', content)
        if match is None:
            return ""
        try:
            candidate = json.loads(f'"{match.group(1)}"')
        except json.JSONDecodeError:
            return ""
        parsed = urlparse(str(candidate).strip())
        return str(candidate).strip() if parsed.scheme in {"http", "https"} and parsed.netloc else ""

    def submit(self, *, page, resolver, context) -> SubmitResult:
        """Process Phenom's step-based forms without treating Next as submit."""
        steps: list[StepSnapshot] = []
        for _ in range(12):
            self._wait_for_form_render(page)
            confirmation = self._extract_confirmation(page)
            if confirmation and not self._extract_fields(page):
                return SubmitResult(
                    status="submitted",
                    current_url=getattr(page, "url", ""),
                    confirmation_payload=confirmation,
                    steps=steps,
                    adapter_name=self.adapter_name,
                )
            if self._has_login_wall(page):
                return self._blocked("login_wall", page, steps)
            if self._has_captcha(page):
                return self._blocked("captcha", page, steps)

            # Ensure resume is uploaded on Step 1 if upload wrapper is present
            self._ensure_resume_uploaded(page, context, steps)

            blocker, _ = self._fill_required_fields(page=page, resolver=resolver, context=context, steps=steps)
            if blocker is not None:
                return blocker

            action = self._next_action(page)
            if action == "submit":
                if not self._click_button(page, ("Submit Application", "Submit application", "Submit")):
                    return self._blocked("submit_button_missing", page, steps)
                # Wait up to 12 seconds for submission / confirmation
                for _ in range(12):
                    self._wait(page, 1000)
                    if self._has_captcha(page):
                        return self._blocked("captcha", page, steps)
                    confirmation = self._extract_confirmation(page)
                    if confirmation:
                        return SubmitResult(
                            status="submitted",
                            current_url=getattr(page, "url", ""),
                            confirmation_payload=confirmation,
                            steps=steps,
                            adapter_name=self.adapter_name,
                        )
                return self._blocked("ambiguous_confirmation", page, steps)
            if action == "next":
                if not self._click_button(page, ("Next", "Continue", "Save and Continue", "Save & Continue")):
                    return self._blocked("navigation_button_missing", page, steps)
                steps.append(
                    StepSnapshot(
                        step_key=f"phenom:next:{len(steps)}",
                        step_label="Advance application step",
                        status="completed",
                    )
                )
                self._wait(page, 5000)
                continue
            return self._blocked("unsupported_widget", page, steps)
        return self._blocked("navigation_limit_exceeded", page, steps)

    def _wait_for_form_render(self, page) -> None:
        """Wait for Phenom's React bundle to render the form controls."""
        if hasattr(page, "wait_for_selector"):
            try:
                page.wait_for_selector("button#next, button.btn-submit, input, select", timeout=10000)
            except Exception:
                pass
        self._wait(page, 2000)

    def _ensure_resume_uploaded(self, page, context, steps: list[StepSnapshot]) -> None:
        if not hasattr(page, "locator") or not getattr(context, "resume_pdf_path", None):
            return
        try:
            wrapper = page.locator(".resume-upload-wrapper input[type='file']")
            if wrapper.count() > 0:
                uploaded = page.locator(".resume-file-name, .uploaded-file-name, [data-automation-id*='uploaded' i]")
                if uploaded.count() == 0 or not uploaded.first.is_visible():
                    wrapper.first.set_input_files(context.resume_pdf_path)
                    self._wait(page, 5000)
                    steps.append(
                        StepSnapshot(
                            step_key="upload:resume",
                            step_label="Upload document",
                            status="completed",
                            field_name="resume",
                            field_type="file",
                            question_text="Resume/CV",
                            answer_source="artifact",
                            answer_value=context.resume_pdf_path,
                        )
                    )
        except Exception:
            pass

    def _fill_required_fields(
        self,
        *,
        page,
        resolver,
        context,
        steps: list[StepSnapshot],
    ) -> tuple[SubmitResult | None, int]:
        filled_count = 0
        if hasattr(page, "locator"):
            try:
                curr_box = page.locator('[id="experienceData[0].fromTo.currentlyWorkHere"]')
                if curr_box.count() > 0 and not curr_box.first.is_checked():
                    end_input = page.locator('[id="experienceData[0].fromTo.endDate"]')
                    if end_input.count() > 0 and not end_input.first.input_value().strip():
                        curr_box.first.check(force=True)
                        self._wait(page, 500)
            except Exception:
                pass
        for field in self._extract_fields(page):
            question_text = str(field.get("question_text") or field.get("label") or field.get("field_name") or "").strip()
            field_name = str(field.get("field_name") or "")
            field_type = str(field.get("field_type") or "text")
            required = bool(field.get("required", True))
            current_value = self._normalized_current_value(
                field_type=field_type,
                current_value=str(field.get("current_value") or ""),
            )
            if current_value:
                continue
            if field_type == "file":
                upload_path = self._artifact_for_field(
                    context=context,
                    question_text=question_text,
                    field_name=field_name,
                )
                if not upload_path:
                    if required:
                        return (
                            self._blocked(
                                "unsupported_required_document",
                                page,
                                steps,
                                field_name=field_name,
                                field_type=field_type,
                                question_text=question_text,
                                details={"message": "A job-specific document is required and no safe upload artifact is available."},
                            ),
                            filled_count,
                        )
                    continue
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
                if required:
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
                continue
            try:
                self._set_field(page, field, resolution.answer)
            except RuntimeError as exc:
                if required:
                    return (
                        self._blocked(
                            "field_interaction_failed",
                            page,
                            steps,
                            field_name=field_name,
                            field_type=field_type,
                            question_text=question_text,
                            details={"message": str(exc)},
                        ),
                        filled_count,
                    )
                continue
            steps.append(
                StepSnapshot(
                    step_key=f"field:{field_name or question_text}",
                    step_label="Fill form field",
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

    def _next_action(self, page) -> str:
        submitter = getattr(page, "submit_application", None)
        if callable(submitter):
            return "submit"
        if hasattr(page, "locator"):
            for selector in ("button.btn-submit", "button#submit", "button.primary-button.btn-submit"):
                try:
                    btn = page.locator(selector)
                    if btn.count() > 0 and btn.first.is_visible():
                        return "submit"
                except Exception:
                    pass
        for name in ("Submit Application", "Submit application", "Submit"):
            try:
                if page.get_by_role("button", name=name, exact=True).count() > 0:
                    return "submit"
            except Exception:
                continue
        if hasattr(page, "locator"):
            for selector in ("button#next", "button.btn-navigate"):
                try:
                    btn = page.locator(selector)
                    if btn.count() > 0 and btn.first.is_visible():
                        return "next"
                except Exception:
                    pass
        for name in ("Next", "Continue", "Save and Continue", "Save & Continue"):
            try:
                if page.get_by_role("button", name=name, exact=True).count() > 0:
                    return "next"
            except Exception:
                continue
        return ""

    def _click_button(self, page, names: tuple[str, ...]) -> bool:
        submitter = getattr(page, "submit_application", None)
        if callable(submitter) and any(name.startswith("Submit") for name in names):
            submitter()
            return True
        if hasattr(page, "locator"):
            if any(name.startswith("Submit") for name in names):
                for selector in ("button.btn-submit", "button#submit", "button.primary-button.btn-submit"):
                    try:
                        btn = page.locator(selector)
                        if btn.count() > 0 and btn.first.is_visible():
                            btn.first.scroll_into_view_if_needed()
                            btn.first.click()
                            return True
                    except Exception:
                        pass
            elif any(name.startswith("Next") for name in names):
                for selector in ("button#next", "button.btn-navigate"):
                    try:
                        btn = page.locator(selector)
                        if btn.count() > 0 and btn.first.is_visible():
                            btn.first.scroll_into_view_if_needed()
                            btn.first.click()
                            return True
                    except Exception:
                        pass
        for name in names:
            try:
                button = page.get_by_role("button", name=name, exact=True)
                if button.count() > 0:
                    if hasattr(button.last, "scroll_into_view_if_needed"):
                        button.last.scroll_into_view_if_needed()
                    button.last.click()
                    return True
            except Exception:
                continue
        return False

    def _has_login_wall(self, page) -> bool:
        if super()._has_login_wall(page):
            return True
        current_url = str(getattr(page, "url", "")).lower()
        if "/login" in urlparse(current_url).path:
            return True
        try:
            text = page.locator("body").inner_text(timeout=2000).lower()
        except Exception:
            return False
        return "sign in" in text and any(marker in text for marker in ("password", "create an account", "log in"))

    def _has_captcha(self, page) -> bool:
        if super()._has_captcha(page):
            return True
        if not hasattr(page, "locator"):
            return False
        try:
            return bool(
                page.locator(
                    "iframe[title*='captcha' i], iframe[src*='recaptcha' i], iframe[src*='hcaptcha' i], [data-sitekey]"
                ).count()
            )
        except Exception:
            return False

    def _extract_confirmation(self, page) -> dict[str, object]:
        super_conf = super()._extract_confirmation(page)
        if super_conf:
            return super_conf
        url = str(getattr(page, "url", "")).lower()
        if any(marker in url for marker in ("stepname=thankyou", "stepname=confirmation", "step=thankyou", "step=confirmation")):
            return {"message": "Application submitted", "url": url}
        if hasattr(page, "locator"):
            try:
                text = page.locator("body").inner_text(timeout=2000).lower()
                if any(m in text for m in ("thank you for applying", "application submitted", "received your application", "thank you for your interest")):
                    return {"message": "Application submitted", "url": url}
            except Exception:
                pass
        return {}

    @staticmethod
    def _wait(page, milliseconds: int) -> None:
        waiter = getattr(page, "wait_for_timeout", None)
        if callable(waiter):
            waiter(milliseconds)

    def _extract_fields(self, page) -> list[dict[str, object]]:
        extractor = getattr(page, "extract_phenom_fields", None)
        if callable(extractor):
            return list(extractor())
        generic_extractor = getattr(page, "extract_fields", None)
        if callable(generic_extractor):
            return list(generic_extractor())
        if not hasattr(page, "evaluate"):
            return []
        return page.evaluate(
            """
            () => {
              const visible = (element) => {
                const type = (element.getAttribute('type') || '').toLowerCase();
                if (type === 'file') return !element.disabled;
                const style = window.getComputedStyle(element);
                const rect = element.getBoundingClientRect();
                return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
              };
              const fields = [];
              let index = 0;
              const selectorFor = (element) => {
                const id = element.id || '';
                if (id) return `#${CSS.escape(id)}`;
                index += 1;
                element.setAttribute('data-jobhunter-phenom-field', String(index));
                return `[data-jobhunter-phenom-field="${index}"]`;
              };
              const labelFor = (element, scope) => {
                const id = element.id || '';
                const type = (element.getAttribute('type') || '').toLowerCase();
                if (type === 'file') {
                  const uploadText = (element.closest('.resume-upload-wrapper, [class*="upload" i], [class*="file" i]')?.textContent || '').trim();
                  if (/resume/i.test(uploadText)) return 'Resume/CV';
                  if (uploadText) return uploadText.slice(0, 50);
                }
                const name = element.getAttribute('name') || id || '';
                if (name.includes('currentlyWorkHere')) return 'I currently work here';
                if (name.includes('currentEmployer')) return 'I am currently employed';

                const linked = id ? document.querySelector(`label[for="${CSS.escape(id)}"]`) : null;
                const parent = element.parentElement;
                const adjacentLabel = (element.nextElementSibling && element.nextElementSibling.tagName === 'LABEL')
                  ? element.nextElementSibling.textContent
                  : (parent ? parent.querySelector('label, span.control-label')?.textContent : null);
                const labelledBy = (element.getAttribute('aria-labelledby') || '')
                  .split(/\\s+/)
                  .map((value) => document.getElementById(value)?.textContent || '')
                  .join(' ');
                const direct = linked?.textContent || labelledBy || element.closest('label')?.textContent || adjacentLabel;
                if (direct && direct.trim()) return direct.trim();
                return (scope?.querySelector('legend, label, [data-automation-id*="label" i]')?.textContent ||
                  element.getAttribute('aria-label') || '').trim();
              };
              const isRequired = (element, scope) => {
                if (element.required || element.getAttribute('aria-required') === 'true') return true;
                const type = (element.getAttribute('type') || '').toLowerCase();
                const name = element.getAttribute('name') || element.id || '';
                if (name.includes('currentlyWorkHere') || name.includes('currentEmployer')) return false;
                if (type === 'checkbox') {
                  const directText = labelFor(element, scope);
                  return /\\*/.test(directText) && /agree|consent|terms|certify/i.test(directText);
                }
                return Boolean(scope?.getAttribute('aria-required') === 'true' || /\\*/.test(labelFor(element, scope)));
              };
              const groups = new Set();
              for (const element of Array.from(document.querySelectorAll('input, textarea, select, [role="combobox"]'))) {
                if (!visible(element) || element.disabled || element.getAttribute('aria-hidden') === 'true') continue;
                const tag = element.tagName.toLowerCase();
                const type = (element.getAttribute('type') || '').toLowerCase();
                if (type === 'hidden' || type === 'submit' || type === 'button' || type === 'reset') continue;
                const scope = element.closest('fieldset, [role="group"], .form-group, [class*="field" i], [class*="question" i]');
                if (type === 'radio' || type === 'checkbox') {
                  const groupKey = `${type}:${element.name || selectorFor(scope || element)}`;
                  if (groups.has(groupKey)) continue;
                  groups.add(groupKey);
                  const optionElements = Array.from((scope || document).querySelectorAll(`input[type="${type}"]`))
                    .filter((candidate) => visible(candidate) && (candidate.name === element.name || (!element.name && candidate === element)));
                  const options = optionElements.map((candidate) => ({
                    selector: selectorFor(candidate),
                    value: candidate.value || '',
                    label: labelFor(candidate, scope),
                    checked: candidate.checked,
                  }));
                  fields.push({
                    selector: options[0]?.selector || '',
                    field_name: element.name || element.id || '',
                    field_type: type === 'radio' ? 'radio-group' : 'checkbox-group',
                    question_text: labelFor(element, scope),
                    required: isRequired(element, scope),
                    current_value: options.filter((option) => option.checked).map((option) => option.label || option.value).join(', '),
                    options,
                  });
                  continue;
                }
                const isTypeahead = element.getAttribute('data-attribute') === 'asyncTypeAhead' ||
                  (element.className || '').includes('rbt-input') ||
                  element.getAttribute('role') === 'combobox';
                const fieldType = type === 'file' ? 'file' :
                  (tag === 'select' ? 'select-one' : 'text');
                const controlKind = tag === 'select' ? 'native-select' :
                  (isTypeahead ? 'asyncTypeAhead' : 'text');
                fields.push({
                  selector: selectorFor(element),
                  field_name: element.getAttribute('name') || element.id || '',
                  field_type: fieldType,
                  control_kind: controlKind,
                  question_text: labelFor(element, scope),
                  required: isRequired(element, scope),
                  current_value: (element.value || element.getAttribute('data-value') || '').trim(),
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
        field_type = str(field.get("field_type") or "text")
        if field_type == "select-one":
            self._set_select(page, field, value)
            return
        if field_type == "file":
            if hasattr(page, "locator"):
                page.locator(str(field.get("selector") or "")).set_input_files(value)
                self._wait(page, 4000)
            return
        if field.get("control_kind") == "asyncTypeAhead" and hasattr(page, "locator"):
            self._set_typeahead(page, field, value)
            return
        if field_type in {"radio-group", "checkbox-group"}:
            self._set_choice(page, field, value)
            return
        if field_type == "checkbox":
            selector = str(field.get("selector") or "")
            desired = value.strip().lower() in {"1", "true", "yes", "on"}
            if hasattr(page, "locator"):
                loc = page.locator(selector)
                if desired:
                    loc.check(force=True)
                else:
                    loc.uncheck(force=True)
            elif hasattr(page, "click"):
                page.click(selector)
            self._wait(page, 200)
            return
        # Text input / textarea
        selector = str(field.get("selector") or "")
        if hasattr(page, "locator"):
            try:
                locator = page.locator(selector)
                locator.scroll_into_view_if_needed()
                locator.click()
                locator.fill("")
                locator.press_sequentially(value, delay=15)
                locator.press("Tab")
                self._wait(page, 100)
                return
            except Exception:
                pass
        super()._set_field(page, field, value)

    def _set_typeahead(self, page, field: dict[str, object], value: str) -> None:
        selector = str(field.get("selector") or "")
        locator = page.locator(selector)
        locator.scroll_into_view_if_needed()
        locator.click()
        locator.fill("")
        locator.press_sequentially(value, delay=25)
        self._wait(page, 1500)
        dropdown = page.locator(".rbt-menu .dropdown-item")
        if dropdown.count() > 0:
            for idx in range(dropdown.count()):
                item = dropdown.nth(idx)
                if item.is_visible() and self._choice_matches(value, item.inner_text()):
                    item.click()
                    self._wait(page, 500)
                    return
            dropdown.first.click()
            self._wait(page, 500)

    def _set_choice(self, page, field: dict[str, object], value: str) -> None:
        normalized = self._normalize_choice(value)
        options = list(field.get("options") or [])
        if not options:
            return
        for option in options:
            option_label = str(option.get("label") or option.get("value") or "").strip()
            if self._choice_matches(value, option_label):
                if hasattr(page, "locator"):
                    page.locator(str(option.get("selector") or "")).check(force=True)
                elif hasattr(page, "click"):
                    page.click(str(option.get("selector") or ""))
                self._wait(page, 300)
                return
        for option in options:
            option_label = str(option.get("label") or option.get("value") or "").strip().lower()
            if normalized in {"true", "1", "yes", "on"} and ("yes" in option_label or "agree" in option_label or "consent" in option_label):
                if hasattr(page, "locator"):
                    page.locator(str(option.get("selector") or "")).check(force=True)
                elif hasattr(page, "click"):
                    page.click(str(option.get("selector") or ""))
                self._wait(page, 300)
                return
            if normalized in {"false", "0", "no", "off"} and "no" in option_label:
                if hasattr(page, "locator"):
                    page.locator(str(option.get("selector") or "")).check(force=True)
                elif hasattr(page, "click"):
                    page.click(str(option.get("selector") or ""))
                self._wait(page, 300)
                return
        if len(options) == 1 and normalized in {"true", "1", "yes", "on"}:
            if hasattr(page, "locator"):
                page.locator(str(options[0].get("selector") or "")).check(force=True)
            elif hasattr(page, "click"):
                page.click(str(options[0].get("selector") or ""))
            self._wait(page, 300)
            return
        raise RuntimeError(f"Unsupported choice-group value '{value}' for {field.get('field_name') or field.get('question_text')}")

    def _set_select(self, page, field: dict[str, object], value: str) -> None:
        selector = str(field.get("selector") or "")
        locator = page.locator(selector) if hasattr(page, "locator") else None
        normalized = self._normalize_choice(value)
        if normalized in {"yes", "true", "1"}:
            value = "Yes"
        elif normalized in {"no", "false", "0"}:
            value = "No"
        if field.get("control_kind") == "native-select" and locator is not None:
            try:
                locator.select_option(label=value)
                return
            except Exception:
                pass
            try:
                locator.select_option(value=value)
                return
            except Exception:
                pass
            options = locator.locator("option")
            count = options.count()
            for index in range(count):
                option = options.nth(index)
                text = option.inner_text()
                val_attr = option.get_attribute("value") or ""
                if self._choice_matches(value, text) or self._choice_matches(value, val_attr):
                    locator.select_option(index=index)
                    return
            for index in range(count):
                option = options.nth(index)
                text = option.inner_text().lower()
                val_attr = (option.get_attribute("value") or "").lower()
                if normalized in text or normalized in val_attr:
                    locator.select_option(index=index)
                    return
            raise RuntimeError(f"No matching native select option for '{value}'")

        if locator is not None:
            locator.click()
            try:
                locator.fill(value)
            except Exception:
                pass
            self._wait(page, 400)
            for option_selector in ("[role='option']", "li[role='option']", "[data-automation-id*='option' i]"):
                options = page.locator(option_selector)
                for index in range(options.count()):
                    option = options.nth(index)
                    if option.is_visible() and self._choice_matches(value, option.inner_text()):
                        option.click()
                        self._wait(page, 250)
                        return
        raise RuntimeError(f"No matching select option for '{value}'")

    @staticmethod
    def _normalize_choice(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()

    @classmethod
    def _choice_matches(cls, expected: str, actual: str) -> bool:
        expected_normalized = cls._normalize_choice(expected)
        actual_normalized = cls._normalize_choice(actual)
        if not expected_normalized or not actual_normalized:
            return False
        if expected_normalized == actual_normalized:
            return True
        synonyms = [
            {"male", "man"},
            {"female", "woman"},
            {"yes", "true", "1"},
            {"no", "false", "0"},
            {"united states", "usa", "us", "united states of america"},
            {"mobile", "home mobile", "cell", "cell phone"},
            {"master s degree", "master s or post graduate degree incomplete", "master s or postgraduate degree"},
        ]
        for s in synonyms:
            if expected_normalized in s and actual_normalized in s:
                return True
        if "master" in expected_normalized and "master" in actual_normalized and "incomplete" in actual_normalized:
            return True
        return bool(
            actual_normalized.startswith(f"{expected_normalized} ")
            or expected_normalized in actual_normalized
            or actual_normalized in expected_normalized
        )
