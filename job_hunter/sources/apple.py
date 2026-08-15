from __future__ import annotations

import logging
from typing import Any

from job_hunter.browser_api import load_browser_api, normalize_browser_backend
from job_hunter.sources.base import SourceConnector

LOG = logging.getLogger(__name__)

SEARCH_PAGE_URL = "https://jobs.apple.com/en-us/search?location=united-states-USA"
SEARCH_URL = "https://jobs.apple.com/api/v1/search"
DETAIL_URL_TEMPLATE = "https://jobs.apple.com/en-us/details/{position_id}"
US_LOCATION_ID = "postLocation-USA"


class AppleJobsSource(SourceConnector):
    """Fetch US Apple Jobs search results from Apple's public careers search API."""

    def __init__(
        self,
        queries: list[str],
        max_results: int = 25,
        headless: bool = True,
        page_timeout_seconds: int = 30,
        browser_backend: str = "playwright",
    ) -> None:
        super().__init__(name="apple")
        self.queries = [query.strip() for query in queries if query.strip()]
        self.max_results = max(max_results, 1)
        self.headless = headless
        self.page_timeout_seconds = max(page_timeout_seconds, 5)
        self.browser_backend = normalize_browser_backend(browser_backend)
        self._fetch_meta: dict[str, object] = {}

    def fetch(self, timeout_seconds: int) -> list[dict]:
        _ = timeout_seconds
        rows: list[dict] = []
        item_results: list[dict[str, str]] = []
        failures = 0

        with load_browser_api(self.browser_backend).sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=self.headless)
            try:
                page = browser.new_page()
                page.set_default_timeout(self.page_timeout_seconds * 1000)
                page.set_default_navigation_timeout(self.page_timeout_seconds * 1000)
                page.goto(SEARCH_PAGE_URL, wait_until="domcontentloaded")
                for query in self.queries:
                    try:
                        jobs = _fetch_search(page, query)
                        rows.extend(_build_row(job, query) for job in jobs[: self.max_results] if isinstance(job, dict))
                        item_results.append({"item": query, "status": "success", "error": ""})
                    except Exception as exc:
                        failures += 1
                        LOG.warning("apple_query_fetch_failed query=%s error=%s", query, exc)
                        item_results.append({"item": query, "status": "failure", "error": str(exc)})
            finally:
                browser.close()

        self._fetch_meta = {
            "configured_queries": list(self.queries),
            "dead_token_count": failures,
            "item_results": item_results,
        }
        return _dedupe_rows(rows)

    def get_fetch_meta(self) -> dict[str, object]:
        return dict(self._fetch_meta)


def _search_payload(query: str) -> dict[str, object]:
    return {
        "query": query,
        "locale": "en-us",
        "filters": {"locations": [US_LOCATION_ID]},
        "page": 1,
        "sort": "relevance",
        "format": {"longDate": "MMMM D, YYYY", "mediumDate": "MMM D, YYYY"},
    }


def _fetch_search(page, query: str) -> list[dict[str, Any]]:
    response = page.evaluate(
        """async ({ url, payload }) => {
          // Initialize the session cookie Apple requires before its search POST.
          await fetch('/api/v1/CSRFToken');
          const response = await fetch(url, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload),
          });
          const body = await response.json().catch(() => ({}));
          return {status: response.status, body};
        }""",
        {"url": SEARCH_URL, "payload": _search_payload(query)},
    )
    if not isinstance(response, dict) or int(response.get("status") or 0) != 200:
        status = response.get("status") if isinstance(response, dict) else "unknown"
        raise RuntimeError(f"Apple Jobs search failed with status={status}")
    body = response.get("body")
    data = body.get("res") if isinstance(body, dict) else None
    jobs = data.get("searchResults") if isinstance(data, dict) else None
    if not isinstance(jobs, list):
        return []
    return [job for job in jobs if isinstance(job, dict)]


def _build_row(job: dict[str, Any], query: str) -> dict:
    position_id = str(job.get("positionId") or job.get("id") or "").strip()
    title = str(job.get("postingTitle") or job.get("title") or "").strip()
    description = _description(job)
    return {
        "source": "apple",
        "source_detail": query,
        "source_metadata": {
            "detail_fetch_attempted": False,
            "detail_quality_status": "search_summary",
            "description_provenance": "apple_search_api",
            "apple_position_id": position_id,
        },
        "external_id": position_id,
        "url": DETAIL_URL_TEMPLATE.format(position_id=position_id) if position_id else "https://jobs.apple.com/en-us/search",
        "title": title,
        "company": "Apple",
        "location": _location(job),
        "posted_at": str(job.get("postDateInGMT") or job.get("postedDate") or "").strip() or None,
        "description": description or "Apple Jobs search result without a job description.",
        "skills": [],
    }


def _description(job: dict[str, Any]) -> str:
    fragments: list[str] = []
    for key in ("jobSummary", "description", "summary"):
        value = job.get(key)
        if isinstance(value, str) and value.strip():
            fragments.append(value.strip())
            break
    team = job.get("team")
    team_name = team.get("teamName") if isinstance(team, dict) else job.get("teamName")
    for label, value in (("Team", team_name), ("Job function", job.get("jobFunction")), ("Weekly hours", job.get("standardWeeklyHours") or job.get("weeklyHours"))):
        value = str(value or "").strip()
        if value:
            fragments.append(f"{label}: {value}.")
    return "\n".join(fragments)


def _location(job: dict[str, Any]) -> str:
    locations = job.get("locations") or job.get("postingLocations") or []
    if isinstance(locations, dict):
        locations = [locations]
    if not isinstance(locations, list):
        return str(job.get("location") or "").strip()

    rendered: list[str] = []
    for location in locations:
        if isinstance(location, str):
            value = location.strip()
        elif isinstance(location, dict):
            value = str(location.get("displayName") or location.get("name") or "").strip()
            if not value:
                parts = [str(location.get(key) or "").strip() for key in ("cityName", "stateName", "countryName")]
                value = ", ".join(part for part in parts if part)
        else:
            value = ""
        if value and value not in rendered:
            rendered.append(value)
    return " | ".join(rendered)


def _dedupe_rows(rows: list[dict]) -> list[dict]:
    unique: dict[str, dict] = {}
    for row in rows:
        key = str(row.get("external_id") or row.get("url") or "")
        if key and key not in unique:
            unique[key] = row
    return list(unique.values())
