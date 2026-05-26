from __future__ import annotations

import datetime
import logging

from job_hunter.sources.base import SourceConnector, get_json

LOG = logging.getLogger(__name__)


class LeverSource(SourceConnector):
    def __init__(self, companies: list[str]) -> None:
        super().__init__(name="lever")
        self.companies = companies

    def fetch(self, timeout_seconds: int) -> list[dict]:
        results: list[dict] = []
        for company in self.companies:
            try:
                jobs = get_json(
                    f"https://api.lever.co/v0/postings/{company}",
                    timeout_seconds,
                    params={"mode": "json"},
                )
            except Exception as exc:
                LOG.warning("lever_company_fetch_failed company=%s error=%s", company, exc)
                continue

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
        return results


def _ms_to_iso(value: object) -> str | None:
    if value is None:
        return None
    try:
        ms = int(str(value))
    except ValueError:
        return None
    dt = datetime.datetime.fromtimestamp(ms / 1000, tz=datetime.timezone.utc)
    return dt.isoformat()
