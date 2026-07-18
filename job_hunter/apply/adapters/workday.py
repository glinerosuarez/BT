from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

from job_hunter.apply.adapters.base import AdapterContext
from job_hunter.apply.resolver import ResolutionError
from job_hunter.apply.types import Blocker, StepSnapshot, SubmitResult

_CONFIRMATION_MARKERS = (
    "application submitted",
    "successfully submitted",
    "thank you for applying",
    "your application has been submitted",
)
_START_APPLICATION_MARKERS = (
    "start your application",
    "apply manually",
)
_FORM_STAGE_MARKERS = (
    "my information",
    "my experience",
    "application questions",
    "voluntary disclosures",
    "self identify",
    "review",
)
_EMPTY_SELECT_VALUES = {"", "select...", "select", "select one", "choose one", "choose an option"}


def _canonical_country(country: str) -> str:
    lowered = (country or "").strip().lower()
    if lowered in {"usa", "us", "u.s.", "u.s.a.", "united states", "united states of america"}:
        return "united states"
    return lowered


class WorkdayAdapter:
    adapter_name = "workday"

    def is_workday_target(self, url: str, page=None) -> bool:
        parsed = urlparse(url.strip())
        host = parsed.netloc.lower()
        if "myworkdayjobs.com" in host or "workdayjobs.com" in host:
            return True
        content = self._page_text(page).lower() if page is not None else ""
        return "careers at" in content and "workday" in content

    def submit(self, *, page, resolver, context: AdapterContext) -> SubmitResult:
        current_url = str(getattr(page, "url", "") or "")
        for _ in range(6):
            self._wait_for_render(page)
            confirmation = self._extract_confirmation(page)
            if confirmation:
                return SubmitResult(
                    status="submitted",
                    current_url=str(getattr(page, "url", "") or ""),
                    confirmation_payload=confirmation,
                    adapter_name=self.adapter_name,
                )

            apply_url = self._workday_action_url(page, action="apply")
            if "/apply" not in current_url.lower() and apply_url:
                page.goto(apply_url, wait_until="domcontentloaded")
                current_url = str(getattr(page, "url", "") or apply_url)
                continue

            if self._is_public_job_page(page):
                return self._blocked(
                    "apply_button_missing",
                    page,
                    question_text="Could not locate the Workday Apply link on the public job page.",
                    details={"stage": "public_job_page"},
                )

            manual_url = self._workday_action_url(page, action="apply_manually")
            if "/apply" in current_url.lower() and "/apply/applymanually" not in current_url.lower() and manual_url:
                page.goto(manual_url, wait_until="domcontentloaded")
                current_url = str(getattr(page, "url", "") or manual_url)
                continue

            if self._is_start_application_page(page):
                return self._blocked(
                    "apply_button_missing",
                    page,
                    question_text="Could not locate the Workday Apply Manually link on the application entry page.",
                    details={"stage": "start_application"},
                )

            if self._has_email_verification_gate(page):
                return self._blocked(
                    "email_verification_required",
                    page,
                    question_text="Enter verification code",
                    field_name="email_verification",
                    field_type="verification_code",
                    details={
                        "provider": "workday",
                        "stage": "email_verification",
                        "current_url": str(getattr(page, "url", "") or current_url),
                    },
                )

            if self._has_account_gate(page):
                return self._blocked(
                    "candidate_account_bootstrap_required",
                    page,
                    question_text="Workday candidate account setup must be completed manually before automation can continue.",
                    details={
                        "provider": "workday",
                        "stage": "candidate_account_bootstrap",
                        "current_url": str(getattr(page, "url", "") or current_url),
                    },
                )

            if self._is_form_stage(page):
                return self._submit_form(page=page, resolver=resolver, context=context)

            break

        return self._blocked(
            "unsupported_widget",
            page,
            question_text="Unsupported Workday flow shape.",
            details={"current_url": current_url},
        )

    def _submit_form(self, *, page, resolver, context: AdapterContext) -> SubmitResult:
        steps: list[StepSnapshot] = []
        for _ in range(20):
            self._wait_for_render(page)
            confirmation = self._extract_confirmation(page)
            if confirmation:
                return SubmitResult(
                    status="submitted",
                    current_url=str(getattr(page, "url", "") or ""),
                    confirmation_payload=confirmation,
                    steps=steps,
                    adapter_name=self.adapter_name,
                )
            if self._has_email_verification_gate(page):
                return self._blocked(
                    "email_verification_required",
                    page,
                    question_text="Enter verification code",
                    field_name="email_verification",
                    field_type="verification_code",
                    details={"provider": "workday", "stage": "email_verification"},
                    steps=steps,
                )
            if self._has_account_gate(page):
                return self._blocked(
                    "candidate_account_bootstrap_required",
                    page,
                    question_text="Workday candidate account setup must be completed manually before automation can continue.",
                    details={"provider": "workday", "stage": "candidate_account_bootstrap"},
                    steps=steps,
                )
            if not self._is_form_stage(page):
                break
            blocker, filled_count = self._fill_required_fields(page=page, resolver=resolver, context=context, steps=steps)
            if blocker is not None:
                return blocker
            action = self._next_form_action(page)
            if not action:
                if filled_count == 0:
                    return self._blocked(
                        "manual_checkpoint_required",
                        page,
                        question_text="Workday application form reached, but no supported next action was detected.",
                        details={
                            "checkpoint": "workday_application_form",
                            "checkpoint_label": "Workday application form",
                            "current_url": str(getattr(page, "url", "") or ""),
                        },
                        steps=steps,
                    )
                self._wait(page, 1000)
                continue
            if not self._click_navigation(page, action):
                return self._blocked(
                    "unsupported_widget",
                    page,
                    question_text="Could not activate the next Workday form action.",
                    details={"action": action, "current_url": str(getattr(page, "url", "") or "")},
                    steps=steps,
                )
            steps.append(
                StepSnapshot(
                    step_key=f"workday:navigation:{action}",
                    step_label=f"Advance Workday form via {action}",
                    status="completed",
                    answer_source="deterministic",
                    answer_value=action,
                )
            )
        confirmation = self._extract_confirmation(page)
        if confirmation:
            return SubmitResult(
                status="submitted",
                current_url=str(getattr(page, "url", "") or ""),
                confirmation_payload=confirmation,
                steps=steps,
                adapter_name=self.adapter_name,
            )
        return self._blocked(
            "manual_checkpoint_required",
            page,
            question_text="Workday application form reached, but automated field support is incomplete.",
            details={
                "checkpoint": "workday_application_form",
                "checkpoint_label": "Workday application form",
                "current_url": str(getattr(page, "url", "") or ""),
            },
            steps=steps,
        )

    def _workday_action_url(self, page, *, action: str) -> str:
        extractor = getattr(page, "extract_workday_action_url", None)
        if callable(extractor):
            candidate = str(extractor(action) or "").strip()
            if candidate:
                return candidate
        if not hasattr(page, "evaluate"):
            return ""
        action_map = {
            "apply": {"automationId": "adventureButton", "label": "Apply"},
            "apply_manually": {"automationId": "applyManually", "label": "Apply Manually"},
        }
        meta = action_map.get(action)
        if meta is None:
            return ""
        try:
            candidate = page.evaluate(
                """
                ({ automationId, label }) => {
                  const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                  const anchors = Array.from(document.querySelectorAll('a[href]'));
                  const exact = anchors.find((anchor) => (anchor.getAttribute('data-automation-id') || '') === automationId);
                  if (exact?.href) return exact.href;
                  const fallback = anchors.find((anchor) => normalize(anchor.innerText).includes(normalize(label)));
                  return fallback?.href || '';
                }
                """,
                meta,
            )
        except Exception:
            return ""
        raw = str(candidate or "").strip()
        if not raw:
            return ""
        return urljoin(str(getattr(page, "url", "") or ""), raw)

    def _is_public_job_page(self, page) -> bool:
        current_url = str(getattr(page, "url", "") or "").lower()
        text = self._page_text(page).lower()
        return "/apply" not in current_url and "apply" in text and "careers at" in text

    def _is_start_application_page(self, page) -> bool:
        current_url = str(getattr(page, "url", "") or "").lower()
        text = self._page_text(page).lower()
        return "/apply" in current_url and any(marker in text for marker in _START_APPLICATION_MARKERS)

    def _has_account_gate(self, page) -> bool:
        text = self._page_text(page).lower()
        current_url = str(getattr(page, "url", "") or "").lower()
        if "/login" in current_url or "/register" in current_url:
            return True
        return (
            "sign in with email" in text
            or ("email address*" in text and "password*" in text)
            or "create account" in text
            or "password requirements:" in text
        )

    def _has_email_verification_gate(self, page) -> bool:
        text = self._page_text(page).lower()
        if "sign in with email" in text or ("email address*" in text and "password*" in text):
            return False
        has_verification_text = (
            "enter verification code" in text
            or "enter code" in text
            or "verification code" in text
            or "we sent a verification code" in text
        )
        if not has_verification_text:
            return False
        if hasattr(page, "evaluate"):
            try:
                detected = bool(
                    page.evaluate(
                        """
                        () => {
                          const root = document.querySelector("#mainContent") || document.body;
                          if (!root) return false;
                          const text = (root.innerText || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                          const hasVerificationText =
                            text.includes('enter verification code') ||
                            text.includes('enter code') ||
                            text.includes('verification code') ||
                            text.includes('we sent a verification code');
                          const hasVerificationInput = Boolean(
                            root.querySelector("input[data-automation-id='verificationCode'], input[inputmode='numeric'], input[type='tel']")
                          );
                          return hasVerificationText && hasVerificationInput;
                        }
                        """
                    )
                )
                if detected:
                    return True
            except Exception:
                pass
        return "resend code" in text or "submit" in text

    def _is_loading_application_shell(self, page) -> bool:
        text = self._page_text_once(page).lower()
        return "loading" in text and any(marker in text for marker in _FORM_STAGE_MARKERS)

    def _has_application_form_widgets(self, page) -> bool:
        helper = getattr(page, "has_workday_form_widgets", None)
        if callable(helper):
            return bool(helper())
        if hasattr(page, "evaluate"):
            try:
                return bool(
                    page.evaluate(
                        """
                        () => {
                          const root = document.querySelector("#mainContent") || document.body;
                          if (!root) return false;
                          const selectors = [
                            "input[data-automation-id]",
                            "textarea[data-automation-id]",
                            "select[data-automation-id]",
                            "[data-automation-id='file-upload-input-ref']",
                            "[data-automation-id='bottom-navigation-next-button']",
                            "[data-automation-id='bottom-navigation-save-button']",
                            "[data-automation-id='bottom-navigation-continue-button']",
                            "[data-automation-id='bottom-navigation-review-button']",
                            "[data-automation-id='pageFooterNextButton']",
                            "[data-automation-id='pageFooterBackButton']",
                          ];
                          return selectors.some((selector) => root.querySelector(selector));
                        }
                        """
                    )
                )
            except Exception:
                return False
        return False

    def _is_form_stage(self, page) -> bool:
        if self._is_loading_application_shell(page):
            return False
        text = self._page_text(page).lower()
        return (
            any(marker in text for marker in _FORM_STAGE_MARKERS)
            and not self._has_account_gate(page)
            and self._has_application_form_widgets(page)
        )

    def _extract_confirmation(self, page) -> dict[str, object]:
        text = self._page_text(page).lower()
        if any(marker in text for marker in _CONFIRMATION_MARKERS):
            return {
                "message": "Application submitted",
                "url": str(getattr(page, "url", "") or ""),
                "source": "workday",
            }
        return {}

    def _page_text(self, page) -> str:
        locator_factory = getattr(page, "locator", None)
        if callable(locator_factory):
            for _ in range(3):
                try:
                    text = str(locator_factory("body").inner_text(timeout=1000) or "")
                    if text.strip():
                        return text
                except Exception:
                    pass
                self._wait(page, 1000)
        content = getattr(page, "content", None)
        if callable(content):
            try:
                return self._sanitize_html_text(str(content() or ""))
            except Exception:
                return ""
        return ""

    def _page_text_once(self, page) -> str:
        locator_factory = getattr(page, "locator", None)
        if callable(locator_factory):
            try:
                return str(locator_factory("body").inner_text(timeout=1000) or "")
            except Exception:
                pass
        content = getattr(page, "content", None)
        if callable(content):
            try:
                return self._sanitize_html_text(str(content() or ""))
            except Exception:
                return ""
        return ""

    def _sanitize_html_text(self, html: str) -> str:
        if not html:
            return ""
        text = re.sub(r"(?is)<script.*?>.*?</script>", " ", html)
        text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        text = text.replace("&nbsp;", " ")
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    def _wait_for_render(self, page) -> None:
        last_form_signature: tuple[tuple[str, ...], ...] | None = None
        stable_form_observations = 0
        for _ in range(45):
            current_url = str(getattr(page, "url", "") or "").lower()
            text = self._page_text_once(page).lower()
            if "/apply/applymanually" in current_url:
                if self._has_account_gate(page) or self._has_email_verification_gate(page) or self._extract_confirmation(page):
                    return
                if self._is_form_stage(page):
                    signature = self._form_content_signature(page)
                    if signature:
                        if signature == last_form_signature:
                            stable_form_observations += 1
                        else:
                            last_form_signature = signature
                            stable_form_observations = 0
                        if stable_form_observations >= 2:
                            return
                self._wait(page, 1000)
                continue
            if "/apply" in current_url:
                if any(marker in text for marker in _START_APPLICATION_MARKERS) or self._workday_action_url(page, action="apply_manually"):
                    return
                if self._has_account_gate(page) or self._has_email_verification_gate(page) or self._extract_confirmation(page):
                    return
                if self._is_form_stage(page):
                    signature = self._form_content_signature(page)
                    if signature:
                        if signature == last_form_signature:
                            stable_form_observations += 1
                        else:
                            last_form_signature = signature
                            stable_form_observations = 0
                        if stable_form_observations >= 2:
                            return
                self._wait(page, 1000)
                continue
            if self._workday_action_url(page, action="apply") or self._is_public_job_page(page):
                return
            if text and "loading" not in text and "follow us" not in text:
                return
            self._wait(page, 1000)

    def _form_content_signature(self, page) -> tuple[tuple[str, ...], ...]:
        fields = self._extract_fields(page)
        if fields:
            return tuple(
                sorted(
                    (
                        str(field.get("field_name") or ""),
                        str(field.get("field_type") or ""),
                        "required" if bool(field.get("required")) else "optional",
                    )
                    for field in fields
                )
            )
        if not hasattr(page, "evaluate"):
            action = self._next_form_action(page)
            return (("navigation", action),) if action == "submit" else ()
        try:
            review_ready = bool(
                page.evaluate(
                    """
                    () => {
                      const visible = (el) => {
                        if (!el) return false;
                        const style = window.getComputedStyle(el);
                        const rect = el.getBoundingClientRect();
                        return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
                      };
                      const heading = Array.from(document.querySelectorAll('h1, h2, h3'))
                        .filter(visible)
                        .map((el) => (el.textContent || '').trim().toLowerCase())
                        .find((text) => text === 'review');
                      const submit = Array.from(document.querySelectorAll('button'))
                        .filter(visible)
                        .some((el) => /submit application/i.test(el.textContent || ''));
                      return Boolean(heading && submit);
                    }
                    """
                )
            )
            return (("navigation", "submit"),) if review_ready else ()
        except Exception:
            return ()

    def _extract_fields(self, page) -> list[dict[str, object]]:
        extractor = getattr(page, "extract_workday_fields", None)
        if callable(extractor):
            return list(extractor())
        if not hasattr(page, "evaluate"):
            return []
        try:
            return list(
                page.evaluate(
                    """
                    () => {
                      const visible = (el) => {
                        if (!el) return false;
                        const style = window.getComputedStyle(el);
                        const rect = el.getBoundingClientRect();
                        return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
                      };
                      const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim();
                      const questionTextFor = (el) => {
                        const id = el.getAttribute('id') || '';
                        const fieldset = el.closest('fieldset');
                        if (fieldset) {
                          const legendNode = fieldset.querySelector('legend, [data-automation-id="richText"], [id^="rich-label"], [id^="checkbox-group-label"]');
                          const legendText = normalize(legendNode?.textContent || '');
                          if (legendText) return legendText;
                        }
                        const direct = id ? document.querySelector(`label[for="${id}"]`) : null;
                        if (direct) return normalize(direct.textContent);
                        const label = el.closest('label');
                        if (label) return normalize(label.textContent);
                        const row = el.closest('[data-automation-id="formField"], [data-automation-id="multiselectInputContainer"], .css-175oi2r');
                        if (row) {
                          const labelNode = row.querySelector('[data-automation-id="formLabel"], legend, label, span');
                          if (labelNode) return normalize(labelNode.textContent);
                        }
                        return normalize(el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.getAttribute('name'));
                      };
                      const fields = [];
                      let counter = 0;
                      const choiceVisible = (el) => {
                        if (visible(el)) return true;
                        const id = el.getAttribute('id') || '';
                        const direct = id ? document.querySelector(`label[for="${id}"]`) : null;
                        return visible(direct) || visible(el.closest('label')) || visible(el.closest('[data-automation-id^="formField-"]'));
                      };
                      const pushField = (el, fieldType, extra = {}) => {
                        const allowHidden = extra.allowHidden === true;
                        if ((!allowHidden && !visible(el)) || el.disabled) return;
                        counter += 1;
                        const marker = `jobhunter-workday-${counter}`;
                        el.setAttribute('data-jobhunter-field-index', marker);
                        let currentValue = '';
                        if (fieldType === 'checkbox') {
                          currentValue = el.checked ? 'Yes' : '';
                        } else if (fieldType === 'listbox-button') {
                          currentValue = normalize(el.innerText || el.textContent || el.getAttribute('value') || '');
                        } else if (fieldType === 'select-one') {
                          currentValue = normalize(el.value || el.textContent || '');
                        } else if (fieldType === 'file') {
                          currentValue = normalize(el.value || '');
                        } else {
                          currentValue = normalize(el.value || '');
                        }
                        fields.push({
                          selector: `[data-jobhunter-field-index="${marker}"]`,
                          field_name: el.getAttribute('name') || el.getAttribute('id') || questionTextFor(el),
                          field_type: fieldType,
                          question_text: questionTextFor(el),
                          required: extra.required === true || el.required || el.getAttribute('aria-required') === 'true',
                          current_value: currentValue,
                        });
                      };
                      const pushChoiceInputs = (inputs, type, root = null) => {
                        inputs = Array.from(inputs || []).filter((el) => choiceVisible(el) && !el.disabled);
                        if (!inputs.length) return;
                        const first = inputs[0];
                        const groupLabel = normalize(
                          root?.querySelector('legend')?.textContent ||
                          root?.getAttribute?.('aria-label') ||
                          first.closest('[data-automation-id="formField"]')?.querySelector('[data-automation-id="formLabel"]')?.textContent ||
                          questionTextFor(first)
                        );
                        const required =
                          inputs.some((el) => el.required || el.getAttribute('aria-required') === 'true') ||
                          root?.getAttribute?.('aria-required') === 'true' ||
                          first.closest('[aria-required="true"]') !== null;
                        const options = inputs.map((el) => {
                          counter += 1;
                          const marker = `jobhunter-workday-${counter}`;
                          el.setAttribute('data-jobhunter-field-index', marker);
                          const id = el.getAttribute('id') || '';
                          const direct = id ? document.querySelector(`label[for="${id}"]`) : null;
                          const label = normalize(direct?.textContent || el.closest('label')?.textContent || el.getAttribute('value') || '');
                          return {
                            selector: `[data-jobhunter-field-index="${marker}"]`,
                            value: normalize(el.getAttribute('value') || ''),
                            label,
                            checked: !!el.checked,
                          };
                        });
                        fields.push({
                          selector: options[0]?.selector || '',
                          field_name: first.getAttribute('name') || groupLabel,
                          field_type: type === 'radio' ? 'radio-group' : 'checkbox-group',
                          question_text: groupLabel,
                          required,
                          current_value: options.filter((opt) => opt.checked).map((opt) => opt.label || opt.value).join(', '),
                          options,
                        });
                      };
                      const pushChoiceGroup = (root, type) => {
                        if (!visible(root)) return;
                        const inputs = root.querySelectorAll(`input[type="${type}"]`);
                        pushChoiceInputs(inputs, type, root);
                      };

                      for (const el of Array.from(document.querySelectorAll('input, textarea, select'))) {
                        const type = (el.getAttribute('type') || '').toLowerCase();
                        if (type === 'hidden') continue;
                        if (type === 'radio' || type === 'checkbox') continue;
                        if (type === 'file') {
                          const uploadContainer = el.closest('[data-automation-id="attachments-FileUpload"]');
                          const row = el.closest('[data-automation-id^="formField-"]') || uploadContainer;
                          if (!row || !visible(row)) continue;
                          const required =
                            row.querySelector('abbr') !== null ||
                            uploadContainer?.getAttribute('aria-required') === 'true' ||
                            row.querySelector('[data-automation-id="inputAlert"]') !== null;
                          pushField(el, 'file', { allowHidden: true, required });
                          continue;
                        }
                        if (el.tagName.toLowerCase() === 'select') {
                          pushField(el, 'select-one');
                          continue;
                        }
                        if (el.getAttribute('role') === 'combobox') {
                          pushField(el, 'select-one');
                          continue;
                        }
                        if (!['', 'text', 'email', 'tel', 'number'].includes(type)) continue;
                        pushField(el, 'text');
                      }

                      for (const button of Array.from(document.querySelectorAll('button[aria-haspopup="listbox"]'))) {
                        const row = button.closest('[data-automation-id^="formField-"]');
                        if (!row || !visible(button)) continue;
                        const fieldset = button.closest('fieldset');
                        const groupLabel = normalize(
                          fieldset?.querySelector('legend, [data-automation-id="richText"], [id^="rich-label"], [id^="checkbox-group-label"]')?.textContent ||
                          questionTextFor(button)
                        );
                        const hiddenInput = row.querySelector('input[type="text"], input[type="hidden"]');
                        const selectedItem = row.querySelector('[data-automation-id="selectedItem"] [data-automation-id="promptOption"], [data-automation-id="selectedItem"]');
                        const promptInstruction = row.querySelector('[data-automation-id="promptAriaInstruction"]');
                        let ariaLabelValue = normalize(button.getAttribute('aria-label') || '');
                        const normalizedGroupLabel = normalize(groupLabel.replace(/\\*+$/, ''));
                        if (ariaLabelValue && normalizedGroupLabel) {
                          const lowerAria = ariaLabelValue.toLowerCase();
                          const lowerLabel = normalizedGroupLabel.toLowerCase();
                          if (lowerAria.startsWith(lowerLabel)) {
                            ariaLabelValue = normalize(ariaLabelValue.slice(normalizedGroupLabel.length));
                          }
                        }
                        ariaLabelValue = normalize(ariaLabelValue.replace(/\brequired\b/gi, '').replace(/\bselect one\b/gi, ''));
                        const currentValue = normalize(
                          selectedItem?.textContent ||
                          button.innerText ||
                          ariaLabelValue ||
                          promptInstruction?.textContent ||
                          hiddenInput?.value ||
                          ''
                        );
                        pushField(button, 'listbox-button', {
                          required:
                            button.getAttribute('aria-required') === 'true' ||
                            button.getAttribute('aria-label')?.toLowerCase().includes('required') ||
                            row.querySelector('abbr') !== null,
                        });
                        fields[fields.length - 1].question_text = groupLabel;
                        fields[fields.length - 1].current_value = currentValue;
                      }

                      for (const fieldset of Array.from(document.querySelectorAll('fieldset'))) {
                        pushChoiceGroup(fieldset, 'radio');
                        pushChoiceGroup(fieldset, 'checkbox');
                      }
                      for (const type of ['radio', 'checkbox']) {
                        const grouped = new Map();
                        for (const el of Array.from(document.querySelectorAll(`input[type="${type}"]`))) {
                          if (!choiceVisible(el) || el.disabled) continue;
                          if (el.closest('fieldset')) continue;
                          const name = normalize(el.getAttribute('name') || questionTextFor(el) || `ungrouped-${type}`);
                          if (!name) continue;
                          const key = `${type}:${name}`;
                          if (!grouped.has(key)) grouped.set(key, []);
                          grouped.get(key).push(el);
                        }
                        for (const inputs of grouped.values()) {
                          pushChoiceInputs(inputs, type, inputs[0]?.closest('[data-automation-id="formField"]'));
                        }
                      }
                      return fields;
                    }
                    """
                )
            )
        except Exception:
            return []

    def _fill_required_fields(self, *, page, resolver, context: AdapterContext, steps: list[StepSnapshot]) -> tuple[SubmitResult | None, int]:
        filled_count = 0
        consent_blocker, consent_filled = self._fill_terms_consent_checkbox(page=page, steps=steps)
        if consent_blocker is not None:
            return consent_blocker, filled_count
        filled_count += consent_filled
        for field in self._extract_fields(page):
            question_text = str(field.get("question_text") or field.get("field_name") or "").strip()
            field_name = str(field.get("field_name") or "")
            field_type = str(field.get("field_type") or "text")
            required = bool(field.get("required", True))
            current_value = self._normalized_current_value(field_type=field_type, current_value=field.get("current_value"))
            if not required:
                continue
            force_refresh = self._should_refresh_prefilled_value(field_name=field_name, question_text=question_text)
            if current_value and not force_refresh:
                continue
            if field_type == "file":
                upload_path = context.cover_letter_pdf_path if "cover" in question_text.lower() else context.resume_pdf_path
                try:
                    self._set_field(page, field, upload_path)
                except Exception:
                    return (
                        self._blocked(
                            "unsupported_widget",
                            page,
                            question_text=question_text,
                            field_name=field_name,
                            field_type=field_type,
                            details={"upload_path": upload_path},
                            steps=steps,
                        ),
                        filled_count,
                    )
                steps.append(
                    StepSnapshot(
                        step_key=f"workday:upload:{field_name or question_text}",
                        step_label="Upload Workday document",
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
                resolution = resolver.resolve_for_portal(
                    portal=self.adapter_name,
                    question_text=question_text,
                    field_name=field_name,
                    field_type=field_type,
                )
            except ResolutionError as exc:
                return (
                    self._blocked(
                        exc.blocker.reason,
                        page,
                        question_text=question_text,
                        field_name=field_name,
                        field_type=field_type,
                        details=exc.blocker.details,
                        steps=steps,
                    ),
                    filled_count,
                )
            if current_value and force_refresh and self._is_effectively_same_value(
                field_name=field_name,
                current_value=current_value,
                desired_value=resolution.answer,
            ):
                continue
            try:
                self._set_field(page, field, resolution.answer)
            except Exception:
                if field_type in {"listbox-button", "radio-group", "checkbox-group"}:
                    checkpoint = (
                        "workday_required_listbox"
                        if field_type == "listbox-button"
                        else "workday_required_choice"
                    )
                    checkpoint_label = (
                        "Workday required dropdown"
                        if field_type == "listbox-button"
                        else "Workday required choice"
                    )
                    return (
                        self._blocked(
                            "manual_checkpoint_required",
                            page,
                            question_text=question_text,
                            field_name=field_name,
                            field_type=field_type,
                            details={
                                "checkpoint": checkpoint,
                                "checkpoint_label": checkpoint_label,
                                "field_name": field_name,
                                "question_text": question_text,
                                "expected_answer": resolution.answer,
                                "current_url": str(getattr(page, "url", "") or ""),
                            },
                            steps=steps,
                        ),
                        filled_count,
                    )
                return (
                    self._blocked(
                        "unsupported_widget",
                        page,
                        question_text=question_text,
                        field_name=field_name,
                        field_type=field_type,
                        details={"answer": resolution.answer},
                        steps=steps,
                    ),
                    filled_count,
                )
            steps.append(
                StepSnapshot(
                    step_key=f"workday:field:{field_name or question_text}",
                    step_label="Fill Workday required field",
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

    def _fill_terms_consent_checkbox(self, *, page, steps: list[StepSnapshot]) -> tuple[SubmitResult | None, int]:
        if not hasattr(page, "locator"):
            return None, 0
        try:
            locator = page.locator("input[name='acceptTermsAndAgreements'][aria-required='true']").first
            if locator.count() == 0 or locator.is_checked():
                return None, 0
            locator.check(force=True)
            self._wait(page, 250)
            if not locator.is_checked():
                raise RuntimeError("consent checkbox was not checked")
        except Exception:
            return (
                self._blocked(
                    "manual_checkpoint_required",
                    page,
                    question_text="Yes, I have read and consent to the terms and conditions*",
                    field_name="acceptTermsAndAgreements",
                    field_type="checkbox",
                    details={
                        "checkpoint": "workday_required_consent",
                        "checkpoint_label": "Workday terms and conditions consent",
                        "expected_answer": "Yes",
                    },
                    steps=steps,
                ),
                0,
            )
        steps.append(
            StepSnapshot(
                step_key="workday:field:acceptTermsAndAgreements",
                step_label="Fill Workday required field",
                status="completed",
                field_name="acceptTermsAndAgreements",
                field_type="checkbox",
                question_text="Yes, I have read and consent to the terms and conditions*",
                answer_source="capability:workday:consent_required:safe_autofill_if_single_option",
                answer_value="Yes",
            )
        )
        return None, 1

    def _should_refresh_prefilled_value(self, *, field_name: str, question_text: str) -> bool:
        normalized_field = field_name.strip().lower()
        normalized_question = question_text.strip().lower()
        return (
            normalized_field == "country"
            or "countryphonecode" in normalized_field
            or normalized_field == "phonenumber"
            or normalized_question in {"country", "country*"}
            or "country phone code" in normalized_question
        )

    def _is_effectively_same_value(self, *, field_name: str, current_value: str, desired_value: str) -> bool:
        normalized_field = field_name.strip().lower()
        current = current_value.strip().lower()
        desired = desired_value.strip().lower()
        if normalized_field == "country":
            return current == desired or _canonical_country(current) == _canonical_country(desired)
        if "countryphonecode" in normalized_field:
            current_digits = "".join(ch for ch in current if ch.isdigit())
            desired_digits = "".join(ch for ch in desired if ch.isdigit())
            return bool(current_digits and desired_digits and current_digits == desired_digits)
        if normalized_field == "phonenumber":
            current_digits = "".join(ch for ch in current if ch.isdigit())
            desired_digits = "".join(ch for ch in desired if ch.isdigit())
            return bool(current_digits and desired_digits and current_digits == desired_digits)
        return current == desired

    def _normalized_current_value(self, *, field_type: str, current_value: object) -> str:
        raw = str(current_value or "").strip()
        if field_type in {"select-one", "listbox-button"} and raw.lower() in _EMPTY_SELECT_VALUES:
            return ""
        return raw

    def _normalize_option_text(self, value: str) -> str:
        return " ".join((value or "").strip().lower().split())

    def _set_field(self, page, field: dict[str, object], value: str) -> None:
        setter = getattr(page, "set_workday_field", None)
        if callable(setter):
            setter(field, value)
            return
        selector = str(field.get("selector") or "")
        field_type = str(field.get("field_type") or "text")
        if not selector or not hasattr(page, "locator"):
            raise RuntimeError("missing selector")
        if field_type == "file":
            page.locator(selector).first.set_input_files(value)
            self._wait(page, 500)
            return
        if field_type == "text":
            page.locator(selector).first.fill(value)
            self._wait(page, 200)
            return
        if field_type == "listbox-button":
            locator = page.locator(selector).first
            try:
                locator.click(force=True)
                self._wait(page, 250)
                option_selectors = '[role="option"], [data-automation-id="menuItem"], [data-automation-id="promptOption"]'
                options = page.locator(option_selectors)
                normalized_target = self._normalize_option_text(value)
                exact_locator = None
                fuzzy_locator = None
                for index in range(options.count()):
                    candidate = options.nth(index)
                    candidate_text = self._normalize_option_text(candidate.inner_text())
                    if not candidate_text:
                        continue
                    if candidate_text == normalized_target:
                        exact_locator = candidate
                        break
                    if normalized_target in candidate_text or candidate_text in normalized_target:
                        fuzzy_locator = fuzzy_locator or candidate
                option_locator = exact_locator or fuzzy_locator
                if option_locator is None:
                    text_locator = page.locator(f'text="{value}"').first
                    if text_locator.count() > 0:
                        option_locator = text_locator
                if option_locator is not None:
                    option_locator.click(force=True)
                    self._wait(page, 400)
                    current_value = self._normalized_current_value(
                        field_type=field_type,
                        current_value=page.locator(selector).first.inner_text(),
                    )
                    if self._normalize_option_text(current_value) != normalized_target:
                        raise RuntimeError("listbox selected unexpected value")
                    self._wait(page, 400)
                    return
                keyboard = getattr(page, "keyboard", None)
                if keyboard is None:
                    sibling_input = page.locator(f"{selector} + input[type=\"text\"]").first
                    sibling_input.fill(value)
                    self._wait(page, 150)
                    sibling_input.press("ArrowDown")
                    self._wait(page, 150)
                    sibling_input.press("Enter")
                    self._wait(page, 400)
                    return
                try:
                    keyboard.press("Home")
                    self._wait(page, 100)
                    for char in value:
                        if char.isalnum():
                            keyboard.press(char.upper() if len(char) == 1 else char)
                            self._wait(page, 50)
                    keyboard.press("ArrowDown")
                    self._wait(page, 100)
                    keyboard.press("Enter")
                    self._wait(page, 400)
                    return
                except Exception:
                    sibling_input = page.locator(f"{selector} + input[type=\"text\"]").first
                    sibling_input.fill(value)
                    self._wait(page, 150)
                    sibling_input.press("ArrowDown")
                    self._wait(page, 150)
                    sibling_input.press("Enter")
                    self._wait(page, 400)
                    return
            except Exception as exc:
                raise RuntimeError("listbox selection failed") from exc
        if field_type == "checkbox":
            lowered = value.strip().lower()
            locator = page.locator(selector).first
            if lowered in {"yes", "true", "1", "checked"}:
                locator.check(force=True)
            else:
                locator.uncheck(force=True)
            self._wait(page, 200)
            return
        if field_type in {"radio-group", "checkbox-group"}:
            options = field.get("options") or []
            if not isinstance(options, list):
                raise RuntimeError("missing options")
            target = value.strip().lower()
            for option in options:
                if not isinstance(option, dict):
                    continue
                label = str(option.get("label") or option.get("value") or "").strip().lower()
                if label == target or target in label or label in target:
                    option_selector = str(option.get("selector") or "")
                    option_locator = page.locator(option_selector).first
                    option_id = None
                    try:
                        option_id = option_locator.get_attribute("id")
                    except Exception:
                        option_id = None
                    if option_id:
                        try:
                            page.locator(f'label[for="{option_id}"]').first.click(force=True)
                            self._wait(page, 200)
                            if option_locator.is_checked():
                                return
                        except Exception:
                            pass
                    try:
                        option_locator.check(force=True)
                        self._wait(page, 200)
                        if option_locator.is_checked():
                            return
                    except Exception:
                        pass
                    try:
                        option_locator.click(force=True)
                        self._wait(page, 200)
                        checked = False
                        try:
                            checked = option_locator.is_checked()
                        except Exception:
                            checked = str(option_locator.get_attribute("aria-checked") or "").lower() == "true"
                        if checked:
                            return
                    except Exception:
                        pass
                    raise RuntimeError("option click failed")
            raise RuntimeError("option not found")
        if field_type == "select-one":
            locator = page.locator(selector).first
            try:
                locator.select_option(label=value)
                self._wait(page, 200)
                return
            except Exception:
                pass
            try:
                locator.select_option(value=value)
                self._wait(page, 200)
                return
            except Exception:
                pass
            try:
                locator.click(force=True)
                locator.fill(value)
                keyboard = getattr(page, "keyboard", None)
                if keyboard is not None:
                    keyboard.press("ArrowDown")
                    keyboard.press("Enter")
                self._wait(page, 400)
                return
            except Exception as exc:
                raise RuntimeError("select failed") from exc
        raise RuntimeError(f"unsupported field type: {field_type}")

    def _next_form_action(self, page) -> str:
        extractor = getattr(page, "extract_workday_navigation_action", None)
        if callable(extractor):
            return str(extractor() or "")
        text = self._page_text(page).lower()
        candidates = (
            ("submit", "Submit Application", ("bottom-navigation-submit-button", "pageFooterNextButton")),
            ("submit", "Submit", ("bottom-navigation-submit-button", "pageFooterNextButton")),
            ("review", "Review", ("bottom-navigation-review-button", "pageFooterNextButton")),
            ("continue", "Continue", ("bottom-navigation-continue-button", "pageFooterNextButton")),
            ("next", "Next", ("bottom-navigation-next-button", "pageFooterNextButton")),
            ("save", "Save and Continue", ("bottom-navigation-save-button", "pageFooterNextButton")),
        )
        for action, label, automation_ids in candidates:
            if action == "submit" and "review" not in text:
                continue
            if any(self._has_button(page, automation_id=automation_id, label=label) for automation_id in automation_ids):
                return action
        return ""

    def _has_button(self, page, *, automation_id: str, label: str) -> bool:
        if not hasattr(page, "locator"):
            return False
        selectors = [
            f"[data-automation-id='{automation_id}']",
            f"button[aria-label='{label}']",
            "button",
            "[role='button']",
        ]
        for selector in selectors:
            try:
                locator = page.locator(selector)
                if selector.startswith("[data-automation-id=") or selector.startswith("button[aria-label="):
                    if locator.count() > 0:
                        if automation_id == "pageFooterNextButton":
                            exact_label = re.compile(rf"^\s*{re.escape(label)}\s*$", re.IGNORECASE)
                            return locator.filter(has_text=exact_label).count() > 0
                        return True
                    continue
                if locator.filter(has_text=label).count() > 0:
                    return True
            except Exception:
                continue
        return False

    def _click_navigation(self, page, action: str) -> bool:
        helper = getattr(page, "click_workday_navigation", None)
        if callable(helper) and helper(action):
            self._wait(page, 800)
            return True
        mapping = {
            "submit": (("bottom-navigation-submit-button", "pageFooterNextButton"), "Submit Application"),
            "review": (("bottom-navigation-review-button", "pageFooterNextButton"), "Review"),
            "continue": (("bottom-navigation-continue-button", "pageFooterNextButton"), "Continue"),
            "next": (("bottom-navigation-next-button", "pageFooterNextButton"), "Next"),
            "save": (("bottom-navigation-save-button", "pageFooterNextButton"), "Save and Continue"),
        }
        automation_ids, label = mapping.get(action, ((), ""))
        if not automation_ids:
            return False
        for candidate_label in ({label, "Submit"} if action == "submit" else {label}):
            for automation_id in automation_ids:
                if self._click_button(page, automation_id=automation_id, label=candidate_label):
                    self._wait(page, 1000)
                    return True
        return False

    def _click_button(self, page, *, automation_id: str, label: str) -> bool:
        if not hasattr(page, "locator"):
            return False
        selectors = [
            f"[data-automation-id='{automation_id}']",
            f"button[aria-label='{label}']",
            "button",
            "[role='button']",
            "a[role='button']",
        ]
        for selector in selectors:
            try:
                locator = page.locator(selector)
                if selector.startswith("[data-automation-id=") or selector.startswith("button[aria-label="):
                    if locator.count() == 0:
                        continue
                    if automation_id == "pageFooterNextButton":
                        exact_label = re.compile(rf"^\s*{re.escape(label)}\s*$", re.IGNORECASE)
                        candidate = locator.filter(has_text=exact_label).first
                    else:
                        candidate = locator.first
                    if candidate.count() == 0:
                        continue
                    candidate.click(force=True, timeout=2000)
                    return True
                candidate = locator.filter(has_text=label).first
                if candidate.count() == 0:
                    continue
                candidate.click(force=True, timeout=2000)
                return True
            except Exception:
                continue
        return False

    def _wait(self, page, milliseconds: int) -> None:
        waiter = getattr(page, "wait_for_timeout", None)
        if callable(waiter):
            waiter(milliseconds)

    def _blocked(
        self,
        reason: str,
        page,
        *,
        question_text: str,
        field_name: str = "target_url",
        field_type: str = "url",
        details: dict[str, object] | None = None,
        steps: list[StepSnapshot] | None = None,
    ) -> SubmitResult:
        return SubmitResult(
            status="blocked",
            current_url=str(getattr(page, "url", "") or ""),
            blocker=Blocker(
                reason=reason,
                question_text=question_text,
                field_name=field_name,
                field_type=field_type,
                details=details or {},
            ),
            steps=list(steps or []),
            adapter_name=self.adapter_name,
        )

    def complete_email_verification(
        self,
        *,
        page,
        code: str,
        steps: list,
        context: AdapterContext | None = None,
        resolver=None,
    ) -> SubmitResult:
        if hasattr(page, "fill_email_verification_code"):
            page.fill_email_verification_code(code)
        else:
            raise RuntimeError("Workday email verification requires a page helper to enter the code.")
        steps.append(
            StepSnapshot(
                step_key="workday:email_verification",
                step_label="Fill Workday email verification code",
                status="completed",
                field_name="email_verification",
                field_type="verification_code",
                question_text="Email verification code",
                answer_source="gmail",
                answer_value="redacted",
            )
        )
        if context is None:
            return self._blocked(
                "manual_checkpoint_required",
                page,
                question_text="Workday verification succeeded, but adapter context is missing for automated continuation.",
                details={
                    "checkpoint": "workday_application_form",
                    "checkpoint_label": "Workday application form",
                    "current_url": str(getattr(page, "url", "") or ""),
                },
                steps=steps,
            )
        return self._submit_form(page=page, resolver=resolver, context=context)
