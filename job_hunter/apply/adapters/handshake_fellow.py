from __future__ import annotations

import html
import re
from urllib.parse import urlparse

from job_hunter.apply.types import Blocker, SubmitResult

_CONFIRMATION_MARKERS = (
    "application submitted",
    "your application has been submitted",
    "thank you for applying",
)
_CONFIRMATION_PATH_MARKERS = (
    "/confirmation",
    "/submitted",
    "/success",
)
_DASHBOARD_TITLE_MARKERS = (
    "projects | handshake ai",
    "handshake ai",
)
_DASHBOARD_TEXT_MARKERS = (
    "awaiting a project match",
    "we'll let you know when a project matches your profile",
    "get paid to do meaningful work",
    "setup payout method",
    "update your profile",
)


class HandshakeFellowAdapter:
    adapter_name = "handshake_fellow"

    def is_handshake_fellow_target(self, url: str, page=None) -> bool:
        parsed = urlparse(url.strip())
        host = parsed.netloc.lower()
        path = parsed.path.lower()
        if "ai.joinhandshake.com" in host and "/fellow" in path:
            return True
        current_url = str(getattr(page, "url", "") or "").strip() if page is not None else ""
        if current_url:
            current = urlparse(current_url)
            if "ai.joinhandshake.com" in current.netloc.lower() and "/fellow" in current.path.lower():
                return True
        return False

    def submit(self, *, page, resolver, context) -> SubmitResult:
        _ = resolver, context
        confirmation = self._extract_confirmation(page)
        if confirmation:
            return SubmitResult(
                status="submitted",
                current_url=str(getattr(page, "url", "") or ""),
                confirmation_payload=confirmation,
                adapter_name=self.adapter_name,
            )
        current_url = str(getattr(page, "url", "") or "")
        if not self._has_job_application_context(page):
            return SubmitResult(
                status="blocked",
                current_url=current_url,
                blocker=Blocker(
                    reason="handshake_fellow_dashboard_only",
                    question_text="Handshake Fellow dashboard",
                    field_name="target_url",
                    field_type="url",
                    details={
                        "message": (
                            "Handshake Fellow opened to a generic dashboard without a job-specific "
                            "application context."
                        ),
                    },
                ),
                adapter_name=self.adapter_name,
                target_url=current_url,
            )
        return SubmitResult(
            status="blocked",
            current_url=current_url,
            blocker=Blocker(
                reason="manual_checkpoint_required",
                question_text="Handshake Fellow application flow",
                field_name="target_url",
                field_type="url",
                details={
                    "checkpoint": "handshake_fellow_apply",
                    "checkpoint_label": "Handshake Fellow application",
                    "message": (
                        "Complete the Handshake Fellow application steps in the browser, "
                        "stop on the final confirmation page, then resume automation from that page."
                    ),
                },
            ),
            adapter_name=self.adapter_name,
            target_url=current_url,
        )

    def _extract_confirmation(self, page) -> dict[str, object]:
        extractor = getattr(page, "extract_confirmation", None)
        if callable(extractor):
            payload = dict(extractor() or {})
            if payload:
                return payload
        current_url = str(getattr(page, "url", "") or "")
        lowered_url = current_url.lower()
        if not any(marker in lowered_url for marker in _CONFIRMATION_PATH_MARKERS):
            return {}
        lowered = self._visible_text(page).lower()
        if any(marker in lowered for marker in _CONFIRMATION_MARKERS):
            return {
                "message": "Application submitted",
                "url": current_url,
                "source": "handshake_fellow",
            }
        return {}

    def _has_job_application_context(self, page) -> bool:
        current_url = str(getattr(page, "url", "") or "").lower()
        if any(marker in current_url for marker in ("/apply", "/application", "/review", "/submission", "/confirm")):
            return True
        lowered = self._visible_text(page).lower()
        title = self._page_title(page).lower()
        if any(marker in title for marker in _DASHBOARD_TITLE_MARKERS):
            return False
        if any(marker in lowered for marker in _DASHBOARD_TEXT_MARKERS):
            return False
        return any(
            marker in lowered
            for marker in (
                "review your application",
                "submit application",
                "application questions",
                "application review",
                "submission",
                "complete your application",
            )
        )

    def _visible_text(self, page) -> str:
        locator = getattr(page, "locator", None)
        if callable(locator):
            try:
                text = locator("body").inner_text(timeout=1_000)
                if text:
                    return str(text)
            except Exception:
                pass

        text_content = getattr(page, "text_content", None)
        if callable(text_content):
            try:
                text = text_content("body")
                if text:
                    return str(text)
            except Exception:
                pass

        content = ""
        try:
            content = str(page.content() or "")
        except Exception:
            content = ""
        if not content:
            return ""

        without_scripts = re.sub(r"<script\b[^>]*>.*?</script>", " ", content, flags=re.IGNORECASE | re.DOTALL)
        without_styles = re.sub(r"<style\b[^>]*>.*?</style>", " ", without_scripts, flags=re.IGNORECASE | re.DOTALL)
        without_tags = re.sub(r"<[^>]+>", " ", without_styles)
        normalized = html.unescape(without_tags)
        return re.sub(r"\s+", " ", normalized).strip()

    def _page_title(self, page) -> str:
        title = getattr(page, "title", None)
        if callable(title):
            try:
                value = title()
                if value:
                    return str(value)
            except Exception:
                pass

        content = ""
        try:
            content = str(page.content() or "")
        except Exception:
            content = ""
        if not content:
            return ""

        match = re.search(r"<title[^>]*>(.*?)</title>", content, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            return ""
        return re.sub(r"\s+", " ", html.unescape(match.group(1))).strip()
