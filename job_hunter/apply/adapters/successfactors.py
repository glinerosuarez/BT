from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

from job_hunter.apply.adapters.base import AdapterContext
from job_hunter.apply.resolver import AnswerResolver, ResolutionError
from job_hunter.apply.types import Blocker, StepSnapshot, SubmitResult

_SF_HOST_PATTERNS = (
    re.compile(r"career\d*\.successfactors\.(?:com|eu)$", re.IGNORECASE),
    re.compile(r".*\.successfactors\.(?:com|eu)$", re.IGNORECASE),
    re.compile(r".*\.jobs2web\.com$", re.IGNORECASE),
)

_CONFIRMATION_MARKERS = (
    "application submitted",
    "thank you for applying",
    "your application has been submitted",
    "your application has been received",
    "we have received your application",
    "application is complete",
    "successfully submitted",
    "you have already applied for this job",
    "you have already applied",
    "already applied",
)

_LOGIN_MARKERS = (
    "career opportunities: sign in",
    "already have an account",
    "enter your email address and password",
    "sign in to apply",
    "log in to apply",
    "create an account to apply",
    "not a registered user yet",
)

_EXTRACT_FIELDS_JS = r"""
() => {
  const fields = [];
  let counter = 0;

  function isVisible(el) {
    if (!el) return false;
    const style = window.getComputedStyle(el);
    return style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0' && el.offsetParent !== null;
  }

  function getLabel(el) {
    if (!el) return '';
    if (el.id) {
      const lbl = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (lbl) return lbl.textContent.trim();
    }
    if (el.getAttribute('aria-label')) return el.getAttribute('aria-label').trim();
    const labelledby = el.getAttribute('aria-labelledby');
    if (labelledby) {
      const parts = labelledby.split(/\s+/).map(id => {
        const target = document.getElementById(id);
        return target ? target.textContent.trim() : '';
      }).filter(Boolean);
      if (parts.length) return parts.join(' ');
    }
    const parentLabel = el.closest('label');
    if (parentLabel) {
      const clone = parentLabel.cloneNode(true);
      Array.from(clone.querySelectorAll('input, select, textarea, button')).forEach(n => n.remove());
      const text = clone.textContent.trim();
      if (text) return text;
    }
    const container = el.closest('.form-group, .form-row, .sapUiFormElement, .sapMInputBase, tr, .field-container, .rcm-field, div[class*="field"], div[class*="row"]');
    if (container) {
      const lbl = container.querySelector('label, .control-label, .sapMLabel, .fieldLabel, th, .label');
      if (lbl && isVisible(lbl)) return lbl.textContent.trim();
    }
    return el.getAttribute('placeholder') || el.getAttribute('title') || el.getAttribute('name') || '';
  }

  function isRequired(el) {
    if (!el) return false;
    if (el.required || el.getAttribute('aria-required') === 'true') return true;
    const container = el.closest('.form-group, .form-row, .sapUiFormElement, tr, div[class*="field"]');
    if (container) {
      const text = container.textContent;
      if (/\*\s*indicates required|\*/.test(text) && container.querySelector('.required, .sapMLabelRequired, [aria-required="true"], span.requiredAsterisk')) {
        return true;
      }
    }
    const label = getLabel(el);
    return /\*|\(required\)/i.test(label);
  }

  // --- 1. File Inputs (Resume, Cover Letter) ---
  const fileInputs = Array.from(document.querySelectorAll('input[type="file"]'));
  for (const el of fileInputs) {
    const idx = counter++;
    el.setAttribute('data-jh-idx', String(idx));
    fields.push({
      selector: `input[type="file"][data-jh-idx="${idx}"]`,
      field_name: el.getAttribute('name') || el.getAttribute('id') || 'resume',
      field_type: 'file',
      label: getLabel(el) || 'Resume / Document Attachment',
      required: isRequired(el) || /resume|cv/i.test(getLabel(el)),
      current_value: '',
      options: [],
    });
  }

  // --- 2. Standard Select / Dropdown Elements ---
  const selects = Array.from(document.querySelectorAll('select')).filter(isVisible);
  for (const el of selects) {
    const idx = counter++;
    el.setAttribute('data-jh-idx', String(idx));
    const opts = Array.from(el.options).map(o => ({
      value: o.value,
      label: o.text.trim(),
    })).filter(o => o.value !== '' && o.label !== '' && !/select|choose|please/i.test(o.label));
    fields.push({
      selector: `select[data-jh-idx="${idx}"]`,
      field_name: el.getAttribute('name') || el.getAttribute('id') || '',
      field_type: 'select-one',
      label: getLabel(el),
      required: isRequired(el),
      current_value: el.selectedOptions[0]?.text?.trim() || '',
      options: opts,
    });
  }

  // --- 3. Text, Email, Tel, Number Inputs & Textareas ---
  const textInputs = Array.from(document.querySelectorAll(
    'input[type="text"], input[type="email"], input[type="tel"], input[type="number"], input[type="url"], input:not([type]), textarea'
  )).filter(el => isVisible(el) && !el.disabled && !el.readOnly);

  for (const el of textInputs) {
    if (el.getAttribute('role') === 'combobox') continue;
    const idx = counter++;
    el.setAttribute('data-jh-idx', String(idx));
    const type = el.tagName.toLowerCase() === 'textarea' ? 'textarea' : 'text';
    fields.push({
      selector: `${el.tagName.toLowerCase()}[data-jh-idx="${idx}"]`,
      field_name: el.getAttribute('name') || el.getAttribute('id') || '',
      field_type: type,
      label: getLabel(el),
      required: isRequired(el),
      current_value: el.value || '',
      options: [],
    });
  }

  // --- 4. Radio Groups ---
  const radioGroups = new Map();
  const radios = Array.from(document.querySelectorAll('input[type="radio"]')).filter(isVisible);
  for (const r of radios) {
    const name = r.getAttribute('name') || 'unnamed_radio';
    if (!radioGroups.has(name)) radioGroups.set(name, []);
    radioGroups.get(name).push(r);
  }

  for (const [name, group] of radioGroups) {
    const idx = counter++;
    group[0].setAttribute('data-jh-radio-group', String(idx));
    const parentContainer = group[0].closest('.form-group, fieldset, .sapUiFormElement, tr, div');
    const groupLabel = parentContainer ? (parentContainer.querySelector('legend, label, .fieldLabel, th')?.textContent?.trim() || getLabel(group[0])) : getLabel(group[0]);
    const options = group.map((r, rIdx) => {
      r.setAttribute('data-jh-idx', `${idx}_${rIdx}`);
      const lbl = getLabel(r) || r.value || '';
      return {
        selector: `input[data-jh-idx="${idx}_${rIdx}"]`,
        label: lbl,
        value: r.value,
        checked: r.checked,
      };
    });
    const checkedOpt = options.find(o => o.checked);
    fields.push({
      selector: `input[data-jh-radio-group="${idx}"]`,
      field_name: name,
      field_type: 'radio-group',
      label: groupLabel,
      required: group.some(isRequired),
      current_value: checkedOpt ? checkedOpt.label : '',
      options: options,
    });
  }

  // --- 5. Checkbox Elements ---
  const checkboxes = Array.from(document.querySelectorAll('input[type="checkbox"]')).filter(isVisible);
  for (const cb of checkboxes) {
    const idx = counter++;
    cb.setAttribute('data-jh-idx', String(idx));
    fields.push({
      selector: `input[type="checkbox"][data-jh-idx="${idx}"]`,
      field_name: cb.getAttribute('name') || cb.getAttribute('id') || '',
      field_type: 'checkbox',
      label: getLabel(cb),
      required: isRequired(cb),
      current_value: cb.checked ? 'true' : 'false',
      options: [],
    });
  }

  return fields;
}
"""


