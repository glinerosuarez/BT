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
    "successfully applied",
    "your application has been sent",
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

  function getLabel(inp) {
    if (!inp) return '';
    // 1. Label with for attribute
    if (inp.id) {
      const allLabels = Array.from(document.querySelectorAll('label'));
      const matchingLabel = allLabels.find(l => l.getAttribute('for') === inp.id);
      if (matchingLabel) {
        const text = matchingLabel.textContent.trim();
        if (text) return text;
      }
    }
    // 2. Ancestor container label search
    const container = inp.closest('.RCMFormField, .rcmFormElement, .sfTextFieldCss, .form-group, .form-row, .sapUiFormElement, tr, .field-container, div[class*="field"], div[class*="row"]');
    if (container) {
      const lbl = container.querySelector('.rcmFormFieldLabel, label, .control-label, .sapMLabel, .fieldLabel, th, .label, span.normal');
      if (lbl) {
        const text = lbl.textContent.trim();
        if (text) return text;
      }
    }
    // 3. aria-label or placeholder or name
    return inp.getAttribute('aria-label') || inp.getAttribute('placeholder') || inp.getAttribute('title') || inp.getAttribute('name') || '';
  }

  function isRequired(inp) {
    if (!inp) return false;
    if (inp.required || inp.getAttribute('aria-required') === 'true') return true;
    const container = inp.closest('.RCMFormField, .rcmFormElement, .form-group, .form-row, .sapUiFormElement, tr, div[class*="field"]');
    if (container) {
      const text = container.textContent;
      if (/\*\s*indicates required|\*/.test(text) && container.querySelector('.required, .requiredField, .sapMLabelRequired, [aria-required="true"], span.requiredAsterisk')) {
        return true;
      }
    }
    const label = getLabel(inp);
    return /\*|\(required\)/i.test(label);
  }

  // --- 1. File Uploads (Direct inputs or attachment buttons) ---
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

  // --- 2. Combobox / Picklist Inputs (SuccessFactors custom dropdowns) ---
  const comboboxes = Array.from(document.querySelectorAll('input[role="combobox"], input.rcmpaginatedselectinput')).filter(isVisible);
  for (const el of comboboxes) {
    const idx = counter++;
    el.setAttribute('data-jh-idx', String(idx));
    const label = getLabel(el).replace(/^\*\s*/, '').trim();
    fields.push({
      selector: `input[data-jh-idx="${idx}"]`,
      field_name: el.getAttribute('name') || el.getAttribute('id') || '',
      field_type: 'combobox',
      label: label,
      required: isRequired(el),
      current_value: el.value || '',
      options: [],
    });
  }

  // --- 3. Standard Select Elements ---
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
      label: getLabel(el).replace(/^\*\s*/, '').trim(),
      required: isRequired(el),
      current_value: el.selectedOptions[0]?.text?.trim() || '',
      options: opts,
    });
  }

  // --- 4. Standard Text Inputs & Textareas ---
  const textInputs = Array.from(document.querySelectorAll(
    'input[type="text"]:not([role="combobox"]):not(.rcmpaginatedselectinput), input[type="email"], input[type="tel"], input[type="number"], input[type="url"], input:not([type]), textarea'
  )).filter(el => isVisible(el) && !el.disabled && !el.readOnly);

  for (const el of textInputs) {
    const idx = counter++;
    el.setAttribute('data-jh-idx', String(idx));
    const type = el.tagName.toLowerCase() === 'textarea' ? 'textarea' : 'text';
    const label = getLabel(el).replace(/^\*\s*/, '').trim();
    fields.push({
      selector: `${el.tagName.toLowerCase()}[data-jh-idx="${idx}"]`,
      field_name: el.getAttribute('name') || el.getAttribute('id') || '',
      field_type: type,
      label: label,
      required: isRequired(el),
      current_value: el.value || '',
      options: [],
    });
  }

  // --- 5. Radio Groups ---
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
      label: groupLabel.replace(/^\*\s*/, '').trim(),
      required: group.some(isRequired),
      current_value: checkedOpt ? checkedOpt.label : '',
      options: options,
    });
  }

  // --- 6. Checkboxes ---
  const checkboxes = Array.from(document.querySelectorAll('input[type="checkbox"]')).filter(isVisible);
  for (const cb of checkboxes) {
    const idx = counter++;
    cb.setAttribute('data-jh-idx', String(idx));
    fields.push({
      selector: `input[type="checkbox"][data-jh-idx="${idx}"]`,
      field_name: cb.getAttribute('name') || cb.getAttribute('id') || '',
      field_type: 'checkbox',
      label: getLabel(cb).replace(/^\*\s*/, '').trim(),
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

        for pattern in _SF_HOST_PATTERNS:
            if pattern.search(host):
                return True

        if "/talentcommunity/apply/" in path or "/talentcommunity/" in path:
            return True
        if "successfactors" in query or "career_company=" in query or ("company=" in query and "careers" in path):
            return True

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

            # 4. Handle document attachments (Resume & Cover Letter)
            self._handle_documents(page=page, context=context, steps=steps)

            # 5. Expand all collapsible sections so all fields are active
            self._expand_all_sections(page)

            # 6. Extract and fill form fields on the current page
            blocked_result, filled_count = self._fill_current_step(
                page=page, resolver=resolver, context=context, steps=steps
            )
            if blocked_result is not None:
                return blocked_result

            # 7. Check if we reached confirmation after filling
            confirmation = self._extract_confirmation(page)
            if confirmation:
                return SubmitResult(
                    status="submitted",
                    current_url=getattr(page, "url", ""),
                    confirmation_payload=confirmation,
                    steps=steps,
                    adapter_name=self.adapter_name,
                )

            # 8. Try clicking Submit / Apply
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
                validation_errors = self._extract_validation_errors(page)
                if validation_errors:
                    return self._blocked(
                        "form_validation_failed",
                        page,
                        steps,
                        details={"errors": validation_errors},
                    )
                continue

            # 9. Try clicking Next / Continue to advance multi-step wizard
            if self._try_click_next(page):
                self._wait_for_spa_ready(page)
                continue

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
    # Document attachments
    # ------------------------------------------------------------------

    def _handle_documents(self, *, page, context: AdapterContext, steps: list[StepSnapshot]) -> None:
        """Handle SuccessFactors popup-based resume and cover letter upload buttons."""
        # 1. Resume upload
        if context.resume_pdf_path and Path(context.resume_pdf_path).exists():
            resume_icon = page.locator('[id="51:_attachIcon"], [id*="resume" i] .addAttachments, .addAttachments').first
            try:
                if resume_icon.count() > 0 and resume_icon.is_visible():
                    resume_icon.click()
                    self._wait(page, 1000)
                    file_input = page.locator('input[type="file"]').first
                    if file_input.count() > 0:
                        file_input.set_input_files(context.resume_pdf_path)
                        self._wait(page, 2000)
                        steps.append(
                            StepSnapshot(
                                step_key="upload:resume",
                                step_label="Upload resume",
                                status="completed",
                                field_name="resume",
                                field_type="file",
                                question_text="Resume",
                                answer_source="artifact",
                                answer_value=context.resume_pdf_path,
                            )
                        )
            except Exception:
                pass

        # 2. Cover letter upload
        if context.cover_letter_pdf_path and Path(context.cover_letter_pdf_path).exists():
            cover_icon = page.locator('[id="53:_attachIcon"], [id*="cover" i] .addAttachments').first
            try:
                if cover_icon.count() > 0 and cover_icon.is_visible():
                    cover_icon.click()
                    self._wait(page, 1000)
                    file_input = page.locator('input[type="file"]').first
                    if file_input.count() > 0:
                        file_input.set_input_files(context.cover_letter_pdf_path)
                        self._wait(page, 2000)
                        steps.append(
                            StepSnapshot(
                                step_key="upload:cover_letter",
                                step_label="Upload cover letter",
                                status="completed",
                                field_name="cover_letter",
                                field_type="file",
                                question_text="Cover Letter",
                                answer_source="artifact",
                                answer_value=context.cover_letter_pdf_path,
                            )
                        )
            except Exception:
                pass

    def _expand_all_sections(self, page) -> None:
        try:
            loc = page.locator('a:has-text("Expand all sections"), [id*="expandAllSections"]').first
            if loc.count() > 0 and loc.is_visible():
                loc.click()
                self._wait(page, 1000)
        except Exception:
            pass

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

        elif field_type == "combobox":
            locator = page.locator(selector)
            locator.scroll_into_view_if_needed()
            locator.click()
            self._wait(page, 500)
            normalized = value.strip().lower()
            tokens = [t for t in re.split(r"[\s/,'\-]+", normalized) if len(t) > 2 and t not in {"the", "and", "for", "with", "have"}]

            clicked = False
            # 1. Exact match or single-word Yes/No exact match
            for opt_loc in page.locator('li[id*=":item"], a[role="menuitem"], [role="option"], .fd-list__item').all():
                try:
                    txt = opt_loc.text_content().strip().lower()
                    if not txt or txt == "no selection":
                        continue
                    if txt == normalized:
                        opt_loc.click()
                        clicked = True
                        break
                    if normalized in {"no", "false"} and txt == "no":
                        opt_loc.click()
                        clicked = True
                        break
                    if normalized in {"yes", "true"} and txt == "yes":
                        opt_loc.click()
                        clicked = True
                        break
                except Exception:
                    pass

            if not clicked:
                # 2. Heuristic matching with negation awareness
                is_no = normalized.startswith("no") or "don't" in normalized or "not" in normalized or "do not" in normalized
                is_yes = normalized.startswith("yes") and not is_no
                for opt_loc in page.locator('li[id*=":item"], a[role="menuitem"], [role="option"], .fd-list__item').all():
                    try:
                        txt = opt_loc.text_content().strip().lower()
                        if not txt or txt == "no selection":
                            continue
                        opt_is_no = txt.startswith("no") or "don't" in txt or "not" in txt or "do not" in txt
                        if is_no and opt_is_no:
                            opt_loc.click()
                            clicked = True
                            break
                        if is_yes and not opt_is_no and (txt.startswith("yes") or "i have a disability" in txt):
                            opt_loc.click()
                            clicked = True
                            break
                        if normalized in txt or txt in normalized:
                            opt_loc.click()
                            clicked = True
                            break
                        if tokens and any(t in txt for t in tokens):
                            if (is_no and opt_is_no) or (not is_no and not opt_is_no):
                                opt_loc.click()
                                clicked = True
                                break
                    except Exception:
                        pass

            if not clicked:
                try:
                    locator.fill(value)
                    locator.dispatch_event("input")
                    locator.dispatch_event("change")
                except Exception:
                    pass
            self._wait(page, 300)



        elif field_type == "select-one":
            normalized = value.strip().lower()
            locator = page.locator(selector)
            locator.scroll_into_view_if_needed()
            options = list(field.get("options") or [])
            for opt in options:
                if str(opt.get("value") or "").strip() == value:
                    locator.select_option(value=value)
                    self._wait(page, 300)
                    return
            for opt in options:
                if str(opt.get("label") or "").strip().lower() == normalized:
                    locator.select_option(label=str(opt["label"]))
                    self._wait(page, 300)
                    return
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
            for opt in options:
                lbl = str(opt.get("label") or "").strip().lower()
                if normalized in lbl or lbl in normalized:
                    locator.select_option(label=str(opt["label"]))
                    self._wait(page, 300)
                    return
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
        if "/talentcommunity/apply/" in current_url or "/careers?" in current_url or "/career?" in current_url or "/apply" in current_url or "portalcareer" in current_url:
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
        for selector in (
            "span.rcmSaveButton:has-text('Apply')",
            "[id*='_submitBtn']",
            "button:has-text('Submit Application')",
            "button:has-text('Submit application')",
            "button:has-text('Submit')",
            "button:has-text('Apply')",
            "button:has-text('Finish')",
        ):
            loc = page.locator(selector).first
            try:
                if loc.count() > 0 and loc.is_visible():
                    loc.click()
                    self._wait(page, 2000)
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
            title = (getattr(page, "title", lambda: "")() or "").lower()
            full_text = f"{title} {content}"
        except Exception:
            return {}
        for marker in _CONFIRMATION_MARKERS:
            if marker in full_text:
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
