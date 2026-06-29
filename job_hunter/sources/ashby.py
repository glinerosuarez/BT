from __future__ import annotations

import json
import logging
import re
import urllib.request

from job_hunter.sources.base import SourceConnector, USER_AGENT, clamp_bulk_source_timeout

LOG = logging.getLogger(__name__)
APP_DATA_RE = re.compile(r"window\.__appData\s*=\s*(\{.*?\});", re.S)


class AshbySource(SourceConnector):
    def __init__(self, board_slugs: list[str]) -> None:
        super().__init__(name="ashby")
        self.board_slugs = board_slugs
        self._fetch_meta: dict[str, int] = {}

    def fetch(self, timeout_seconds: int) -> list[dict]:
        http_timeout_seconds = clamp_bulk_source_timeout(timeout_seconds)
        dead_token_count = 0
        logged_errors = 0
        max_error_logs = 10
        item_results: list[dict[str, str]] = []
        results: list[dict] = []

        for slug in self.board_slugs:
            board_url = f"https://jobs.ashbyhq.com/{slug}"
            try:
                req = urllib.request.Request(board_url, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=http_timeout_seconds) as resp:
                    html = resp.read().decode("utf-8", errors="replace")
            except Exception as exc:
                dead_token_count += 1
                if logged_errors < max_error_logs:
                    LOG.warning("ashby_board_fetch_failed board=%s error=%s", slug, exc)
                    logged_errors += 1
                item_results.append({"item": slug, "status": "failure", "error": str(exc)})
                continue

            match = APP_DATA_RE.search(html)
            if not match:
                dead_token_count += 1
                error = "window.__appData not found"
                if logged_errors < max_error_logs:
                    LOG.warning("ashby_board_parse_failed board=%s error=%s", slug, error)
                    logged_errors += 1
                item_results.append({"item": slug, "status": "failure", "error": error})
                continue

            try:
                payload = json.loads(match.group(1))
            except json.JSONDecodeError as exc:
                dead_token_count += 1
                if logged_errors < max_error_logs:
                    LOG.warning("ashby_board_json_failed board=%s error=%s", slug, exc)
                    logged_errors += 1
                item_results.append({"item": slug, "status": "failure", "error": str(exc)})
                continue

            item_results.append({"item": slug, "status": "success", "error": ""})
            job_board = payload.get("jobBoard") or {}
            org = payload.get("organization") or {}
            org_name = str(org.get("name") or slug)
            jobs = job_board.get("jobPostings") or []
            for posting in jobs:
                if not isinstance(posting, dict):
                    continue
                if not posting.get("isListed", True):
                    continue
                external_id = str(posting.get("id") or posting.get("jobId") or "")
                title = str(posting.get("title") or "").strip()
                if not title or not external_id:
                    continue

                url = f"{board_url}/{external_id}"
                location = _build_location(posting)
                description = _build_description(posting)
                results.append(
                    {
                        "source": self.name,
                        "source_detail": slug,
                        "external_id": external_id,
                        "url": url,
                        "title": title,
                        "company": org_name,
                        "location": location,
                        "posted_at": posting.get("publishedDate") or posting.get("updatedAt"),
                        "description": description,
                        "skills": [],
                    }
                )

        self._fetch_meta = {"dead_token_count": dead_token_count, "item_results": item_results}
        suppressed = dead_token_count - logged_errors
        if suppressed > 0:
            LOG.warning("ashby_board_fetch_failures_suppressed count=%s", suppressed)
        return results

    def get_fetch_meta(self) -> dict[str, int]:
        return dict(self._fetch_meta)


def _build_location(posting: dict) -> str:
    parts: list[str] = []
    primary = str(posting.get("locationName") or "").strip()
    if primary:
        parts.append(primary)
    secondary_locations = posting.get("secondaryLocations") or []
    for secondary in secondary_locations:
        if not isinstance(secondary, dict):
            continue
        name = str(secondary.get("locationName") or "").strip()
        if name and name not in parts:
            parts.append(name)
    return " | ".join(parts)


def _build_description(posting: dict) -> str:
    fragments: list[str] = []
    team = str(posting.get("teamName") or "").strip()
    department = str(posting.get("departmentName") or "").strip()
    employment_type = str(posting.get("employmentType") or "").strip()
    workplace_type = str(posting.get("workplaceType") or "").strip()
    compensation = str(posting.get("compensationTierSummary") or "").strip()

    if team:
        fragments.append(f"Team: {team}.")
    if department and department != team:
        fragments.append(f"Department: {department}.")
    if employment_type:
        fragments.append(f"Employment type: {employment_type}.")
    if workplace_type:
        fragments.append(f"Workplace type: {workplace_type}.")
    if compensation:
        fragments.append(f"Compensation: {compensation}.")
    return " ".join(fragments)
