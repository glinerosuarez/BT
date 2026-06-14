from __future__ import annotations

import datetime
import logging

from job_hunter.sources.base import SourceConnector, get_json

LOG = logging.getLogger(__name__)


class LeverSource(SourceConnector):
    def __init__(self, companies: list[str]) -> None:
        super().__init__(name="lever")
        self.companies = companies
        self._fetch_meta: dict[str, int] = {}

    def fetch(self, timeout_seconds: int) -> list[dict]:
        dead_token_count = 0
        max_error_logs = 10
        logged_errors = 0
        item_results: list[dict[str, str]] = []
        results: list[dict] = []
        for company in self.companies:
            try:
                jobs = get_json(
                    f"https://api.lever.co/v0/postings/{company}",
                    timeout_seconds,
                    params={"mode": "json"},
                )
            except Exception as exc:
                dead_token_count += 1
                if logged_errors < max_error_logs:
                    LOG.warning("lever_company_fetch_failed company=%s error=%s", company, exc)
                    logged_errors += 1
                item_results.append({"item": company, "status": "failure", "error": str(exc)})
                continue

            item_results.append({"item": company, "status": "success", "error": ""})
            if not isinstance(jobs, list):
                continue

            for item in jobs:
                if not isinstance(item, dict):
                    continue
                categories = item.get("categories", {})
                location = ""
                if isinstance(categories, dict):
                    location = str(categories.get("location") or "")
                posted_at = _ms_to_iso(item.get("createdAt"))
                description = " ".join(
                    str(item.get(key, "")) for key in ("descriptionPlain", "description", "additionalPlain")
                )
                results.append(
                    {
                        "source": self.name,
                        "source_detail": company,
                        "external_id": str(item.get("id") or item.get("hostedUrl") or ""),
                        "url": item.get("hostedUrl", ""),
                        "title": item.get("text", ""),
                        "company": company,
                        "location": location,
                        "posted_at": posted_at,
                        "description": description,
                        "skills": [],
                    }
                )
        self._fetch_meta = {"dead_token_count": dead_token_count, "item_results": item_results}
        suppressed = dead_token_count - logged_errors
        if suppressed > 0:
            LOG.warning("lever_company_fetch_failures_suppressed count=%s", suppressed)
        return results

    def get_fetch_meta(self) -> dict[str, int]:
        return dict(self._fetch_meta)


def _ms_to_iso(value: object) -> str | None:
    if value is None:
        return None
    try:
        ms = int(str(value))
    except ValueError:
        return None
    dt = datetime.datetime.fromtimestamp(ms / 1000, tz=datetime.timezone.utc)
    return dt.isoformat()
