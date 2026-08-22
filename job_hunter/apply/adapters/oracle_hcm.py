"""Adapter for Oracle HCM Cloud Candidate Experience (hcmUI/CandidateExperience).

Oracle HCM Cloud Fusion Recruiting runs on Oracle JET (JavaScript Extension Toolkit).
Components use ``oj-`` prefixed custom elements, e.g.:
  - ``oj-input-text``      -> renders ``<input class="oj-text-field-input">``
  - ``oj-textarea``        -> renders ``<textarea class="oj-text-field-input">``
  - ``oj-select-single``   -> renders a combobox with listbox popup
  - ``oj-radioset``        -> renders radio buttons inside ``<oj-option>``
  - ``oj-checkboxset``     -> renders checkboxes inside ``<oj-option>``

URL pattern: ``*.fa.*.oraclecloud.com/hcmUI/CandidateExperience/``
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from job_hunter.apply.resolver import AnswerResolver, ResolutionError
from job_hunter.apply.types import Blocker, StepSnapshot, SubmitResult

_ORACLE_HOST_PATTERN = re.compile(r"\.fa\.[^.]+\.oraclecloud\.com$", re.IGNORECASE)
_HCM_PATH_PREFIX = "/hcmUI/CandidateExperience"

_CONFIRMATION_MARKERS = (
    "application submitted",
    "thank you for applying",
    "your application has been submitted",
    "we received your application",
    "application is complete",
    "successfully submitted",
    "you're all set",
    "you are all set",
    "saving job application",
    "creating candidate info",
    "you already applied for this job",
    "you previously applied",
    "already applied",
)



_LOGIN_MARKERS = (
    "sign in",
    "create an account",
    "log in to apply",
    "please sign in",
)

_EXTRACT_FIELDS_JS = r"""
() => {
  const fields = [];
  let counter = 0;

  const isVisible = (el) => {
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return (
      style.visibility !== 'hidden' &&
      style.display !== 'none' &&
      rect.width > 0 &&
      rect.height > 0
    );
  };

  const cleanText = (t) => (t || '').replace(/\s+/g, ' ').trim();

  const labelFor = (el) => {
    const id = el.id || el.getAttribute('id') || '';
    if (id) {
      const lbl = document.querySelector(`label[for="${CSS.escape(id)}"]`);
      if (lbl) return cleanText(lbl.textContent);
    }
    const row = el.closest('.input-row, .phone-row, .address-block, .fieldset, .cx-select') || el.parentElement;
    if (row) {
      const lbl = row.querySelector('.input-row__label, label, .input-field__label');
      if (lbl) return cleanText(lbl.textContent);
    }
    const ariaLabel = el.getAttribute('aria-label');
    if (ariaLabel) return cleanText(ariaLabel);
    const labelledBy = el.getAttribute('aria-labelledby');
    if (labelledBy) {
      const parts = labelledBy.split(' ');
      const texts = parts.map(i => document.getElementById(i)?.textContent || '').join(' ');
      if (cleanText(texts)) return cleanText(texts);
    }
    return '';
  };

  const isRequired = (el) => {
    if (el.required) return true;
    if (el.getAttribute('aria-required') === 'true') return true;
    const row = el.closest('.input-row, .cx-select');
    if (row && (row.querySelector('.input-row__label--required') || row.classList.contains('input-row__label--required'))) return true;
    const lbl = labelFor(el);
    if (lbl.includes('*')) return true;
    return false;
  };

  // 1. Pill button groups (application questions)
  for (const pillContainer of Array.from(document.querySelectorAll('.cx-select-pills-container'))) {
    if (!isVisible(pillContainer)) continue;
    const buttons = Array.from(pillContainer.querySelectorAll('button.cx-select-pill-section, button')).filter(
      b => isVisible(b) && !b.disabled
    );
    if (!buttons.length) continue;
    const row = pillContainer.closest('.input-row') || pillContainer.parentElement;
    const qText = cleanText(row?.querySelector('.input-row__label, label')?.textContent) || labelFor(pillContainer);
    const required = row?.querySelector('.input-row__label--required') !== null || qText.includes('*');
    const options = buttons.map((btn, idx) => {
      counter++;
      btn.setAttribute('data-jh-idx', String(counter));
      const active = btn.classList.contains('cx-select-pill-section--selected') || btn.classList.contains('active');
      return {
        selector: `[data-jh-idx="${counter}"]`,
        label: cleanText(btn.textContent),
        value: cleanText(btn.textContent),
        active: active
      };
    });
    const currentVal = options.filter(o => o.active).map(o => o.label).join(', ');
    fields.push({
      selector: options[0]?.selector || '',
      field_name: qText,
      field_type: 'pill-group',
      question_text: qText,
      required: required,
      current_value: currentVal,
      options: options
    });
  }

  // 2. Auto-suggest inputs (like addressLine1)
  for (const el of Array.from(document.querySelectorAll('input.cx-select-input--auto-suggest, input[aria-describedby*="oracle-maps"]'))) {
    if (!isVisible(el) || el.disabled || el.readOnly) continue;
    counter++;
    el.setAttribute('data-jh-idx', String(counter));
    fields.push({
      selector: `[data-jh-idx="${counter}"]`,
      field_name: el.name || el.id || '',
      field_type: 'auto-suggest',
      question_text: labelFor(el),
      required: isRequired(el),
      current_value: el.value || '',
    });
  }

  // 3. Combobox / Select-one inputs
  for (const el of Array.from(document.querySelectorAll('input[role="combobox"], input.cx-select-input'))) {
    if (!isVisible(el) || el.disabled || el.readOnly) continue;
    if (el.classList.contains('cx-select-input--auto-suggest') || el.getAttribute('aria-describedby')?.includes('oracle-maps')) continue;
    counter++;
    el.setAttribute('data-jh-idx', String(counter));
    fields.push({
      selector: `[data-jh-idx="${counter}"]`,
      field_name: el.name || el.id || '',
      field_type: 'select-one',
      question_text: labelFor(el),
      required: isRequired(el),
      current_value: el.value || '',
    });
  }

  // 4. File inputs
  for (const el of Array.from(document.querySelectorAll('input[type="file"]'))) {
    counter++;
    el.setAttribute('data-jh-idx', String(counter));
    fields.push({
      selector: `[data-jh-idx="${counter}"]`,
      field_name: el.name || el.id || '',
      field_type: 'file',
      question_text: labelFor(el),
      required: isRequired(el),
      current_value: el.value || '',
    });
  }

  // 5. Standard text / tel / email / textarea inputs
  for (const el of Array.from(document.querySelectorAll('input.input-row__control, input.phone-row__input, input.oj-text-field-input, textarea.input-row__control, textarea.oj-text-field-input, input[type="tel"], input[type="email"], input[type="url"], input[type="text"]'))) {
    if (!isVisible(el) || el.disabled || el.readOnly) continue;
    if (el.getAttribute('role') === 'combobox' || el.classList.contains('cx-select-input')) continue;
    if (el.classList.contains('cx-select-input--auto-suggest') || el.getAttribute('aria-describedby')?.includes('oracle-maps')) continue;
    if (el.type === 'hidden' || el.type === 'file' || el.type === 'radio' || el.type === 'checkbox') continue;
    if (el.getAttribute('data-jh-idx')) continue;
    counter++;
    el.setAttribute('data-jh-idx', String(counter));
    const type = el.tagName === 'TEXTAREA' ? 'textarea' : 'text';
    fields.push({
      selector: `[data-jh-idx="${counter}"]`,
      field_name: el.name || el.id || '',
      field_type: type,
      question_text: labelFor(el),
      required: isRequired(el),
      current_value: el.value || '',
    });
  }

  // 6. Radio groups
  const radioNames = new Set();
  for (const el of Array.from(document.querySelectorAll('input[type="radio"]'))) {
    const isVis = isVisible(el) || (el.offsetParent !== null);
    if (!isVis && !el.closest('.input-row')) continue;
    const name = el.name;
    if (!name || radioNames.has(name)) continue;
    radioNames.add(name);
    const radios = Array.from(document.querySelectorAll(`input[type="radio"][name="${CSS.escape(name)}"]`));
    const qText = labelFor(radios[0]) || name;
    const required = radios.some(isRequired);
    const options = radios.map((r, idx) => {
      counter++;
      r.setAttribute('data-jh-idx', String(counter));
      const lbl = cleanText(r.closest('label')?.textContent || r.parentElement?.querySelector('label')?.textContent || r.value);
      return {
        selector: `[data-jh-idx="${counter}"]`,
        label: lbl,
        value: r.value,
        checked: r.checked,
        index: idx
      };
    });
    const checkedOpt = options.filter(o => o.checked).map(o => o.label || o.value).join(', ');
    fields.push({
      selector: options[0]?.selector || '',
      field_name: name,
      field_type: 'radio-group',
      question_text: qText,
      required: required,
      current_value: checkedOpt,
      options: options
    });
  }

  // 7. Checkbox groups
  for (const cbset of Array.from(document.querySelectorAll('oj-checkboxset'))) {
    if (!isVisible(cbset)) continue;
    const inputs = Array.from(cbset.querySelectorAll('input[type="checkbox"]')).filter(
      el => isVisible(el) && !el.disabled
    );
    if (!inputs.length) continue;
    const legend = (
      cbset.getAttribute('label-hint') ||
      cbset.querySelector('[slot="label"], label, legend')?.textContent || ''
    ).trim();
    const required = inputs.some(isRequired);
    const options = inputs.map((el, idx) => {
      counter++;
      el.setAttribute('data-jh-idx', String(counter));
      const optEl = el.closest('oj-option') || el.closest('label')?.parentElement;
      const optLabel = (optEl?.querySelector('label')?.textContent || el.value || '').trim();
      return { selector: `[data-jh-idx="${counter}"]`, value: el.value, label: optLabel, checked: el.checked, index: idx };
    });
    const checkedLabels = options.filter(o => o.checked).map(o => o.label || o.value);
    fields.push({
      selector: options[0]?.selector || '',
      field_name: inputs[0]?.name || cbset.id || legend,
      field_type: 'checkbox-group',
      question_text: legend,
      required,
      current_value: checkedLabels.join(', '),
      options,
    });
  }

  return fields;
}
"""



class OracleHCMAdapter:
    adapter_name = "oracle_hcm"

    # ------------------------------------------------------------------
    # Portal detection
    # ------------------------------------------------------------------

    def is_oracle_hcm_target(self, url: str, page=None) -> bool:
        """Return True when *url* or the live *page* indicates an Oracle HCM CE portal."""
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        path = parsed.path

        if _ORACLE_HOST_PATTERN.search(host) and _HCM_PATH_PREFIX.lower() in path.lower():
            return True

        checker = getattr(page, "detect_oracle_hcm", None)
        if callable(checker):
            return bool(checker())

        if page is not None:
            content = self._page_text(page).lower()
            if "oraclecloud" in content and "candidateexperience" in content:
                return True
            if ("oj-input-text" in content or "oj-select-single" in content) and (
                "hcm" in content or "recruiting" in content
            ):
                return True
        return False

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def submit(self, *, page, resolver: AnswerResolver, context) -> SubmitResult:
        steps: list[StepSnapshot] = []

        # Oracle HCM is a heavy Oracle JET SPA.  The page is often navigated
        # with only ``domcontentloaded`` fired; wait for network activity to
        # settle and for an interactive landmark to appear before starting.
        self._wait_for_spa_ready(page)

        for attempt in range(15):
            current_url = getattr(page, "url", "")

            # Dismiss any Oracle HCM idle-session dialog ("Are You Still With Us?")
            # before inspecting the page state; it can block all clicks.
            self._dismiss_idle_dialog(page)

            # If landed on a search / job list page, navigate to the specific job
            if "/jobs" in current_url and "/job/" not in current_url:
                job_link = page.locator('a[href*="/job/"]').first
                try:
                    if job_link.count() > 0 and job_link.is_visible():
                        job_link.click()
                        self._wait_for_spa_ready(page)
                        continue
                except Exception:
                    pass


            confirmation = self._extract_confirmation(page)
            if confirmation:
                return SubmitResult(
                    status="submitted",
                    current_url=current_url,
                    confirmation_payload=confirmation,
                    steps=steps,
                    adapter_name=self.adapter_name,
                )

            if self._has_login_wall(page):
                return self._blocked(
                    "candidate_account_required",
                    page,
                    steps,
                    question_text="Oracle HCM requires sign-in to continue.",
                    details={"hint": "Sign in or create an Oracle HCM account to proceed."},
                )

            # Detect the email OTP verification screen ("Confirm Your Identity")
            verification_blocker = self._detect_email_verification_blocker(page)
            if verification_blocker is not None:
                return SubmitResult(
                    status="blocked",
                    current_url=current_url,
                    blocker=verification_blocker,
                    steps=steps,
                    adapter_name=self.adapter_name,
                )

            # Handle the email-gate step (/apply/email) — Oracle HCM asks for
            # a guest email before creating a profile.  Fill it and advance.
            if self._handle_email_gate(page=page, resolver=resolver, steps=steps):
                self._wait(page, 3000)
                continue


            if self._try_click_apply_button(page):
                self._wait(page, 3000)
                steps.append(
                    StepSnapshot(
                        step_key="oracle_hcm:click_apply",
                        step_label="Click Apply button",
                        status="completed",
                    )
                )
                continue

            # Clean up broken imported profile tiles if any
            self._clean_broken_profile_tiles(page)

            result, filled = self._fill_current_step(
                page=page, resolver=resolver, context=context, steps=steps
            )
            if result is not None:
                return result

            if self._try_click_next(page):
                self._wait(page, 2000)
                continue

            if self._try_submit(page):
                self._wait(page, 5000)
                confirmation = self._extract_confirmation(page)
                if confirmation:
                    return SubmitResult(
                        status="submitted",
                        current_url=getattr(page, "url", ""),
                        confirmation_payload=confirmation,
                        steps=steps,
                        adapter_name=self.adapter_name,
                    )
                if self._has_validation_errors(page):
                    continue
                return self._blocked("ambiguous_confirmation", page, steps)

            if attempt > 2 and filled == 0:
                break

        return self._blocked("stalled_application", page, steps)


    # ------------------------------------------------------------------
    # Step filling
    # ------------------------------------------------------------------

    def _fill_current_step(
        self, *, page, resolver: AnswerResolver, context, steps: list[StepSnapshot]
    ) -> tuple[SubmitResult | None, int]:
        fields = self._extract_fields(page)
        filled = 0
        for field in fields:
            question_text = str(field.get("question_text") or field.get("label") or "").strip()
            field_name = str(field.get("field_name") or "").strip()
            field_type = str(field.get("field_type") or "text").strip()
            required = bool(field.get("required", False))
            current_value = str(field.get("current_value") or "").strip()

            if current_value:
                continue

            if field_type == "file":
                artifact = self._artifact_for_field(
                    context=context, question_text=question_text, field_name=field_name
                )
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

            if not resolution.answer or (
                resolution.answer.lower() in {"false", "no", "0"}
                and any(tok in f"{field_name} {question_text}".lower() for tok in ("date", "month", "day", "year", "military", "discharge"))
            ):
                if not required:
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
    # Field extraction
    # ------------------------------------------------------------------

    def _extract_fields(self, page) -> list[dict[str, object]]:
        extractor = getattr(page, "extract_fields", None)
        if callable(extractor):
            return list(extractor())
        return page.evaluate(_EXTRACT_FIELDS_JS)

    # ------------------------------------------------------------------
    # Field interaction
    # ------------------------------------------------------------------

    def _set_field(self, page, field: dict[str, object], value: str) -> None:
        setter = getattr(page, "set_field", None)
        if callable(setter):
            setter(field, value)
            return

        selector = str(field.get("selector") or "")
        field_type = str(field.get("field_type") or "text")

        if field_type == "file":
            locator = page.locator(selector)
            try:
                locator.set_input_files(value)
            except Exception:
                ojpicker = page.locator("oj-file-picker").first
                if ojpicker.count() > 0:
                    with page.expect_file_chooser() as chooser_info:
                        ojpicker.click()
                    chooser_info.value.set_files(value)
            self._wait(page, 2000)

        elif field_type == "pill-group":
            normalized = value.strip().lower()
            options = list(field.get("options") or [])
            # 1. Exact match
            for opt in options:
                opt_label = str(opt.get("label") or opt.get("value") or "").strip().lower()
                if opt_label == normalized:
                    page.locator(str(opt["selector"])).click()
                    self._wait(page, 300)
                    return
            # 2. Boolean fallback
            for opt in options:
                opt_label = str(opt.get("label") or opt.get("value") or "").strip().lower()
                if normalized in {"true", "1", "yes", "on"} and opt_label == "yes":
                    page.locator(str(opt["selector"])).click()
                    self._wait(page, 300)
                    return
                if normalized in {"false", "0", "no", "off"} and opt_label == "no":
                    page.locator(str(opt["selector"])).click()
                    self._wait(page, 300)
                    return
            # 3. Substring / partial match
            for opt in options:
                opt_label = str(opt.get("label") or opt.get("value") or "").strip().lower()
                if normalized in opt_label or opt_label in normalized:
                    page.locator(str(opt["selector"])).click()
                    self._wait(page, 300)
                    return
            raise RuntimeError(
                f"Oracle HCM pill-group: no matching option '{value}' for {field.get('field_name') or field.get('question_text')}"
            )

        elif field_type == "auto-suggest":
            locator = page.locator(selector)
            locator.click()
            self._wait(page, 200)
            locator.fill("")
            locator.fill(value)
            locator.dispatch_event("input")
            locator.dispatch_event("change")
            self._wait(page, 800)
            # Check if any suggestion list item appeared
            opt = self._find_listbox_option(page, value)
            if opt is not None:
                try:
                    opt.click()
                    self._wait(page, 400)
                    return
                except Exception:
                    pass
            # Dismiss popup if still open
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass
            self._wait(page, 300)

        elif field_type in ("text", "textarea"):
            locator = page.locator(selector)
            locator.fill(value)
            locator.dispatch_event("input")
            locator.dispatch_event("change")

        elif field_type == "select-one":
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "on"}:
                value = "Yes"
            elif normalized in {"false", "0", "no", "off"}:
                value = "No"
            locator = page.locator(selector)
            locator.scroll_into_view_if_needed()
            locator.click()
            self._wait(page, 500)

            # Direct check before filtering
            option = self._find_listbox_option(page, value)
            if option is not None:
                option.click()
                self._wait(page, 400)
                return

            # Filter with typed value
            locator.fill("")
            locator.fill(value)
            self._wait(page, 600)
            option = self._find_listbox_option(page, value)
            if option is not None:
                option.click()
                self._wait(page, 400)
                return

            # Clear filter and try clicking first option
            locator.fill("")
            self._wait(page, 400)
            first_opt = page.locator(
                ".cx-select__list li, [role='option'], .oj-listbox-result, .cx-select__option, .cx-select-option, .cx-select__item, [role='gridcell']"
            ).first
            if first_opt.count() > 0 and first_opt.is_visible():
                first_opt.click()
                self._wait(page, 400)
                return

            # Keyboard fallback: ArrowDown then Enter
            locator.press("ArrowDown")
            self._wait(page, 200)
            locator.press("Enter")
            self._wait(page, 300)

        elif field_type in ("radio-group", "checkbox-group"):
            normalized = value.strip().lower()
            options = list(field.get("options") or [])
            for opt in options:
                opt_label = str(opt.get("label") or opt.get("value") or "").strip().lower()
                if opt_label == normalized:
                    page.locator(str(opt["selector"])).check(force=True)
                    self._wait(page, 300)
                    return
            for opt in options:
                opt_label = str(opt.get("label") or opt.get("value") or "").strip().lower()
                if normalized in {"true", "1", "yes", "on"} and opt_label == "yes":
                    page.locator(str(opt["selector"])).check(force=True)
                    self._wait(page, 300)
                    return
                if normalized in {"false", "0", "no", "off"} and opt_label == "no":
                    page.locator(str(opt["selector"])).check(force=True)
                    self._wait(page, 300)
                    return
            # Substring match
            for opt in options:
                opt_label = str(opt.get("label") or opt.get("value") or "").strip().lower()
                if normalized in opt_label or opt_label in normalized:
                    page.locator(str(opt["selector"])).check(force=True)
                    self._wait(page, 300)
                    return
            raise RuntimeError(
                f"Oracle HCM: no matching option '{value}' for "
                f"{field.get('field_name') or field.get('question_text')}"
            )

    def _find_listbox_option(self, page, value: str):
        normalized_value = re.sub(r"[^a-z0-9 ]+", " ", value.lower()).strip()
        for selector in (
            "[role='option']",
            ".oj-listbox-result",
            ".oj-select-results li",
            "li[data-oj-item-context]",
            ".cx-select__list li",
            ".cx-select__option",
            ".cx-select-option",
            ".cx-select__item",
            "[role='gridcell']",
            ".table-row",
            ".cx-select-list-item",
        ):
            options = page.locator(selector)
            try:
                count = options.count()
            except Exception:
                continue
            for i in range(count):
                opt = options.nth(i)
                try:
                    if not opt.is_visible():
                        continue
                    text = opt.inner_text().strip()
                except Exception:
                    continue
                normalized_opt = re.sub(r"[^a-z0-9 ]+", " ", text.lower()).strip()
                if normalized_value == normalized_opt:
                    return opt
                if normalized_value in {"yes", "no"} and normalized_opt.startswith(
                    f"{normalized_value} "
                ):
                    return opt
                for degree_level in ("associate", "bachelor", "master", "doctor"):
                    if (
                        degree_level in normalized_value.split()
                        and degree_level in normalized_opt.split()
                    ):
                        return opt
                if normalized_value in normalized_opt or normalized_opt in normalized_value:
                    return opt
        return None



    # ------------------------------------------------------------------
    # Dialog / gate helpers
    # ------------------------------------------------------------------

    def _dismiss_idle_dialog(self, page) -> None:
        """Click 'Continue Working' on Oracle HCM's idle-session timeout dialog."""
        try:
            page.evaluate(
                """() => {
                  const btns = Array.from(document.querySelectorAll('button, .app-dialog__footer-button, [role="button"]'));
                  for (const btn of btns) {
                    const text = (btn.textContent || '').trim().toLowerCase();
                    if (text.includes('continue working') || text.includes('stay signed in')) {
                      btn.click();
                      return true;
                    }
                  }
                  return false;
                }"""
            )
            self._wait(page, 300)
        except Exception:
            pass

        for label in ("Continue Working", "Continue working", "Stay Signed In", "Continue"):
            loc = page.get_by_role("button", name=label)
            try:
                if loc.count() > 0 and loc.first.is_visible():
                    loc.first.click()
                    self._wait(page, 300)
                    return
            except Exception:
                pass

    def _handle_email_gate(
        self, *, page, resolver: AnswerResolver, steps: list[StepSnapshot]
    ) -> bool:
        """Handle the Oracle HCM guest-email authentication step (/apply/email).

        Returns True if the email gate was detected and handled (caller should
        ``continue`` the loop), False if the current page is not the email gate.
        """
        try:
            current_url = getattr(page, "url", "")
            if "/apply/email" not in current_url.lower() and "/apply/signin" not in current_url.lower():
                present = page.evaluate(
                    """() => {
                      const body = (document.body?.textContent || '').toLowerCase();
                      return body.includes('get started right away by simply using your email') ||
                             body.includes('authentication screen');
                    }"""
                )
                if not present:
                    return False
        except Exception:
            return False

        # Resolve the applicant email from the profile
        try:
            email_resolution = resolver.resolve(
                question_text="email address",
                field_name="email",
                field_type="text",
            )
            email = email_resolution.answer
        except Exception:
            return False

        if not email:
            return False

        # Dismiss cookie consent if present
        try:
            cookie_btn = page.locator('button.cookie-consent__button.accept, button:has-text("Accept")').first
            if cookie_btn.count() > 0 and cookie_btn.is_visible():
                cookie_btn.click()
                self._wait(page, 500)
        except Exception:
            pass

        # Wait up to 5s for the email input to appear on the SPA
        for _ in range(10):
            try:
                filled = page.evaluate(
                    f"""() => {{
                      const el = document.querySelector('input[type="email"], input#primary-email-0, input.oj-text-field-input');
                      if (!el) return false;
                      el.focus();
                      el.value = {repr(email)};
                      el.dispatchEvent(new Event('input', {{bubbles: true}}));
                      el.dispatchEvent(new Event('change', {{bubbles: true}}));
                      
                      // Check legal disclaimer checkbox if present
                      const cb = document.getElementById('legal-disclaimer-checkbox') || document.querySelector('input[type="checkbox"]');
                      if (cb && !cb.checked) {{
                        cb.click();
                        cb.dispatchEvent(new Event('change', {{bubbles: true}}));
                      }}
                      return true;
                    }}"""
                )
                if filled:
                    break
            except Exception:
                pass
            self._wait(page, 500)
        else:
            return False

        steps.append(
            StepSnapshot(
                step_key="oracle_hcm:email_gate",
                step_label="Fill guest email",
                status="completed",
                field_name="email",
                field_type="text",
                question_text="Email Address",
                answer_source="structured:identity.email",
                answer_value=email,
            )
        )

        # Click Next to proceed past the email gate
        self._wait(page, 500)
        self._try_click_next(page)
        self._wait(page, 1500)
        return True



    # ------------------------------------------------------------------
    # Navigation helpers
    # ------------------------------------------------------------------

    def _try_click_apply_button(self, page) -> bool:
        clicker = getattr(page, "click_apply_button", None)
        if callable(clicker):
            return bool(clicker())
        for label in ("Apply Now", "Apply now", "Apply", "Start Application", "Quick Apply"):
            for role in ("button", "link"):
                loc = page.get_by_role(role, name=label)
                try:
                    if loc.count() > 0 and loc.first.is_visible():
                        loc.first.click()
                        return True
                except Exception:
                    pass
        return False

    def _try_click_next(self, page) -> bool:
        for label in ("Next", "Continue", "Save and Continue", "Save & Continue"):
            loc = page.get_by_role("button", name=label)
            try:
                if loc.count() > 0 and loc.first.is_visible():
                    loc.first.click()
                    return True
            except Exception:
                pass
        return False

    def _try_submit(self, page) -> bool:
        clicker = getattr(page, "submit_application", None)
        if callable(clicker):
            clicker()
            return True
        for label in ("Submit", "Submit Application", "Submit application", "Finish"):
            loc = page.get_by_role("button", name=label)
            try:
                if loc.count() > 0 and loc.first.is_visible():
                    loc.first.click()
                    return True
            except Exception:
                pass
        return False

    # ------------------------------------------------------------------
    # Confirmation / login wall
    # ------------------------------------------------------------------

    def _extract_confirmation(self, page) -> dict[str, object]:
        extractor = getattr(page, "extract_confirmation", None)
        if callable(extractor):
            payload = dict(extractor() or {})
            if payload:
                return payload
        try:
            content = page.content().lower()
        except Exception:
            return {}
        if any(marker in content for marker in _CONFIRMATION_MARKERS):
            return {"message": "Application submitted", "url": getattr(page, "url", "")}
        return {}

    def _has_login_wall(self, page) -> bool:
        checker = getattr(page, "detect_login_wall", None)
        if callable(checker):
            return bool(checker())
        try:
            content = page.content().lower()
        except Exception:
            return False
        return any(marker in content for marker in _LOGIN_MARKERS)

    def _detect_email_verification_blocker(self, page) -> Blocker | None:
        """Return a Blocker if Oracle HCM is showing the OTP 'Confirm Your Identity' screen."""
        try:
            is_visible_otp = page.evaluate(
                """() => {
                  const pins = Array.from(document.querySelectorAll('input.pin-code-input__input, input[id^="pin-code-"]'));
                  if (pins.length > 0 && pins.some(el => el.offsetParent !== null && window.getComputedStyle(el).display !== 'none')) {
                    return true;
                  }
                  const headings = Array.from(document.querySelectorAll('h1, h2, h3, .apply-flow-dialog__title, .dialog-header, p, span'));
                  return headings.some(h => {
                    const text = (h.textContent || '').toLowerCase();
                    const style = window.getComputedStyle(h);
                    return (text.includes('confirm your identity') || text.includes('enter verification code')) &&
                           style.display !== 'none' && style.visibility !== 'hidden' && h.offsetParent !== null;
                  });
                }"""
            )
            if not is_visible_otp:
                return None
        except Exception:
            return None

        # It's the OTP screen — extract how many digits Oracle expects
        try:
            digits = page.evaluate(
                """() => {
                  const inputs = Array.from(document.querySelectorAll('input.pin-code-input__input, input[id^="pin-code-"]'))
                    .filter(el => {
                      const style = window.getComputedStyle(el);
                      return style.display !== 'none' && style.visibility !== 'hidden' && !el.disabled;
                    });
                  return inputs.length;
                }"""
            )
        except Exception:
            digits = 6
        return Blocker(
            reason="email_verification_required",
            field_name="verification_code",
            field_type="verification_code",
            question_text="Confirm Your Identity — Oracle HCM email verification code required.",
            details={"digits": digits or 6},
        )


    def complete_email_verification(
        self, *, page, code: str, steps: list[StepSnapshot], context=None, resolver=None
    ) -> SubmitResult:
        """Fill the Oracle HCM OTP code and advance to the next step."""
        self._dismiss_idle_dialog(page)
        clean_code = "".join(c for c in code if c.isalnum())
        try:
            filled = False
            for i, char in enumerate(clean_code):
                loc = page.locator(f"#pin-code-{i + 1}")
                if loc.count() > 0 and loc.first.is_visible():
                    loc.first.fill(char)
                    loc.first.dispatch_event("input")
                    loc.first.dispatch_event("change")
                    filled = True

            if not filled:
                page.evaluate(
                    f"""() => {{
                      const pinInputs = Array.from(document.querySelectorAll(
                        'input.pin-code-input__input, input[id^="pin-code-"]'
                      )).filter(el => el.offsetParent !== null && !el.disabled);
                      if (pinInputs.length >= {len(clean_code)}) {{
                        const codeStr = {repr(clean_code)};
                        for (let i = 0; i < codeStr.length; i++) {{
                          pinInputs[i].focus();
                          pinInputs[i].value = codeStr[i];
                          pinInputs[i].dispatchEvent(new Event('input', {{bubbles: true}}));
                          pinInputs[i].dispatchEvent(new Event('change', {{bubbles: true}}));
                        }}
                        return;
                      }}
                      const inputs = Array.from(document.querySelectorAll(
                        'input[type="text"], input[type="number"], input.oj-text-field-input'
                      )).filter(el => {{
                        const style = window.getComputedStyle(el);
                        return style.display !== 'none' && style.visibility !== 'hidden' && !el.disabled;
                      }});
                      if (!inputs.length) return;
                      const el = inputs[0];
                      el.focus();
                      el.value = {repr(clean_code)};
                      el.dispatchEvent(new Event('input', {{bubbles: true}}));
                      el.dispatchEvent(new Event('change', {{bubbles: true}}));
                    }}"""
                )
        except Exception:
            pass

        steps.append(
            StepSnapshot(
                step_key="oracle_hcm:email_verification",
                step_label="Fill Oracle HCM email verification code",
                status="completed",
                field_name="verification_code",
                field_type="verification_code",
                question_text="Confirm Your Identity",
                answer_source="gmail",
                answer_value="redacted",
            )
        )

        self._wait(page, 1000)
        self._dismiss_idle_dialog(page)

        # Click the Verify / Confirm / Next button
        clicked = False
        for label in ("Verify", "Confirm", "Next", "Continue"):
            loc = page.get_by_role("button", name=label)
            try:
                if loc.count() > 0 and loc.first.is_visible():
                    loc.first.click()
                    clicked = True
                    break
            except Exception:
                pass

        if not clicked:
            try:
                v_btn = page.locator('button[data-bind*="verify"], button.apply-flow-dialog__button--primary').first
                if v_btn.count() > 0 and v_btn.is_visible():
                    v_btn.click()
            except Exception:
                pass

        # Wait for pin-code inputs to detach or disappear
        try:
            page.wait_for_function(
                """() => {
                  const pins = document.querySelectorAll('input.pin-code-input__input, input[id^="pin-code-"]');
                  return pins.length === 0 || !Array.from(pins).some(el => el.offsetParent !== null);
                }""",
                timeout=12000,
            )
        except Exception:
            pass

        self._wait(page, 3000)

        confirmation = self._extract_confirmation(page)
        if confirmation:
            return SubmitResult(
                status="submitted",
                current_url=getattr(page, "url", ""),
                confirmation_payload=confirmation,
                steps=steps,
                adapter_name=self.adapter_name,
            )

        # Re-enter the main submit loop after verification
        return self.submit(page=page, resolver=resolver, context=context)



    # ------------------------------------------------------------------
    # Artifact helper
    # ------------------------------------------------------------------

    def _artifact_for_field(self, *, context, question_text: str, field_name: str) -> str:
        desc = f"{question_text} {field_name}".lower()
        # Skip the profile auto-import parser to avoid generating broken draft cards
        if "import your profile" in desc or "import profile" in desc:
            return ""
        if any(tok in desc for tok in ("resume", "curriculum vitae", "cv", "attachment")):
            return context.resume_pdf_path
        if "cover" in desc and "letter" in desc:
            return context.cover_letter_pdf_path
        if "transcript" in desc:
            return getattr(context, "transcript_path", "")
        return ""

    def _clean_broken_profile_tiles(self, page) -> None:
        """Delete invalid or broken education/experience tiles created by resume parsing."""
        try:
            page.evaluate(
                """() => {
                  const deleteBtns = Array.from(document.querySelectorAll('.apply-flow-profile-item-tile__delete-icon, button[aria-label="Delete"]'));
                  for (const btn of deleteBtns) {
                    const tile = btn.closest('.apply-flow-profile-item-tile, .apply-flow-block');
                    if (tile && (tile.textContent.includes('Fields to fix') || tile.textContent.includes('Unnamed') || tile.classList.contains('apply-flow-profile-item-tile--invalid'))) {
                      btn.click();
                      const confirmBtn = document.querySelector('.apply-flow-dialog__button--primary, button[aria-label="Delete"]');
                      if (confirmBtn) confirmBtn.click();
                    }
                  }
                }"""
            )
        except Exception:
            pass

    def _has_validation_errors(self, page) -> bool:
        try:
            content = page.content().lower()
            return (
                "issues that need to be fixed" in content
                or "fields to fix" in content
                or "this info is required" in content
                or "field is required" in content
            )
        except Exception:
            return False


    # ------------------------------------------------------------------
    # Internal utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _page_text(page) -> str:
        try:
            return page.content()
        except Exception:
            return ""

    @staticmethod
    def _wait(page, ms: int) -> None:
        wait = getattr(page, "wait_for_timeout", None)
        if callable(wait):
            wait(ms)

    @staticmethod
    def _wait_for_spa_ready(page, timeout_ms: int = 20_000) -> None:
        """Wait for the Oracle JET SPA to hydrate.

        Tries ``networkidle`` first (up to *timeout_ms*), then polls for any
        interactive element that indicates the shell has rendered: an Apply
        button, an OJ form input, or a confirmation heading.  Falls back
        gracefully so the main loop always runs even if the heuristic times out.
        """
        # 1. networkidle – best signal that XHR/fetch bursts have settled
        try:
            wfls = getattr(page, "wait_for_load_state", None)
            if callable(wfls):
                wfls("networkidle", timeout=timeout_ms)
        except Exception:
            pass

        # 2. Poll for an interactive landmark (up to 15 s in 500 ms ticks)
        try:
            page.wait_for_function(
                """() => {
                  // Apply button present
                  for (const role of ['button', 'link']) {
                    for (const name of ['Apply Now', 'Apply now', 'Apply', 'Start Application']) {
                      const el = document.querySelector(
                        `[role="${role}"]`
                      );
                      if (el && el.textContent.includes(name)) return true;
                    }
                  }
                  // Any OJ form input rendered
                  if (document.querySelector('input.oj-text-field-input, input[role="combobox"]')) return true;
                  // Confirmation heading
                  const body = (document.body?.textContent || '').toLowerCase();
                  if (body.includes('application submitted') || body.includes('thank you for applying')) return true;
                  return false;
                }""",
                timeout=15_000,
            )
        except Exception:
            # If nothing interactive appears, let the main loop handle it
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
