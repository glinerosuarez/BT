from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, urlparse

from job_hunter.apply.adapters.greenhouse import GreenhouseAdapter
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
        for _ in range(8):
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

            blocker, _ = self._fill_required_fields(page=page, resolver=resolver, context=context, steps=steps)
            if blocker is not None:
                return blocker

            action = self._next_action(page)
            if action == "submit":
                if not self._click_button(page, ("Submit Application", "Submit application", "Submit")):
                    return self._blocked("submit_button_missing", page, steps)
                self._wait(page, 3000)
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
                self._wait(page, 1000)
                continue
            return self._blocked("unsupported_widget", page, steps)
        return self._blocked("navigation_limit_exceeded", page, steps)

    def _next_action(self, page) -> str:
        submitter = getattr(page, "submit_application", None)
        if callable(submitter):
            return "submit"
        for name in ("Submit Application", "Submit application", "Submit"):
            try:
                if page.get_by_role("button", name=name, exact=True).count() > 0:
                    return "submit"
            except Exception:
                continue
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
        for name in names:
            try:
                button = page.get_by_role("button", name=name, exact=True)
                if button.count() > 0:
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
        try:
            return bool(
                page.locator(
                    "iframe[title*='captcha' i], iframe[src*='recaptcha' i], iframe[src*='hcaptcha' i], [data-sitekey]"
                ).count()
            )
        except Exception:
            return False

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
        return page.evaluate(
            """
            () => {
              const visible = (element) => {
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
                const linked = id ? document.querySelector(`label[for="${CSS.escape(id)}"]`) : null;
                const labelledBy = (element.getAttribute('aria-labelledby') || '')
                  .split(/\\s+/)
                  .map((value) => document.getElementById(value)?.textContent || '')
                  .join(' ');
                return (linked?.textContent || labelledBy || element.closest('label')?.textContent ||
                  scope?.querySelector('legend, label, [data-automation-id*="label" i]')?.textContent ||
                  element.getAttribute('aria-label') || '').trim();
              };
              const isRequired = (element, scope) => Boolean(
                element.required || element.getAttribute('aria-required') === 'true' ||
                scope?.getAttribute('aria-required') === 'true' || /\\*/.test(labelFor(element, scope))
              );
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
                    .filter((candidate) => visible(candidate) && candidate.name === element.name);
                  const options = optionElements.map((candidate) => ({
                    selector: selectorFor(candidate),
                    value: candidate.value || '',
                    label: labelFor(candidate, scope),
                    checked: candidate.checked,
                  }));
                  fields.push({
                    selector: options[0]?.selector || '',
                    field_name: element.name || '',
                    field_type: type === 'radio' ? 'radio-group' : 'checkbox-group',
                    question_text: labelFor(element, scope),
                    required: isRequired(element, scope),
                    current_value: options.filter((option) => option.checked).map((option) => option.label || option.value).join(', '),
                    options,
                  });
                  continue;
                }
                const fieldType = type === 'file' ? 'file' :
                  (tag === 'select' || element.getAttribute('role') === 'combobox' ? 'select-one' : 'text');
                fields.push({
                  selector: selectorFor(element),
                  field_name: element.getAttribute('name') || element.id || '',
                  field_type: fieldType,
                  control_kind: tag === 'select' ? 'native-select' : (element.getAttribute('role') === 'combobox' ? 'combobox' : 'text'),
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
            page.locator(str(field.get("selector") or "")).set_input_files(value)
            page.wait_for_timeout(750)
            return
        super()._set_field(page, field, value)

    def _set_select(self, page, field: dict[str, object], value: str) -> None:
        selector = str(field.get("selector") or "")
        locator = page.locator(selector)
        normalized = self._normalize_choice(value)
        if normalized in {"yes", "true", "1"}:
            value = "Yes"
        elif normalized in {"no", "false", "0"}:
            value = "No"
        if field.get("control_kind") == "native-select":
            try:
                locator.select_option(label=value)
                return
            except Exception:
                options = locator.locator("option")
                for index in range(options.count()):
                    option = options.nth(index)
                    if self._choice_matches(value, option.inner_text()):
                        locator.select_option(index=index)
                        return
                raise RuntimeError(f"No matching native select option for '{value}'")

        locator.click()
        try:
            locator.fill(value)
        except Exception:
            pass
        page.wait_for_timeout(400)
        for option_selector in ("[role='option']", "li[role='option']", "[data-automation-id*='option' i]"):
            options = page.locator(option_selector)
            for index in range(options.count()):
                option = options.nth(index)
                if option.is_visible() and self._choice_matches(value, option.inner_text()):
                    option.click()
                    page.wait_for_timeout(250)
                    return
        raise RuntimeError(f"No matching select option for '{value}'")

    @staticmethod
    def _normalize_choice(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()

    @classmethod
    def _choice_matches(cls, expected: str, actual: str) -> bool:
        expected_normalized = cls._normalize_choice(expected)
        actual_normalized = cls._normalize_choice(actual)
        return bool(expected_normalized) and (
            expected_normalized == actual_normalized
            or actual_normalized.startswith(f"{expected_normalized} ")
            or expected_normalized in actual_normalized
        )
