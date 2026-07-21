from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import sync_playwright

from job_hunter.sources.base import SourceConnector

LOG = logging.getLogger(__name__)

API_URL = "https://web.production.interstride.com/api/v1/jobs/search"
BASE_SEARCH_URL = "https://student.interstride.com/jobs/search"


class InterstrideSource(SourceConnector):
    """Fetch structured Interstride results through the authenticated web client API."""

    def __init__(
        self,
        search_urls: list[str],
        profile_dir: str,
        headless: bool,
        max_results: int,
        page_timeout_seconds: int,
        max_posting_age_days: int,
        fetch_details: bool = True,
    ) -> None:
        super().__init__(name="interstride")
        self.search_urls = search_urls
        self.profile_dir = profile_dir
        self.headless = headless
        self.max_results = max(max_results, 1)
        self.page_timeout_seconds = max(page_timeout_seconds, 5)
        self.max_posting_age_days = max(max_posting_age_days, 1)
        self.fetch_details = fetch_details
        self._fetch_meta: dict[str, object] = {}

    def fetch(self, timeout_seconds: int) -> list[dict]:
        _ = timeout_seconds
        profile_path = Path(self.profile_dir).expanduser()
        profile_path.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                str(profile_path), channel="chrome", headless=self.headless
            )
            try:
                context.set_default_timeout(self.page_timeout_seconds * 1000)
                context.set_default_navigation_timeout(self.page_timeout_seconds * 1000)
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(BASE_SEARCH_URL, wait_until="domcontentloaded")
                page.wait_for_timeout(1000)
                _raise_for_auth_wall(page)

                rows: list[dict] = []
                for search_url in self.search_urls:
                    items = self._fetch_api_page(page, search_url)
                    rows.extend(_build_row(item, search_url) for item in items[: self.max_results])
                self._fetch_meta = {"configured_query_keys": list(self.search_urls)}
                return _dedupe_rows(rows)
            finally:
                context.close()

    def get_fetch_meta(self) -> dict[str, object]:
        return dict(self._fetch_meta)

    def _fetch_api_page(self, page, search_url: str) -> list[dict]:
        payload = _search_payload(search_url)
        response = page.evaluate(
            """async ({ url, payload }) => {
              const token = localStorage.getItem('authToken');
              if (!token) return { status: 401, error: 'missing_auth_token', jobs: [] };
              const response = await fetch(url, {
                method: 'POST',
                headers: {
                  'Content-Type': 'application/json;charset=utf-8',
                  'Authorization': `Bearer ${token}`,
                },
                body: JSON.stringify(payload),
              });
              const body = await response.json().catch(() => ({}));
              return { status: response.status, error: body?.error || '', jobs: body?.data?.jobs || [] };
            }""",
            {"url": API_URL, "payload": payload},
        )
        if not isinstance(response, dict) or int(response.get("status") or 0) != 200:
            raise RuntimeError(f"interstride_search_failed status={response.get('status') if isinstance(response, dict) else 'unknown'}")
        jobs = response.get("jobs")
        if not isinstance(jobs, list):
            return []
        return [item for item in jobs if isinstance(item, dict)]


def _search_payload(search_url: str) -> dict[str, object]:
    query = parse_qs(urlparse(search_url).query)
    keyword = (query.get("keyword") or query.get("query") or [""])[0].strip()
    payload: dict[str, object] = {
        "sort": "date",
        "job_region": "us",
        "country": "us",
        "visa": "all_sponsored_companies",
        "job_type": ["internship"],
        "job_search_type": "approx",
        "page": 1,
    }
    if keyword:
        payload["search"] = keyword
        payload["keyword"] = keyword
    return payload


def _raise_for_auth_wall(page) -> None:
    url = str(page.url or "").lower()
    if "/login" in url or "/sign-in" in url:
        raise RuntimeError("Interstride session not authenticated. Run `python -m job_hunter.interstride_login` first.")
    if not page.evaluate("Boolean(localStorage.getItem('authToken'))"):
        raise RuntimeError("Interstride session not authenticated. Run `python -m job_hunter.interstride_login` first.")


def _build_row(item: dict, search_url: str) -> dict:
    title = str(item.get("job_title") or "").strip()
    company = str(item.get("company") or "").strip()
    location = str(item.get("formatted_location_full") or item.get("formatted_location") or "").strip()
    job_url = str(item.get("url") or "").strip()
    external_id = str(item.get("id") or item.get("job_key") or job_url).strip()
    summary = str(item.get("snippet") or item.get("ai_summary") or "").strip()
    sponsorship_available = bool(item.get("visa_sponsorship"))
    source_metadata: dict[str, object] = {
        "detail_fetch_attempted": False,
        "detail_quality_status": "summary_only",
        "description_provenance": "interstride_summary",
        "interstride_job_key": str(item.get("job_key") or ""),
        "interstride_source": str(item.get("source") or ""),
        "visa_sponsorship": sponsorship_available,
    }
    if job_url:
        source_metadata["external_apply_url"] = job_url
    description = summary or "Interstride listing without a full job description."
    if sponsorship_available:
        description = f"Sponsorship available. {description}"
    return {
        "source": "interstride",
        "source_detail": search_url,
        "source_metadata": source_metadata,
        "external_id": external_id,
        "url": job_url or BASE_SEARCH_URL,
        "title": title,
        "company": company,
        "location": location,
        "posted_at": str(item.get("date") or "").strip() or None,
        "description": description,
        "skills": [],
    }


def _dedupe_rows(rows: list[dict]) -> list[dict]:
    best_by_key: dict[str, dict] = {}
    for row in rows:
        key = "|".join(
            [
                _normalized_key_part(row.get("company")),
                _normalized_key_part(row.get("title")),
                _normalized_key_part(row.get("location")),
                str(row.get("posted_at") or "")[:10],
            ]
        )
        if not key.strip("|"):
            continue
        current = best_by_key.get(key)
        if current is None or len(str(row.get("description") or "")) > len(str(current.get("description") or "")):
            best_by_key[key] = row
    return list(best_by_key.values())


def _normalized_key_part(value: object) -> str:
    return " ".join(str(value or "").lower().split())