class SuccessFactorsAdapter:
    """ApplyAdapter implementation for SAP SuccessFactors & jobs2web career portals."""

    adapter_name = "successfactors"

    # ------------------------------------------------------------------
    # Target detection
    # ------------------------------------------------------------------

    def is_successfactors_target(self, url: str, page=None) -> bool:
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        path = parsed.path.lower()
        query = parsed.query.lower()

        # Host-based detection
        for pattern in _SF_HOST_PATTERNS:
            if pattern.search(host):
                return True

        # URL path/query-based detection (company-branded vanity domains)
        if "/talentcommunity/apply/" in path or "/talentcommunity/" in path:
            return True
        if "successfactors" in query or "career_company=" in query or ("company=" in query and "careers" in path):
            return True

        # DOM-based detection if page context is available
        if page is not None:
            checker = getattr(page, "detect_successfactors", None)
            if callable(checker):
                return bool(checker())
            try:
                content = page.content().lower()
                if any(marker in content for marker in (
                    "sap.sf.",
                    "sap-ui-core.js",
                    "successfactors",
                    "rcmcareer",
                    "jobs2web",
                    "dialogapplybtn",
                )):
                    return True
            except Exception:
                pass

        return False

    # ------------------------------------------------------------------
    # Main submission loop
    # ------------------------------------------------------------------

    def submit(
        self,
        *,
        page,
        resolver: AnswerResolver,
        context: AdapterContext,
    ) -> SubmitResult:
        steps: list[StepSnapshot] = []

        self._wait_for_spa_ready(page)

        for attempt in range(15):
            current_url = getattr(page, "url", "")

            # 1. Check for confirmation
            confirmation = self._extract_confirmation(page)
            if confirmation:
                return SubmitResult(
                    status="submitted",
                    current_url=current_url,
                    confirmation_payload=confirmation,
                    steps=steps,
                    adapter_name=self.adapter_name,
                )

            # 2. Check for login / account requirement
            if self._has_login_wall(page):
                return self._blocked(
                    "candidate_account_bootstrap_required",
                    page,
                    steps,
                    question_text="SAP SuccessFactors requires sign-in or account creation before automation can continue.",
                    details={
                        "provider": "successfactors",
                        "current_url": current_url,
                        "hint": "Sign in or create an account on SuccessFactors, then resume.",
                    },
                )

            # 3. Advance from initial job requisition landing page if Apply button present
            if self._advance_from_job_landing_page(page):
                self._wait_for_spa_ready(page)
                continue

            # 4. Extract and fill form fields on the current page
            blocked_result, filled_count = self._fill_current_step(
                page=page, resolver=resolver, context=context, steps=steps
            )
            if blocked_result is not None:
                return blocked_result

            # 5. Check if we reached confirmation after filling
            confirmation = self._extract_confirmation(page)
            if confirmation:
                return SubmitResult(
                    status="submitted",
                    current_url=getattr(page, "url", ""),
                    confirmation_payload=confirmation,
                    steps=steps,
                    adapter_name=self.adapter_name,
                )

            # 6. Try clicking Submit / Apply
            if self._try_submit(page):
                self._wait_for_spa_ready(page)
                confirmation = self._extract_confirmation(page)
                if confirmation:
                    return SubmitResult(
                        status="submitted",
                        current_url=getattr(page, "url", ""),
                        confirmation_payload=confirmation,
                        steps=steps,
                        adapter_name=self.adapter_name,
                    )
                # Check for validation errors after submitting
                validation_errors = self._extract_validation_errors(page)
                if validation_errors:
                    return self._blocked(
                        "form_validation_failed",
                        page,
                        steps,
                        details={"errors": validation_errors},
                    )
                continue

            # 7. Try clicking Next / Continue to advance multi-step wizard
            if self._try_click_next(page):
                self._wait_for_spa_ready(page)
                continue

            # If nothing was filled and no navigation occurred, check confirmation again
            if filled_count == 0:
                confirmation = self._extract_confirmation(page)
                if confirmation:
                    return SubmitResult(
                        status="submitted",
                        current_url=getattr(page, "url", ""),
                        confirmation_payload=confirmation,
                        steps=steps,
                        adapter_name=self.adapter_name,
                    )
                return self._blocked(
                    "stalled_application",
                    page,
                    steps,
                    question_text="No form fields or navigation buttons were actionable on the current SuccessFactors page.",
                    details={"current_url": current_url},
                )

        return self._blocked(
            "max_attempts_exceeded",
            page,
            steps,
            details={"attempts": 15},
        )

    # ------------------------------------------------------------------
    # Step filling
    # ------------------------------------------------------------------

    def _fill_current_step(
        self,
        *,
        page,
        resolver: AnswerResolver,
        context: AdapterContext,
        steps: list[StepSnapshot],
    ) -> tuple[SubmitResult | None, int]:
        fields = self._extract_fields(page)
        filled = 0

        for field in fields:
            field_name = str(field.get("field_name") or "").strip()
            field_type = str(field.get("field_type") or "text").strip()
            question_text = str(field.get("label") or "").strip()
            required = bool(field.get("required"))
            current_value = str(field.get("current_value") or "").strip()

            # Handle file attachments
            if field_type == "file":
                artifact = None
                normalized_label = f"{field_name} {question_text}".lower()
                if "cover" in normalized_label or "letter" in normalized_label:
                    artifact = context.cover_letter_pdf_path
                else:
                    artifact = context.resume_pdf_path

                if not artifact:
                    if required:
                        return (
                            self._blocked(
                                "unsupported_required_document",
                                page,
                                steps,
                                field_name=field_name,
                                field_type=field_type,
                                question_text=question_text,
                            ),
                            filled,
                        )
                    continue

                self._set_field(page, field, artifact)
                steps.append(
                    StepSnapshot(
                        step_key=f"upload:{field_name or question_text}",
                        step_label="Upload document",
                        status="completed",
                        field_name=field_name,
                        field_type=field_type,
                        question_text=question_text,
                        answer_source="artifact",
                        answer_value=artifact,
                    )
                )
                filled += 1
                continue

            if not required and not question_text and not field_name:
                continue

            try:
                resolution = resolver.resolve_for_portal(
                    portal=self.adapter_name,
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
                        filled,
                    )
                continue

            if current_value and current_value == resolution.answer:
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
                        filled,
                    )
                continue

            steps.append(
                StepSnapshot(
                    step_key=f"field:{field_name or question_text}",
                    step_label="Fill field",
                    status="completed",
                    field_name=field_name,
                    field_type=field_type,
                    question_text=question_text,
                    answer_source=resolution.source,
                    answer_value=resolution.answer,
                )
            )
            filled += 1

        return None, filled

    # ------------------------------------------------------------------
    # Field extraction & setting
    # ------------------------------------------------------------------

    def _extract_fields(self, page) -> list[dict[str, object]]:
        extractor = getattr(page, "extract_fields", None)
        if callable(extractor):
            return list(extractor())
        try:
            return list(page.evaluate(_EXTRACT_FIELDS_JS))
        except Exception:
            return []

    def _set_field(self, page, field: dict[str, object], value: str) -> None:
        setter = getattr(page, "set_field", None)
        if callable(setter):
            setter(field, value)
            return

        selector = str(field["selector"])
        field_type = str(field.get("field_type") or "text").strip()

        if field_type == "file":
            p = Path(value)
            if not p.exists():
                raise RuntimeError(f"SuccessFactors: document path does not exist: {value}")
            page.locator(selector).set_input_files(str(p))
            self._wait(page, 500)

        elif field_type in ("text", "textarea"):
            locator = page.locator(selector)
            locator.scroll_into_view_if_needed()
            locator.fill(value)
            locator.dispatch_event("input")
            locator.dispatch_event("change")
            self._wait(page, 200)

        elif field_type == "select-one":
            normalized = value.strip().lower()
            locator = page.locator(selector)
            locator.scroll_into_view_if_needed()
            options = list(field.get("options") or [])
            # 1. Exact value match
            for opt in options:
                if str(opt.get("value") or "").strip() == value:
                    locator.select_option(value=value)
                    self._wait(page, 300)
                    return
            # 2. Exact label match
            for opt in options:
                if str(opt.get("label") or "").strip().lower() == normalized:
                    locator.select_option(label=str(opt["label"]))
                    self._wait(page, 300)
                    return
            # 3. Boolean yes/no match
            if normalized in {"true", "1", "yes", "on"}:
                for opt in options:
                    if str(opt.get("label") or "").strip().lower() in {"yes", "true"}:
                        locator.select_option(label=str(opt["label"]))
                        self._wait(page, 300)
                        return
            if normalized in {"false", "0", "no", "off"}:
                for opt in options:
                    if str(opt.get("label") or "").strip().lower() in {"no", "false"}:
                        locator.select_option(label=str(opt["label"]))
                        self._wait(page, 300)
                        return
            # 4. Substring match
            for opt in options:
                lbl = str(opt.get("label") or "").strip().lower()
                if normalized in lbl or lbl in normalized:
                    locator.select_option(label=str(opt["label"]))
                    self._wait(page, 300)
                    return
            # Fallback: select by label directly
            try:
                locator.select_option(label=value)
            except Exception:
                pass
            self._wait(page, 300)

        elif field_type == "radio-group":
            normalized = value.strip().lower()
            options = list(field.get("options") or [])
            for opt in options:
                lbl = str(opt.get("label") or opt.get("value") or "").strip().lower()
                if lbl == normalized:
                    page.locator(str(opt["selector"])).check(force=True)
                    self._wait(page, 200)
                    return
            for opt in options:
                lbl = str(opt.get("label") or opt.get("value") or "").strip().lower()
                if normalized in {"true", "1", "yes", "on"} and lbl == "yes":
                    page.locator(str(opt["selector"])).check(force=True)
                    self._wait(page, 200)
                    return
                if normalized in {"false", "0", "no", "off"} and lbl == "no":
                    page.locator(str(opt["selector"])).check(force=True)
                    self._wait(page, 200)
                    return
            for opt in options:
                lbl = str(opt.get("label") or opt.get("value") or "").strip().lower()
                if normalized in lbl or lbl in normalized:
                    page.locator(str(opt["selector"])).check(force=True)
                    self._wait(page, 200)
                    return

        elif field_type == "checkbox":
            normalized = value.strip().lower()
            should_check = normalized in {"true", "1", "yes", "on"}
            locator = page.locator(selector)
            if should_check:
                locator.check(force=True)
            else:
                locator.uncheck(force=True)
            self._wait(page, 200)

    # ------------------------------------------------------------------
    # Navigation & Landing Page helpers
    # ------------------------------------------------------------------

    def _advance_from_job_landing_page(self, page) -> bool:
        """Click the primary Apply Now button on the job posting detail page."""
        current_url = getattr(page, "url", "").lower()
        if "/talentcommunity/apply/" in current_url or "/careers?" in current_url or "/career?" in current_url or "/apply" in current_url:
            return False

        for selector in (
            "a.apply",
            "a.dialogApplyBtn",
            "a[href*='/talentcommunity/apply/']",
            "button:has-text('Apply now')",
            "a:has-text('Apply now')",
            "a:has-text('Apply Now')",
        ):
            loc = page.locator(selector).first
            try:
                if loc.count() > 0 and loc.is_visible():
                    loc.click()
                    self._wait(page, 1500)
                    return True
            except Exception:
                pass
        return False


    def _try_click_next(self, page) -> bool:
        for label in ("Next", "Save & Continue", "Save and Continue", "Continue", "Next Step"):
            loc = page.get_by_role("button", name=label).first
            try:
                if loc.count() > 0 and loc.is_visible():
                    loc.click()
                    self._wait(page, 1000)
                    return True
            except Exception:
                pass
        return False

    def _try_submit(self, page) -> bool:
        clicker = getattr(page, "submit_application", None)
        if callable(clicker):
            clicker()
            return True
        for label in ("Submit Application", "Submit application", "Submit", "Apply", "Finish"):
            loc = page.get_by_role("button", name=label).first
            try:
                if loc.count() > 0 and loc.is_visible():
                    loc.click()
                    self._wait(page, 1500)
                    return True
            except Exception:
                pass
        return False

    # ------------------------------------------------------------------
    # Confirmation & Login Wall detection
    # ------------------------------------------------------------------

    def _extract_confirmation(self, page) -> dict[str, object]:
        extractor = getattr(page, "extract_confirmation", None)
        if callable(extractor):
            res = extractor()
            if res:
                return dict(res)
        try:
            content = page.content().lower()
        except Exception:
            return {}
        for marker in _CONFIRMATION_MARKERS:
            if marker in content:
                return {
                    "source": "successfactors",
                    "marker": marker,
                    "url": getattr(page, "url", ""),
                }
        return {}

    def _has_login_wall(self, page) -> bool:
        checker = getattr(page, "detect_login_wall", None)
        if callable(checker):
            return bool(checker())
        try:
            content = page.content().lower()
            current_url = getattr(page, "url", "").lower()
        except Exception:
            return False

        if "loginflowrequired=true" in current_url:
            return True

        return any(marker in content for marker in _LOGIN_MARKERS)

    def _extract_validation_errors(self, page) -> list[str]:
        try:
            return page.evaluate(
                """() => {
                  const errors = Array.from(document.querySelectorAll(
                    '.has-error, .sapUiErrorMessage, .error, .errorMessage, .invalid-feedback, [aria-invalid="true"]'
                  )).map(el => el.textContent.trim()).filter(Boolean);
                  return Array.from(new Set(errors));
                }"""
            )
        except Exception:
            return []

    # ------------------------------------------------------------------
    # Internal utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _wait(page, ms: int) -> None:
        wait = getattr(page, "wait_for_timeout", None)
        if callable(wait):
            wait(ms)

    @staticmethod
    def _wait_for_spa_ready(page, timeout_ms: int = 15_000) -> None:
        try:
            wfls = getattr(page, "wait_for_load_state", None)
            if callable(wfls):
                wfls("domcontentloaded", timeout=timeout_ms)
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
                question_text=question_text,
                field_name=field_name,
                field_type=field_type,
                details=details or {},
            ),
            steps=steps,
            adapter_name=self.adapter_name,
        )
