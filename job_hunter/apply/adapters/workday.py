from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urljoin, urlparse

from job_hunter.apply.adapters.base import AdapterContext
from job_hunter.apply.resolver import ResolutionError
from job_hunter.apply.types import AnswerResolution, Blocker, StepSnapshot, SubmitResult

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
_US_STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri",
    "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey",
    "NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio",
    "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}


def _canonical_country(country: str) -> str:
    lowered = (country or "").strip().lower()
    if lowered in {"usa", "us", "u.s.", "u.s.a.", "united states", "united states of america"}:
        return "united states"
    return lowered


def _canonical_degree(degree: str) -> str:
    normalized = re.sub(r"[^a-z]", "", (degree or "").lower())
    if normalized in {"bachelor", "bachelors", "bachelorsdegree"}:
        return "bachelor"
    if normalized in {"master", "masters", "mastersdegree"}:
        return "master"
    if normalized in {"doctorate", "doctoral", "phd", "phddegree"}:
        return "doctorate"
    if normalized in {"associate", "associates", "associatesdegree"}:
        return "associate"
    return normalized


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
        # Workday often leaves stale or hidden controls in the DOM. Keep each
        # Playwright operation bounded so a failed selector becomes a persisted
        # checkpoint instead of holding an application run indefinitely.
        set_default_timeout = getattr(page, "set_default_timeout", None)
        if callable(set_default_timeout):
            try:
                set_default_timeout(5_000)
            except Exception:
                pass
        current_url = str(getattr(page, "url", "") or "")
        account_sign_in_attempted = False
        for _ in range(6):
            self._wait_for_render(page)
            self._accept_legal_notice(page)
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
            if "/apply" not in current_url.lower() and not apply_url:
                # Some tenants render the public job shell before its Apply
                # link hydrates. For a known Workday job URL, the manual
                # endpoint is stable and avoids treating that transient state
                # as an unsupported portal.
                direct_manual_url = self._direct_manual_apply_url(current_url)
                if direct_manual_url:
                    page.goto(direct_manual_url, wait_until="domcontentloaded")
                    current_url = str(getattr(page, "url", "") or direct_manual_url)
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
                if not account_sign_in_attempted:
                    account_sign_in_attempted = True
                    if self._sign_in_to_candidate_account(page=page, context=context):
                        current_url = str(getattr(page, "url", "") or current_url)
                        continue
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

    def _sign_in_to_candidate_account(self, *, page, context: AdapterContext) -> bool:
        """Use the local, host-scoped Workday credential only on an account gate."""
        profile = context.profile
        host = urlparse(str(getattr(page, "url", "") or "")).netloc.lower()
        credential = profile.workday_credential_for_host(host) if profile is not None and host else None
        if credential is None or not hasattr(page, "locator"):
            return False
        try:
            # Locate this outside a possible stale/hidden dialog. Workday
            # renders it as either an anchor or a button depending on tenant.
            existing_account = page.locator('[data-automation-id="signInLink"]').first
            # Copart renders the create-account shell before this link becomes
            # interactive. Wait for the existing-account control instead of
            # mistaking its duplicated email/password IDs for a sign-in form.
            for _ in range(36):
                if existing_account.count() > 0:
                    break
                self._wait(page, 250)
            if existing_account.count() > 0:
                try:
                    # Some tenants wire this through React without accepting a
                    # forced pointer click. Trigger its native button handler.
                    existing_account.evaluate("element => element.click()")
                except Exception:
                    existing_account.click(force=True)
                self._wait(page, 1250)

            # Create Account and Sign In reuse the same email/password field
            # IDs. Never populate credentials while the switch is still on
            # screen, otherwise a failed click can mutate the account-creation
            # form instead of logging in.
            for _ in range(36):
                if page.locator('[data-automation-id="signInLink"]').first.count() == 0:
                    break
                self._wait(page, 250)
            else:
                return False

            sign_in_form = page.locator('form[data-automation-id="signInFormo"]:visible').first
            scope = sign_in_form if sign_in_form.count() > 0 else self._sign_in_scope(page)
            email = scope.locator('input[data-automation-id="email"]:visible').first
            if email.count() == 0:
                # Some tenants initially show a create-account pane inside the
                # sign-in dialog. Switch it to the existing-account form before
                # looking for credentials.
                existing_account = scope.locator('a:has-text("Sign In"):visible').first
                if existing_account.count() == 0:
                    existing_account = scope.locator('button:has-text("Sign In"):visible').first
                if existing_account.count() > 0:
                    existing_account.click(force=True)
                    self._wait(page, 750)
                    scope = self._sign_in_scope(page)
                email_flow = scope.locator('button[data-automation-id="SignInWithEmailButton"]:visible').first
                if email_flow.count() == 0:
                    header_sign_in = page.locator('[data-automation-id="utilityButtonSignIn"]:visible').first
                    if header_sign_in.count() > 0:
                        header_sign_in.click(force=True)
                        self._wait(page, 500)
                    scope = self._sign_in_scope(page)
                    email_flow = scope.locator('button[data-automation-id="SignInWithEmailButton"]:visible').first
                if email_flow.count() > 0:
                    email_flow.click(force=True)
                    # Some Workday tenants take several seconds to replace the
                    # provider chooser with the email/password form.
                    self._wait(page, 1000)
            for _ in range(60):
                sign_in_form = page.locator('form[data-automation-id="signInFormo"]:visible').first
                scope = sign_in_form if sign_in_form.count() > 0 else self._sign_in_scope(page)
                email = scope.locator('input[data-automation-id="email"]:visible').first
                password = scope.locator('input[data-automation-id="password"]:visible').first
                if email.count() == 0:
                    email = scope.locator(
                        'input[type="email"]:visible, input[autocomplete="username"]:visible, input[name*="email" i]:visible'
                    ).first
                if password.count() == 0:
                    password = scope.locator('input[type="password"]:visible').first
                submit = scope.locator('[data-automation-id="click_filter"]:visible').first
                if submit.count() == 0:
                    submit = scope.locator('button[data-automation-id="signInSubmitButton"]:visible').first
                if submit.count() == 0:
                    submit = scope.locator('button:has-text("Sign In"):visible').first
                if email.count() > 0 and password.count() > 0 and submit.count() > 0:
                    break
                self._wait(page, 250)
            if email.count() == 0 or password.count() == 0 or submit.count() == 0:
                return False
            email.fill(credential.email)
            password.fill(credential.password)
            # Workday's visible action is often a div over a hidden submit
            # button. Use a normal click on the foreground control so tenant
            # pointer handlers run without touching a background account form.
            form = scope.locator('form[data-automation-id="signInFormo"]:visible').first
            try:
                submit.click()
            except Exception:
                try:
                    password.press("Enter")
                except Exception:
                    if form.count() > 0 and hasattr(form, "evaluate"):
                        form.evaluate("form => form.requestSubmit()")
                    else:
                        return False
            self._wait(page, 6000)
            return True
        except Exception:
            return False

    def _sign_in_scope(self, page):
        """Prefer the foreground sign-in dialog over a background create-account form."""
        dialog = page.locator('[data-automation-id="popUpDialog"]:visible').first
        try:
            if dialog.count() > 0:
                return dialog
        except Exception:
            pass
        return page

    def _submit_form(self, *, page, resolver, context: AdapterContext) -> SubmitResult:
        steps: list[StepSnapshot] = []
        last_form_signature: tuple[tuple[str, ...], ...] | None = None
        repeated_form_signature = 0
        transient_error_retries = 0
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
            if self._has_transient_error_page(page):
                if transient_error_retries >= 1 or not hasattr(page, "reload"):
                    return self._blocked(
                        "transient_portal_error",
                        page,
                        question_text="Workday returned a transient error page.",
                        details={"provider": "workday", "stage": "transient_error"},
                        steps=steps,
                    )
                transient_error_retries += 1
                page.reload(wait_until="domcontentloaded")
                self._wait(page, 1500)
                continue
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
            if self._has_assessment_stage(page):
                return self._blocked(
                    "assessment_required",
                    page,
                    question_text="Take Assessment",
                    field_name="workday_assessment",
                    field_type="assessment",
                    details={"provider": "workday", "stage": "take_assessment"},
                    steps=steps,
                )
            if not self._is_form_stage(page):
                break
            form_signature = self._form_content_signature(page)
            if form_signature and form_signature == last_form_signature:
                repeated_form_signature += 1
            else:
                last_form_signature = form_signature or None
                repeated_form_signature = 0
            if repeated_form_signature >= 2:
                invalid_fields = [
                    {
                        "field_name": str(field.get("field_name") or ""),
                        "field_type": str(field.get("field_type") or ""),
                        "question_text": str(field.get("question_text") or ""),
                    }
                    for field in self._extract_fields(page)
                    if bool(field.get("required")) and bool(field.get("invalid"))
                ]
                return self._blocked(
                    "manual_checkpoint_required",
                    page,
                    question_text="Workday did not advance after repeated attempts on the same form step.",
                    details={
                        "checkpoint": "workday_no_progress",
                        "checkpoint_label": "Workday form did not advance",
                        "invalid_fields": invalid_fields,
                        "current_url": str(getattr(page, "url", "") or ""),
                    },
                    steps=steps,
                )
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
            if not self._wait_for_navigation_progress(
                page,
                previous_signature=form_signature,
                previous_action=action,
            ):
                return self._blocked(
                    "manual_checkpoint_required",
                    page,
                    question_text="Workday did not render the next application step after the selected form action.",
                    details={
                        "checkpoint": "workday_navigation_timeout",
                        "checkpoint_label": "Workday form navigation did not complete",
                        "action": action,
                        "current_url": str(getattr(page, "url", "") or ""),
                    },
                    steps=steps,
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

    def _direct_manual_apply_url(self, url: str) -> str:
        """Build the standard manual path only for a concrete Workday job URL."""
        parsed = urlparse(url.strip())
        if "workdayjobs.com" not in parsed.netloc.lower() or "/job/" not in parsed.path.lower():
            return ""
        path = parsed.path.rstrip("/")
        if not path:
            return ""
        return parsed._replace(path=f"{path}/apply/applyManually", query="", fragment="").geturl()

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

    def _has_assessment_stage(self, page) -> bool:
        """Detect Workday's separate assessment checkpoint without opening it."""
        if not hasattr(page, "evaluate"):
            return False
        try:
            return bool(
                page.evaluate(
                    r"""
                    () => {
                      const visible = (el) => {
                        if (!el) return false;
                        const style = window.getComputedStyle(el);
                        const rect = el.getBoundingClientRect();
                        return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
                      };
                      const normalize = (value) => (value || '').replace(/\s+/g, ' ').trim().toLowerCase();
                      const activeStep = document.querySelector('[data-automation-id="progressBarActiveStep"]');
                      if (normalize(activeStep?.textContent) === 'take assessment') return true;
                      return Array.from(document.querySelectorAll('button'))
                        .some((button) => visible(button) && normalize(button.textContent) === 'take assessment');
                    }
                    """
                )
            )
        except Exception:
            return False

    def _has_transient_error_page(self, page) -> bool:
        text = self._page_text_once(page).lower()
        return "something went wrong" in text and "refresh the page" in text

    def _accept_legal_notice(self, page) -> bool:
        """Dismiss Workday's cookie notice when it obscures the application flow."""
        if not hasattr(page, "locator"):
            return False
        try:
            accept = page.locator(
                'button[data-automation-id="legalNoticeAcceptButton"]:visible'
            ).first
            if accept.count() == 0:
                return False
            accept.click(force=True)
            self._wait(page, 300)
            return True
        except Exception:
            return False

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
        # Workday may take several seconds to hydrate controls, but a form
        # that remains non-interactive for longer needs a persisted checkpoint
        # rather than an unbounded browser worker.
        for _ in range(20):
            current_url = str(getattr(page, "url", "") or "").lower()
            text = self._page_text_once(page).lower()
            if "/apply/applymanually" in current_url:
                if self._has_account_gate(page) or self._has_email_verification_gate(page) or self._extract_confirmation(page):
                    return
                if self._is_apply_flow_loading(page):
                    self._wait(page, 1000)
                    continue
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
                if self._is_apply_flow_loading(page):
                    self._wait(page, 1000)
                    continue
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

    def _wait_for_navigation_progress(
        self,
        page,
        *,
        previous_signature: tuple[tuple[str, ...], ...],
        previous_action: str,
    ) -> bool:
        """Wait only for an observable Workday transition after a footer click."""
        for _ in range(20):
            if self._extract_confirmation(page):
                return True
            if self._has_account_gate(page) or self._has_email_verification_gate(page):
                return True
            if self._is_form_stage(page):
                signature = self._form_content_signature(page)
                if signature and signature != previous_signature:
                    return True
                next_action = self._next_form_action(page)
                if next_action and next_action != previous_action:
                    return True
            self._wait(page, 1000)
        return False

    def _is_apply_flow_loading(self, page) -> bool:
        """Workday renders form controls before its loading veil is removed."""
        if not hasattr(page, "evaluate"):
            return False
        try:
            return bool(
                page.evaluate(
                    r"""
                    () => {
                      const el = document.querySelector('[data-automation-id="applyFlowLoadingPage"]');
                      if (!el) return false;
                      const style = window.getComputedStyle(el);
                      const rect = el.getBoundingClientRect();
                      return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
                    }
                    """
                )
            )
        except Exception:
            return False

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
                        const formField = el.closest('[data-automation-id^="formField-"]');
                        counter += 1;
                        const marker = `jobhunter-workday-${counter}`;
                        el.setAttribute('data-jobhunter-field-index', marker);
                        let currentValue = '';
                        if (fieldType === 'checkbox') {
                          currentValue = el.checked ? 'Yes' : '';
                        } else if (fieldType === 'prompt-input') {
                          const container = el.closest('[data-automation-id="multiSelectContainer"]');
                          currentValue = normalize(
                            container?.querySelector('[data-automation-id="selectedItem"]')?.textContent ||
                            container?.querySelector('[data-automation-id="promptSelectionLabel"]')?.textContent ||
                            el.value ||
                            ''
                          );
                        } else if (fieldType === 'listbox-button') {
                          currentValue = normalize(el.innerText || el.textContent || el.getAttribute('value') || '');
                        } else if (fieldType === 'select-one') {
                          currentValue = normalize(el.value || el.textContent || '');
                        } else if (fieldType === 'file') {
                          currentValue = normalize(el.value || '');
                        } else {
                          currentValue = normalize(el.value || '');
                        }
                        if (Object.prototype.hasOwnProperty.call(extra, 'currentValue')) {
                          currentValue = normalize(extra.currentValue || '');
                        }
                        fields.push({
                          selector: `[data-jobhunter-field-index="${marker}"]`,
                          field_name: el.getAttribute('name') || el.getAttribute('id') || questionTextFor(el),
                          field_type: fieldType,
                          question_text: questionTextFor(el),
                          required:
                            extra.required === true ||
                            el.required ||
                            el.getAttribute('aria-required') === 'true' ||
                            formField?.querySelector('abbr') !== null ||
                            formField?.querySelector('[data-automation-id="inputAlert"]') !== null,
                          current_value: currentValue,
                          container_id: extra.containerId || '',
                          invalid:
                            el.getAttribute('aria-invalid') === 'true' ||
                            formField?.querySelector('[data-automation-id="inputAlert"]') !== null,
                        });
                      };
                      const pushChoiceInputs = (inputs, type, root = null) => {
                        inputs = Array.from(inputs || []).filter((el) => choiceVisible(el) && !el.disabled);
                        if (!inputs.length) return;
                        const first = inputs[0];
                        const ancestorFieldset = root?.parentElement?.closest('fieldset');
                        const groupLabel = normalize(
                          root?.querySelector('legend')?.textContent ||
                          ancestorFieldset?.querySelector('legend')?.textContent ||
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
                          // Workday self-identification groups use a generic
                          // legend, so retain the inner fieldset ID as the
                          // stable semantic field name when there is no name.
                          field_name: first.getAttribute('name') || root?.getAttribute?.('id') || groupLabel,
                          field_type: type === 'radio' ? 'radio-group' : 'checkbox-group',
                          question_text: groupLabel,
                          required,
                          current_value: options.filter((opt) => opt.checked).map((opt) => opt.label || opt.value).join(', '),
                          options,
                        });
                      };
                      const pushChoiceGroup = (root, type) => {
                        if (!visible(root)) return;
                        // Workday nests a visual fieldset inside the semantic
                        // group fieldset. Only the innermost owner should emit
                        // the inputs, otherwise each option becomes a duplicate
                        // required group after React rerenders the form.
                        const inputs = Array.from(root.querySelectorAll(`input[type="${type}"]`))
                          .filter((el) => el.closest('fieldset') === root);
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
                          const uploadedNames = Array.from(
                            uploadContainer?.querySelectorAll('[data-automation-id="file-upload-item-name"]') || []
                          ).map((node) => normalize(node.textContent || '')).filter(Boolean).join(', ');
                          const required =
                            row.querySelector('abbr') !== null ||
                            uploadContainer?.getAttribute('aria-required') === 'true' ||
                            row.querySelector('[data-automation-id="inputAlert"]') !== null;
                          pushField(el, 'file', { allowHidden: true, required, currentValue: uploadedNames });
                          const fileField = fields[fields.length - 1];
                          fileField.question_text = normalize(row.querySelector('label')?.textContent || 'Resume/CV');
                          fileField.field_name = el.getAttribute('name') || el.getAttribute('id') || 'resumeAttachments';
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
                        const multiSelect = el.closest('[data-automation-id="multiSelectContainer"]');
                        if (multiSelect) {
                          pushField(el, 'prompt-input', {
                            required: el.getAttribute('aria-required') === 'true',
                            containerId: multiSelect.getAttribute('id') || '',
                          });
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
        fields = sorted(self._extract_fields(page), key=self._field_fill_priority)
        for field in fields:
            question_text = str(field.get("question_text") or field.get("field_name") or "").strip()
            field_name = str(field.get("field_name") or "")
            field_type = str(field.get("field_type") or "text")
            required = bool(field.get("required", True))
            current_value = self._normalized_current_value(field_type=field_type, current_value=field.get("current_value"))
            invalid = bool(field.get("invalid", False))
            if not required:
                continue
            force_refresh = self._should_refresh_prefilled_value(field_name=field_name, question_text=question_text)
            if current_value and not force_refresh and not invalid:
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
            resolution = self._signed_attestation_date_resolution(
                field_name=field_name,
                question_text=question_text,
            )
            if resolution is None:
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
            except Exception as exc:
                if field_type in {"prompt-input", "listbox-button", "radio-group", "checkbox-group"}:
                    checkpoint = (
                        "workday_required_prompt"
                        if field_type == "prompt-input"
                        else (
                            "workday_required_listbox"
                            if field_type == "listbox-button"
                            else "workday_required_choice"
                        )
                    )
                    checkpoint_label = (
                        "Workday required search selection"
                        if field_type == "prompt-input"
                        else (
                            "Workday required dropdown"
                            if field_type == "listbox-button"
                            else "Workday required choice"
                        )
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
                                "error": str(exc),
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
                        details={"answer": resolution.answer, "error": str(exc)},
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

    def _field_fill_priority(self, field: dict[str, object]) -> int:
        field_name = str(field.get("field_name") or "").strip().lower()
        if field_name == "country":
            return 0
        if field_name == "countryregion":
            return 1
        if "countryphonecode" in field_name:
            return 2
        return 1

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
            or "employment eligibility" in normalized_question
        )

    def _is_effectively_same_value(self, *, field_name: str, current_value: str, desired_value: str) -> bool:
        normalized_field = field_name.strip().lower()
        current = current_value.strip().lower()
        desired = desired_value.strip().lower()
        if desired in {"true", "1", "on"}:
            desired = "yes"
        elif desired in {"false", "0", "off"}:
            desired = "no"
        if normalized_field == "source":
            # Workday tenants use different source taxonomies. Treat only
            # explicitly LinkedIn-labelled options as equivalent to a
            # LinkedIn job-board answer; do not broaden other source values.
            return "linkedin" in current and "linkedin" in desired
        if normalized_field in {"phonetype", "devicetype", "phone-device-type"}:
            # Workday tenants label the same option as either "Mobile" or
            # "Mobile Phone". Both are the candidate's mobile device type.
            return desired == "mobile" and "mobile" in current
        if normalized_field == "ethnicity":
            # Tenants may suffix ethnicity options with a country label.
            # Preserve the explicit-negation guard to avoid matching "Not
            # Hispanic or Latino" for the affirmative answer.
            return desired in current and f"not {desired}" not in current
        if desired in {"yes", "no"}:
            # Some Workday tenants expand a binary answer with explanatory
            # text, e.g. "No - I am not related ...". The leading standalone
            # response is still the selected answer.
            return current == desired or re.match(rf"^{re.escape(desired)}(?:\s|[-:,(])", current) is not None
        if desired.startswith("__work_auth_us_"):
            return self._employment_eligibility_match_score(
                target=desired,
                candidate=current,
            ) > 0
        if normalized_field == "country":
            return current == desired or _canonical_country(current) == _canonical_country(desired)
        if "countryphonecode" in normalized_field:
            current_digits = "".join(ch for ch in current if ch.isdigit())
            desired_digits = "".join(ch for ch in desired if ch.isdigit())
            return bool(current_digits and desired_digits and current_digits == desired_digits)
        if normalized_field == "veteranstatus":
            normalized_current = "".join(ch for ch in current if ch.isalnum())
            normalized_desired = "".join(ch for ch in desired if ch.isalnum())
            if (
                "notaveteran" in normalized_current
                and "notaprotectedveteran" in normalized_desired
            ):
                return True
            return bool(normalized_current and normalized_current == normalized_desired)
        if normalized_field == "phonenumber":
            current_digits = "".join(ch for ch in current if ch.isdigit())
            desired_digits = "".join(ch for ch in desired if ch.isdigit())
            return bool(current_digits and desired_digits and current_digits == desired_digits)
        if self._is_us_state_equivalent(
            field_name=field_name,
            current_value=current_value,
            desired_value=desired_value,
        ):
            return True
        if field_name.strip().lower() == "degree":
            return _canonical_degree(current_value) == _canonical_degree(desired_value)
        return current == desired

    def _is_us_state_equivalent(self, *, field_name: str, current_value: str, desired_value: str) -> bool:
        if field_name.strip().lower() != "countryregion":
            return False
        expected_name = _US_STATE_NAMES.get(desired_value.strip().upper())
        return bool(expected_name and current_value.strip().lower() == expected_name.lower())

    def _normalized_current_value(self, *, field_type: str, current_value: object) -> str:
        raw = str(current_value or "").strip()
        if field_type in {"select-one", "listbox-button"} and raw.lower() in _EMPTY_SELECT_VALUES:
            return ""
        return raw

    def _normalize_option_text(self, value: str) -> str:
        return " ".join((value or "").strip().lower().split())

    def _prompt_selection_path(self, value: str, *, field: dict[str, object] | None = None) -> list[str]:
        """Split explicit hierarchies and normalize known Workday source trees."""
        path = [part.strip() for part in value.split(">")]
        path = [part for part in path if part] or [value.strip()]
        question = str((field or {}).get("question_text") or "").lower()
        if (
            len(path) == 1
            and path[0].strip().lower() == "linkedin"
            and "how did you hear" in question
        ):
            # Workday tenants commonly group LinkedIn beneath Job Sites
            # instead of presenting it as a top-level option.
            return ["Job Sites", "LinkedIn"]
        return path

    def _listbox_selection_path(self, value: str, *, field: dict[str, object] | None = None) -> list[str]:
        """Split hierarchical Workday listbox answers into selectable menu levels."""
        path = [part.strip() for part in value.split(">")]
        path = [part for part in path if part] or [value.strip()]
        question = str((field or {}).get("question_text") or "").lower()
        if (
            len(path) == 1
            and path[0].strip().lower() == "linkedin"
            and "how did you hear" in question
        ):
            return ["Job Sites", "LinkedIn"]
        return path

    def _prompt_multi_values(self, value: str) -> list[str]:
        """Allow answer rules to express a sequence of Workday multi-select choices."""
        values = [part.strip() for part in value.split("||")]
        return [part for part in values if part] or [value.strip()]

    def _should_use_other_school(
        self,
        page,
        field: dict[str, object],
        path_index: int,
        selection_path: list[str],
    ) -> bool:
        question = str(field.get("question_text") or field.get("field_name") or "").lower()
        if path_index != 0 or len(selection_path) != 1 or not any(token in question for token in ("school", "university")):
            return False
        page_text = self._page_text_once(page).lower()
        return "type other" in page_text and "school is not listed" in page_text

    def _listbox_option_match_score(self, *, field_name: str, target: str, candidate: str) -> int:
        normalized_target = self._normalize_option_text(target)
        normalized_candidate = self._normalize_option_text(candidate)
        if not normalized_target or not normalized_candidate:
            return 0
        eligibility_score = self._employment_eligibility_match_score(
            target=normalized_target,
            candidate=normalized_candidate,
        )
        if eligibility_score:
            return eligibility_score
        if normalized_candidate == normalized_target:
            return 3
        if f"not {normalized_target}" in normalized_candidate:
            # Avoid a partial match selecting the explicit negation of the
            # requested value, such as "Not Hispanic or Latino".
            return 0
        if self._is_us_state_equivalent(field_name=field_name, current_value=candidate, desired_value=target):
            return 2
        if self._is_effectively_same_value(
            field_name=field_name,
            current_value=candidate,
            desired_value=target,
        ):
            return 2
        if normalized_target in normalized_candidate or normalized_candidate in normalized_target:
            return 1
        return 0

    def _employment_eligibility_match_score(self, *, target: str, candidate: str) -> int:
        """Match only explicit Workday work-authorization wording.

        A profile can state whether sponsorship is required, but it does not
        establish citizenship or permanent-residency status.  The special
        tokens below therefore only accept dropdown labels that explicitly
        describe authorization and sponsorship, preserving a manual checkpoint
        for legal-status-only option sets.
        """
        if not target.startswith("__work_auth_us_"):
            return 0
        requires_sponsorship = any(
            phrase in candidate
            for phrase in ("require sponsorship", "requires sponsorship", "need sponsorship", "will need sponsorship")
        )
        no_sponsorship = any(
            phrase in candidate
            for phrase in (
                "do not require sponsorship",
                "does not require sponsorship",
                "will not require sponsorship",
                "without sponsorship",
                "no sponsorship required",
                "authorized to work permanently",
            )
        )
        not_authorized = any(
            phrase in candidate
            for phrase in ("not authorized", "not eligible", "not permitted")
        )
        if target == "__work_auth_us_no_sponsorship__":
            return 2 if no_sponsorship and not not_authorized else 0
        if target == "__work_auth_us_sponsorship_required__":
            return 2 if requires_sponsorship and not no_sponsorship else 0
        if target == "__work_auth_us_not_authorized__":
            return 2 if not_authorized else 0
        return 0

    def _prompt_option_match_score(
        self,
        *,
        field_name: str,
        target: str,
        candidate: str,
        selected_country: str = "",
    ) -> int:
        score = self._listbox_option_match_score(
            field_name=field_name,
            target=target,
            candidate=candidate,
        )
        if "countryphonecode" not in field_name.strip().lower() or not selected_country.strip():
            return score
        candidate_country = candidate.rsplit("(", 1)[0].strip()
        if (
            _canonical_country(candidate_country) == _canonical_country(selected_country)
            and self._is_effectively_same_value(
                field_name=field_name,
                current_value=candidate,
                desired_value=target,
            )
        ):
            return 4
        return score

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
            successful_uploads = page.locator('[data-automation-id="file-upload-successful"]')
            previous_success_count = successful_uploads.count()
            page.locator(selector).first.set_input_files(value)
            for _ in range(60):
                if successful_uploads.count() > previous_success_count:
                    return
                self._wait(page, 250)
            raise RuntimeError("Workday file upload did not reach a successful state")
            return
        if field_type == "text":
            locator = page.locator(selector).first
            date_component_value = self._date_component_value(field=field, value=value)
            locator.fill(date_component_value)
            self._wait(page, 200)
            return
        if field_type == "prompt-input":
            prompt_values = self._prompt_multi_values(value)
            if len(prompt_values) > 1:
                for prompt_value in prompt_values:
                    self._select_multi_prompt_value(page, field, prompt_value)
                return
            locator = page.locator(selector).first
            selection_path = self._prompt_selection_path(value, field=field)
            expected_target = selection_path[-1]
            field_name = str(field.get("field_name") or "")
            selected_country = ""
            if "countryphonecode" in field_name.lower():
                try:
                    selected_country = self._listbox_current_value(
                        page,
                        {"field_name": "country", "selector": 'button[name="country"]'},
                    )
                except Exception:
                    selected_country = ""
            for path_index, target in enumerate(selection_path):
                if path_index == 0:
                    locator.click(force=True)
                    locator.fill("")
                    locator.fill(target)
                    self._wait(page, 750)
                options = self._prompt_options(page, field, locator)
                option_locator = None
                best_match_score = 0
                for _ in range(20):
                    for index in range(options.count()):
                        candidate = options.nth(index)
                        try:
                            if not candidate.is_visible():
                                continue
                        except Exception:
                            pass
                        match_score = self._prompt_option_match_score(
                            field_name=field_name,
                            target=target,
                            candidate=str(candidate.inner_text() or ""),
                            selected_country=selected_country,
                        )
                        if match_score == 0:
                            continue
                        if match_score > best_match_score:
                            option_locator = candidate
                            best_match_score = match_score
                        if match_score == 4:
                            break
                    if option_locator is not None:
                        break
                    self._wait(page, 150)
                if option_locator is None:
                    exact_match = page.get_by_text(target, exact=True)
                    for index in range(exact_match.count()):
                        candidate = exact_match.nth(index)
                        try:
                            if candidate.is_visible():
                                candidate.click()
                                option_locator = candidate
                                break
                        except Exception:
                            continue
                    if option_locator is None:
                        if self._should_use_other_school(page, field, path_index, selection_path):
                            locator.fill("OTHER")
                            locator.press("Enter")
                            expected_target = "OTHER"
                            self._wait(page, 400)
                            continue
                        raise RuntimeError(f"prompt option not found for {target!r}")
                if path_index < len(selection_path) - 1:
                    branch = option_locator.locator('xpath=ancestor-or-self::*[@role="option"][1]')
                    side_charm = branch.locator(
                        '[data-uxi-multiselectlistitem-hassidecharm="true"] > div'
                    ).last
                    if side_charm.count() == 0:
                        raise RuntimeError(f"prompt option has no nested choices for {target!r}")
                    side_charm.click(force=True)
                else:
                    option_locator.click()
                self._wait(page, 300)
                if path_index < len(selection_path) - 1:
                    self._wait(page, 300)
            current_value = self._prompt_current_value(page, field)
            if not current_value or self._prompt_is_invalid(page, field):
                locator = page.locator(selector).first
                locator.press("ArrowDown")
                self._wait(page, 200)
                locator.press("Enter")
                self._wait(page, 300)
                try:
                    locator.press("Tab")
                except Exception:
                    pass
            for _ in range(15):
                current_value = self._prompt_current_value(page, field)
                if current_value and not self._prompt_is_invalid(page, field):
                    break
                self._wait(page, 100)
            if (
                self._normalize_option_text(expected_target) not in self._normalize_option_text(current_value)
                or self._prompt_is_invalid(page, field)
            ):
                raise RuntimeError(
                    "prompt selection was not committed: "
                    f"current={current_value!r}, desired={expected_target!r}, "
                    f"invalid={self._prompt_is_invalid(page, field)}"
                )
            return
        if field_type == "listbox-button":
            locator = page.locator(selector).first
            selection_path = self._listbox_selection_path(value, field=field)

            def verify_selection() -> None:
                current_value = self._normalized_current_value(
                    field_type=field_type,
                    current_value=self._listbox_current_value(page, field),
                )
                matches_selected_leaf = (
                    len(selection_path) > 1
                    and self._normalize_option_text(selection_path[-1])
                    in self._normalize_option_text(current_value)
                )
                if not matches_selected_leaf and not self._is_effectively_same_value(
                    field_name=str(field.get("field_name") or ""),
                    current_value=current_value,
                    desired_value=value,
                ):
                    raise RuntimeError(
                        f"listbox selected unexpected value: current={current_value!r}, desired={value!r}"
                    )

            try:
                # Workday's listbox buttons often ignore forced clicks: the
                # component needs focus and a normal pointer event to mount
                # its popup. Do that first, then retain force as a fallback
                # for overlays that occasionally cover the control.
                try:
                    locator.scroll_into_view_if_needed()
                except Exception:
                    pass
                try:
                    locator.click()
                except Exception:
                    locator.click(force=True)
                options = self._listbox_options(page, locator)
                # Workday frequently mounts the option list after the button
                # click animation. The adjacent input is an internal hidden
                # value mirror, never a user-editable search field.
                for _ in range(20):
                    if options.count() > 0:
                        break
                    self._wait(page, 150)
                    options = self._listbox_options(page, locator)
                field_name = str(field.get("field_name") or "")
                # Some tenants render the full source label as one option
                # ("A Job Board > LinkedIn"), while others expose a nested
                # menu. Prefer the exact flat option before traversing levels.
                normalized_value = self._normalize_option_text(value)
                for index in range(options.count()):
                    try:
                        candidate_text = self._normalize_option_text(options.nth(index).inner_text())
                    except Exception:
                        continue
                    if candidate_text == normalized_value:
                        selection_path = [value]
                        break
                    if (
                        field_name == "source"
                        and "linkedin" in candidate_text
                        and "linkedin" in normalized_value
                    ):
                        # This tenant's flat option is "Internet - LinkedIn".
                        # Selecting it is equivalent to the requested LinkedIn
                        # job-board source and can be verified by label.
                        selection_path = ["LinkedIn"]
                        break
                selected_option = False
                for path_index, target in enumerate(selection_path):
                    if path_index:
                        options = self._listbox_options(page, locator)
                        for _ in range(20):
                            if options.count() > 0:
                                break
                            self._wait(page, 150)
                            options = self._listbox_options(page, locator)
                    option_locator = None
                    best_match_score = 0
                    for index in range(options.count()):
                        candidate = options.nth(index)
                        candidate_text = self._normalize_option_text(candidate.inner_text())
                        if not candidate_text:
                            continue
                        match_score = self._listbox_option_match_score(
                            field_name=field_name,
                            target=target,
                            candidate=candidate_text,
                        )
                        if match_score > best_match_score:
                            option_locator = candidate
                            best_match_score = match_score
                        if match_score == 3:
                            break
                    if option_locator is None:
                        break
                    # Forced clicks can bypass the pointer sequence Workday's
                    # listbox uses to commit a value. Prefer a normal click,
                    # retaining force only for a genuine overlay failure.
                    try:
                        option_locator.click()
                    except Exception:
                        option_locator.click(force=True)
                    selected_option = True
                    self._wait(page, 350)
                    if path_index == len(selection_path) - 1:
                        try:
                            locator.press("Tab")
                        except Exception:
                            pass
                if selected_option and option_locator is not None:
                    # React commonly replaces both the popup and button after
                    # choosing an option. Poll the stable field selector until
                    # its selected value is observable before declaring the
                    # control unresolved.
                    for _ in range(24):
                        try:
                            verify_selection()
                            break
                        except RuntimeError:
                            self._wait(page, 250)
                    else:
                        verify_selection()
                    self._wait(page, 400)
                    return
                if value.startswith("__work_auth_us_"):
                    # Do not let keyboard navigation select the first legal
                    # status option when no explicit sponsorship match exists.
                    available_options: list[str] = []
                    for index in range(options.count()):
                        try:
                            option_text = str(options.nth(index).inner_text() or "").strip()
                        except Exception:
                            continue
                        if option_text:
                            available_options.append(option_text)
                    try:
                        locator.press("Escape")
                    except Exception:
                        pass
                    raise RuntimeError(
                        "employment-eligibility dropdown has no explicit authorization/sponsorship match; "
                        f"available options: {available_options!r}"
                    )
                if len(selection_path) > 1:
                    raise RuntimeError(f"listbox hierarchy option not found for {selection_path!r}")
                try:
                    locator.focus()
                except Exception:
                    pass
                keyboard = getattr(page, "keyboard", None)
                if keyboard is not None:
                    # Workday's closed-list typeahead highlights the matching
                    # option as characters are entered. Moving down afterward
                    # can change a precise match (for example Hispanic) into
                    # the adjacent option.
                    for char in value:
                        if char.isalnum():
                            keyboard.press(char.upper() if len(char) == 1 else char)
                            self._wait(page, 50)
                    self._wait(page, 100)
                    keyboard.press("Enter")
                    self._wait(page, 400)
                    verify_selection()
                    return
                for char in value:
                    if char.isalnum():
                        locator.press(char.upper() if len(char) == 1 else char)
                        self._wait(page, 50)
                self._wait(page, 100)
                locator.press("Enter")
                self._wait(page, 400)
                verify_selection()
                return
            except Exception as exc:
                raise RuntimeError(f"listbox selection failed: {exc}") from exc
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
                    option_value = str(option.get("value") or "")
                    option_locator = self._choice_option_locator(
                        page,
                        field=field,
                        option_label=label,
                        fallback_selector=option_selector,
                    )

                    def is_selected() -> bool:
                        # Check a freshly-resolved locator first. Workday can
                        # replace its controlled inputs after a state update.
                        candidates = [
                            self._choice_option_locator(
                                page,
                                field=field,
                                option_label=label,
                                fallback_selector=option_selector,
                            ),
                            option_locator,
                        ]
                        field_name = str(field.get("field_name") or "").strip()
                        if field_name and option_value:
                            escaped_name = field_name.replace("\\", "\\\\").replace('"', '\\"')
                            escaped_value = option_value.replace("\\", "\\\\").replace('"', '\\"')
                            candidates.insert(
                                0,
                                page.locator(
                                    f'input[name="{escaped_name}"][value="{escaped_value}"]'
                                ).first,
                            )
                        for candidate in candidates:
                            try:
                                if candidate.is_checked():
                                    return True
                            except Exception:
                                pass
                        return False

                    if field_type == "checkbox-group":
                        try:
                            # Workday's voluntary self-ID controls are rendered
                            # as virtualized checkbox rows. Clicking the input
                            # can temporarily change its DOM property without
                            # notifying Workday's state model; the visible label
                            # dispatches the supported interaction instead.
                            option_id = str(option_locator.get_attribute("id") or "")
                            if option_id:
                                escaped_id = option_id.replace("\\", "\\\\").replace('"', '\\"')
                                page.locator(f'label[for="{escaped_id}"]').first.click(force=True)
                                self._wait(page, 600)
                                if is_selected():
                                    return
                        except Exception:
                            pass
                    try:
                        # Let Playwright use the native radio semantics before
                        # falling back to Workday's custom visual controls.
                        option_locator.check(timeout=1_000)
                        self._wait(page, 200)
                        if is_selected():
                            return
                    except Exception:
                        pass
                    if field_type == "checkbox-group":
                        try:
                            # A DOM-native click performs the checkbox default
                            # action even when Workday's zero-size input cannot
                            # receive a Playwright pointer click.
                            option_locator.evaluate("element => element.click()")
                            self._wait(page, 350)
                            if is_selected():
                                return
                        except Exception:
                            pass
                        try:
                            # Voluntary-disclosure groups are virtualized grids
                            # in some tenants. The change handler is attached to
                            # the visible row, not to the nested checkbox shell.
                            row = option_locator.locator(
                                "xpath=ancestor-or-self::*[@role='row'][1]"
                            ).first
                            if row.count() > 0:
                                row.click(force=True)
                                self._wait(page, 350)
                                if is_selected():
                                    return
                        except Exception:
                            pass
                        try:
                            # Native checkbox keyboard semantics are a final
                            # generic fallback for zero-size controlled inputs.
                            option_locator.focus()
                            option_locator.press("Space")
                            self._wait(page, 350)
                            if is_selected():
                                return
                        except Exception:
                            pass
                        try:
                            # Some Workday tenants render a hidden native
                            # checkbox while React owns the visible control.
                            # Update the native property through its prototype
                            # setter and bubble the form events React observes.
                            # This is intentionally not a DOM attribute write:
                            # attributes do not update the controlled value.
                            option_locator.evaluate(
                                """
                                element => {
                                  const checkedSetter = Object.getOwnPropertyDescriptor(
                                    HTMLInputElement.prototype,
                                    'checked'
                                  )?.set;
                                  if (!checkedSetter) throw new Error('native checkbox setter unavailable');
                                  checkedSetter.call(element, true);
                                  element.dispatchEvent(new Event('input', { bubbles: true }));
                                  element.dispatchEvent(new Event('change', { bubbles: true }));
                                }
                                """
                            )
                            self._wait(page, 500)
                            if is_selected():
                                return
                        except Exception:
                            pass
                    try:
                        indicator = option_locator.locator("xpath=following-sibling::span[1]").first
                        try:
                            indicator.click()
                        except Exception:
                            indicator.click(force=True)
                        self._wait(page, 200)
                        if is_selected():
                            return
                    except Exception:
                        pass
                    # Workday commonly binds the change handler to the visual
                    # radio shell rather than the hidden input or its label.
                    try:
                        visual_control = option_locator.locator("xpath=..").first
                        try:
                            visual_control.click()
                        except Exception:
                            visual_control.click(force=True)
                        self._wait(page, 200)
                        if is_selected():
                            return
                    except Exception:
                        pass
                    option_id = None
                    try:
                        option_id = option_locator.get_attribute("id")
                    except Exception:
                        option_id = None
                    if option_id:
                        try:
                            label_locator = page.locator(f'label[for="{option_id}"]').first
                            # Workday keeps the native radio visually hidden;
                            # a normal click on its label is what triggers the
                            # tenant's controlled-state update.
                            try:
                                label_locator.click()
                            except Exception:
                                label_locator.click(force=True)
                            self._wait(page, 200)
                            if is_selected():
                                return
                            if field_type == "checkbox-group":
                                # Workday's ethnic-origin group uses a hidden
                                # checkbox behind a visual shell. Dispatch a
                                # native label click to reach React's controlled
                                # change handler when pointer clicks do not.
                                try:
                                    label_locator.dispatch_event("click")
                                except Exception:
                                    label_locator.evaluate("element => element.click()")
                                self._wait(page, 350)
                                if is_selected():
                                    return
                        except Exception:
                            pass
                    if field_type == "checkbox-group":
                        try:
                            option_locator.dispatch_event("click")
                            self._wait(page, 350)
                            if is_selected():
                                return
                        except Exception:
                            pass
                    try:
                        option_locator.check(force=True)
                        self._wait(page, 200)
                        if is_selected():
                            return
                    except Exception:
                        pass
                    try:
                        option_locator.click(force=True)
                        self._wait(page, 200)
                        if is_selected():
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

    def _choice_option_locator(
        self,
        page,
        *,
        field: dict[str, object],
        option_label: str,
        fallback_selector: str,
    ):
        """Reacquire a Workday choice after React replaces a rendered row."""
        if not hasattr(page, "evaluate") or not hasattr(page, "locator"):
            return page.locator(fallback_selector).first
        question_text = str(field.get("question_text") or "")
        try:
            option_id = str(
                page.evaluate(
                    r"""
                    ({ questionText, optionLabel }) => {
                      const normalize = (value) => (value || '')
                        .replace(/\*+/g, '')
                        .replace(/\s+/g, ' ')
                        .trim()
                        .toLowerCase();
                      const question = normalize(questionText);
                      const wanted = normalize(optionLabel);
                      const scope = Array.from(document.querySelectorAll('fieldset')).find((fieldset) => {
                        const legend = fieldset.querySelector('legend');
                        return legend && normalize(legend.textContent) === question;
                      });
                      if (!scope || !wanted) return '';
                      const label = Array.from(scope.querySelectorAll('label')).find((candidate) => {
                        const text = normalize(candidate.textContent);
                        return text === wanted || text.includes(wanted) || wanted.includes(text);
                      });
                      return label?.htmlFor || '';
                    }
                    """,
                    {"questionText": question_text, "optionLabel": option_label},
                )
                or ""
            ).strip()
        except Exception:
            option_id = ""
        if option_id:
            escaped_id = option_id.replace("\\", "\\\\").replace('"', '\\"')
            return page.locator(f'input[id="{escaped_id}"]').first
        return page.locator(fallback_selector).first

    def _date_component_value(self, *, field: dict[str, object], value: str) -> str:
        """Split a complete date override across Workday's three spinbuttons."""
        field_name = str(field.get("field_name") or "").lower()
        if "datesection" not in field_name:
            return value
        normalized = value.strip()
        try:
            month, day, year = normalized.split("/")
        except ValueError:
            return value
        components = {
            "month-input": month.zfill(2),
            "day-input": day.zfill(2),
            "year-input": year,
        }
        for suffix, component_value in components.items():
            if suffix in field_name:
                return component_value
        return value

    def _signed_attestation_date_resolution(
        self,
        *,
        field_name: str,
        question_text: str,
    ) -> AnswerResolution | None:
        """Fill Workday's explicit signed-on date with the current local date."""
        normalized = f"{field_name} {question_text}".lower()
        if "datesignedon" not in normalized:
            return None
        return AnswerResolution(
            answer=datetime.now().strftime("%m/%d/%Y"),
            source="capability:workday:self_identify_date_signed:current_date",
        )

    def _select_multi_prompt_value(self, page, field: dict[str, object], value: str) -> None:
        """Add one Workday multi-select value without toggling existing selections.

        Workday skills pickers render result rows asynchronously and use a checkbox
        inside each row. Clicking the row itself is unreliable: it can focus the
        search input or toggle an already selected item. Target the best matching
        checkbox after the search results have rendered instead.
        """
        if self._prompt_has_selected_value(page, field, value):
            return
        selector = str(field.get("selector") or "")
        if not selector:
            raise RuntimeError("missing multi-select prompt selector")
        search = page.locator(selector).first
        search.click(force=True)
        search.fill(value)
        # Some Workday skill pickers only issue their search after Enter. If it
        # selects a unique match directly, the selected-value check below exits.
        try:
            search.press("Enter")
        except Exception:
            pass
        self._wait(page, 500)
        if self._prompt_has_selected_value(page, field, value):
            search.fill("")
            return

        option_locator = None
        for _ in range(30):
            options = self._prompt_options(page, field, search)
            best_match_score = 0
            for index in range(options.count()):
                candidate = options.nth(index)
                try:
                    if not candidate.is_visible():
                        continue
                    candidate_text = str(candidate.inner_text() or "")
                except Exception:
                    continue
                match_score = self._prompt_option_match_score(
                    field_name=str(field.get("field_name") or ""),
                    target=value,
                    candidate=candidate_text,
                )
                if match_score > best_match_score:
                    option_locator = candidate
                    best_match_score = match_score
            if option_locator is not None:
                break
            self._wait(page, 250)
        if option_locator is None:
            raise RuntimeError(f"multi-select option not found for {value!r}")

        checkbox = None
        for row in (
            option_locator,
            option_locator.locator("xpath=ancestor::*[@role='option'][1]"),
            option_locator.locator(
                "xpath=ancestor::*[.//input[@type='checkbox'] or .//*[@role='checkbox']][1]"
            ),
        ):
            candidate_checkbox = row.locator('input[type="checkbox"]').first
            if candidate_checkbox.count() == 0:
                candidate_checkbox = row.locator('[role="checkbox"]').first
            if candidate_checkbox.count() > 0:
                checkbox = candidate_checkbox
                break
        if checkbox is None:
            # Some tenants use a clickable result row without exposing its
            # selection control to the accessibility tree.
            option_locator.click(force=True)
        elif checkbox.get_attribute("type") == "checkbox":
            try:
                if not checkbox.is_checked():
                    checkbox.check(force=True)
            except Exception:
                checkbox.click(force=True)
        else:
            checked = str(checkbox.get_attribute("aria-checked") or "").lower() == "true"
            if not checked:
                checkbox.click(force=True)

        for _ in range(20):
            if self._prompt_has_selected_value(page, field, value):
                search.fill("")
                self._wait(page, 200)
                return
            self._wait(page, 150)
        raise RuntimeError(f"multi-select value was not committed: {value!r}")

    def _listbox_current_value(self, page, field: dict[str, object]) -> str:
        """Read from a stable Workday selector after React replaces the opened button."""
        field_name = str(field.get("field_name") or "").strip()
        if field_name:
            escaped_name = field_name.replace("\\", "\\\\").replace('"', '\\"')
            stable_locator = page.locator(f'button[name="{escaped_name}"]').first
            if stable_locator.count() > 0:
                value = self._locator_value(stable_locator)
                if value:
                    return value
                # Some Workday dropdowns keep the selected text in a sibling
                # input or form-field selected-item node instead of the button.
                selected_selectors = (
                    f'button[name="{escaped_name}"] ~ input[type="hidden"]',
                    f'button[name="{escaped_name}"] ~ input[type="text"]',
                    (
                        f'[data-automation-id^="formField-"]:has(button[name="{escaped_name}"]) '
                        '[data-automation-id="selectedItem"]'
                    ),
                )
                for selector in selected_selectors:
                    try:
                        selected_value = self._locator_value(page.locator(selector).first)
                    except Exception:
                        continue
                    if selected_value:
                        return selected_value
        selector = str(field.get("selector") or "")
        return self._locator_value(page.locator(selector).first)

    def _locator_value(self, locator) -> str:
        try:
            if locator.count() == 0:
                return ""
        except Exception:
            return ""
        for reader in (
            lambda: locator.inner_text(),
            lambda: locator.get_attribute("value"),
            lambda: locator.get_attribute("aria-label"),
        ):
            try:
                value = str(reader() or "").strip()
            except Exception:
                continue
            if value:
                return value
        return ""

    def _prompt_current_value(self, page, field: dict[str, object]) -> str:
        container_id = str(field.get("container_id") or "").strip()
        if not container_id:
            return ""
        escaped_id = container_id.replace("\\", "\\\\").replace('"', '\\"')
        selected = page.locator(
            f'[id="{escaped_id}"] [data-automation-id="selectedItem"]'
        ).first
        selected_text = str(selected.inner_text() or "") if selected.count() > 0 else ""
        if selected_text.strip():
            return selected_text
        label = page.locator(
            f'[id="{escaped_id}"] [data-automation-id="promptSelectionLabel"]'
        ).first
        label_text = str(label.inner_text() or "") if label.count() > 0 else ""
        if label_text.strip():
            return label_text
        return ""

    def _prompt_has_selected_value(self, page, field: dict[str, object], value: str) -> bool:
        container_id = str(field.get("container_id") or "").strip()
        if not container_id:
            return False
        escaped_id = container_id.replace("\\", "\\\\").replace('"', '\\"')
        selected = page.locator(f'[id="{escaped_id}"] [data-automation-id="selectedItem"]')
        for index in range(selected.count()):
            try:
                text = self._normalize_option_text(str(selected.nth(index).inner_text() or ""))
            except Exception:
                continue
            if self._listbox_option_match_score(
                field_name=str(field.get("field_name") or ""),
                target=value,
                candidate=text,
            ) > 0:
                return True
        return False

    def _prompt_is_invalid(self, page, field: dict[str, object]) -> bool:
        field_name = str(field.get("field_name") or "").strip()
        if not field_name:
            return False
        escaped_name = field_name.replace("\\", "\\\\").replace('"', '\\"')
        input_locator = page.locator(f'input[id="{escaped_name}"]').first
        if input_locator.count() == 0:
            return False
        return str(input_locator.get_attribute("aria-invalid") or "").lower() == "true"

    def _prompt_options(self, page, field: dict[str, object], input_locator):
        """Prefer the popup owned by the active Workday prompt over global menus."""
        option_selectors = '[role="option"], [data-automation-id="promptOption"]'
        for attribute in ("aria-controls", "aria-owns"):
            try:
                popup_id = str(input_locator.get_attribute(attribute) or "").strip()
            except Exception:
                popup_id = ""
            if not popup_id:
                continue
            escaped_id = popup_id.replace("\\", "\\\\").replace('"', '\\"')
            scoped_options = page.locator(f'[id="{escaped_id}"] {option_selectors}')
            if scoped_options.count() > 0:
                return scoped_options
        container_id = str(field.get("container_id") or "").strip()
        if container_id:
            escaped_id = container_id.replace("\\", "\\\\").replace('"', '\\"')
            scoped_options = page.locator(f'[data-uxi-multiselect-id="{escaped_id}"] {option_selectors}')
            if scoped_options.count() > 0:
                return scoped_options
        return page.locator(option_selectors)

    def _listbox_options(self, page, button_locator):
        """Limit option matching to the menu controlled by the selected Workday field."""
        option_selectors = '[role="option"], [data-automation-id="menuItem"], [data-automation-id="promptOption"]'
        try:
            controls_id = str(button_locator.get_attribute("aria-controls") or "").strip()
        except Exception:
            controls_id = ""
        if controls_id:
            escaped_id = controls_id.replace("\\", "\\\\").replace('"', '\\"')
            scoped_options = page.locator(f'[id="{escaped_id}"] {option_selectors}')
            if scoped_options.count() > 0:
                return scoped_options
        # A form can contain already selected multiselect items which also
        # expose role=option. Only a visible popup is a valid listbox source.
        visible_popup_options = page.locator(
            f'[role="listbox"]:visible {option_selectors}, '
            f'[data-automation-id="menu"]:visible {option_selectors}, '
            f'[data-automation-id="promptOptions"]:visible {option_selectors}'
        )
        if visible_popup_options.count() > 0:
            return visible_popup_options
        return page.locator('[role="listbox"]:visible ' + option_selectors)

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
                exact_label = re.compile(rf"^\s*{re.escape(label)}\s*$", re.IGNORECASE)
                if selector.startswith("[data-automation-id=") or selector.startswith("button[aria-label="):
                    if locator.count() > 0:
                        # Workday reuses automation ids for hidden actions and
                        # for several footer labels. The visible label is the
                        # reliable action discriminator.
                        return locator.filter(has_text=exact_label).count() > 0
                    continue
                if locator.filter(has_text=exact_label).count() > 0:
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
                exact_label = re.compile(rf"^\s*{re.escape(label)}\s*$", re.IGNORECASE)
                if selector.startswith("[data-automation-id=") or selector.startswith("button[aria-label="):
                    if locator.count() == 0:
                        continue
                    candidate = locator.filter(has_text=exact_label).first
                    if candidate.count() == 0:
                        continue
                    candidate.click(force=True, timeout=2000)
                    return True
                candidate = locator.filter(has_text=exact_label).first
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
