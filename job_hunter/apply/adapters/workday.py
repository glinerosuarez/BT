from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

from job_hunter.apply.adapters.base import AdapterContext
from job_hunter.apply.types import Blocker, SubmitResult

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
        _ = resolver, context
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
                return self._blocked(
                    "manual_checkpoint_required",
                    page,
                    question_text="Workday application form reached, but automated field support is not implemented yet.",
                    details={
                        "checkpoint": "workday_application_form",
                        "checkpoint_label": "Workday application form",
                        "current_url": str(getattr(page, "url", "") or current_url),
                    },
                )

            break

        return self._blocked(
            "unsupported_widget",
            page,
            question_text="Unsupported Workday flow shape.",
            details={"current_url": current_url},
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
        for _ in range(45):
            current_url = str(getattr(page, "url", "") or "").lower()
            text = self._page_text_once(page).lower()
            if "/apply/applymanually" in current_url:
                if self._has_account_gate(page) or self._has_email_verification_gate(page) or self._is_form_stage(page) or self._extract_confirmation(page):
                    return
                self._wait(page, 1000)
                continue
            if "/apply" in current_url:
                if any(marker in text for marker in _START_APPLICATION_MARKERS) or self._workday_action_url(page, action="apply_manually"):
                    return
                if self._has_account_gate(page) or self._has_email_verification_gate(page) or self._is_form_stage(page) or self._extract_confirmation(page):
                    return
                self._wait(page, 1000)
                continue
            if self._workday_action_url(page, action="apply") or self._is_public_job_page(page):
                return
            if text and "loading" not in text and "follow us" not in text:
                return
            self._wait(page, 1000)

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
            adapter_name=self.adapter_name,
        )

    def complete_email_verification(self, *, page, code: str, steps: list, context: AdapterContext | None = None) -> SubmitResult:
        _ = code, steps, context
        return self._blocked(
            "manual_checkpoint_required",
            page,
            question_text="Workday application form reached, but automated field support is not implemented yet.",
            details={
                "checkpoint": "workday_application_form",
                "checkpoint_label": "Workday application form",
                "current_url": str(getattr(page, "url", "") or ""),
            },
        )
