from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from job_hunter.models import JobRecord

LOG = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str, timeout_seconds: int = 20) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.timeout_seconds = timeout_seconds

    def send(self, job: JobRecord) -> bool:
        text = _format_alert(job)
        return self.send_text(text)

    def send_text(self, text: str) -> bool:
        endpoint = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        data = urllib.parse.urlencode(
            {
                "chat_id": self.chat_id,
                "text": text,
                "disable_web_page_preview": "true",
            }
        ).encode("utf-8")

        req = urllib.request.Request(endpoint, data=data, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            ok = bool(payload.get("ok"))
            if not ok:
                LOG.warning("telegram_send_failed %s", payload)
            return ok
        except urllib.error.URLError:
            LOG.exception("telegram_send_error")
            return False


def _format_alert(job: JobRecord) -> str:
    keywords = ", ".join(job.relevance_hits[:5]) if job.relevance_hits else "none"
    sponsor = ", ".join(job.sponsorship_signals[:3]) if job.sponsorship_signals else "none"
    work_auth = ", ".join(job.work_auth_signals[:3]) if job.work_auth_signals else "none"
    posted = job.posted_at or "unknown"
    compensation = job.compensation_type or "unknown"

    return (
        f"[Internship Alert] {job.title}\n"
        f"Company: {job.company}\n"
        f"Location: {job.location or 'unknown'}\n"
        f"Posted: {posted}\n"
        f"Compensation: {compensation}\n"
        f"Score: {job.relevance_score:.2f}\n"
        f"Eligibility: {job.eligibility_status} ({job.eligibility_confidence:.2f})\n"
        f"Sponsorship signals: {sponsor}\n"
        f"Work-auth negatives: {work_auth}\n"
        f"Keywords: {keywords}\n"
        f"Apply: {job.url}"
    )
