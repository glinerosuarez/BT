from __future__ import annotations

import json
import logging
import re
import urllib.request
from typing import Any

from job_hunter.sources.base import SourceConnector, USER_AGENT, clamp_bulk_source_timeout

LOG = logging.getLogger(__name__)
NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.DOTALL)


class HiringCafeSource(SourceConnector):
    """Fetch server-rendered Hiring Cafe search results without browser automation."""

    def __init__(self, search_urls: list[str], max_results: int = 20) -> None:
        super().__init__(name="hiring_cafe")
        self.search_urls = [url.strip() for url in search_urls if url.strip()]
        self.max_results = max(max_results, 1)
        self._fetch_meta: dict[str, object] = {}

    def fetch(self, timeout_seconds: int) -> list[dict]:
        rows: list[dict] = []
        item_results: list[dict[str, str]] = []
        for search_url in self.search_urls:
            try:
                document = _fetch_html(search_url, clamp_bulk_source_timeout(timeout_seconds))
                hits = _extract_hits(document)
                query_rows = [_build_row(hit, search_url) for hit in hits[: self.max_results]]
                rows.extend(row for row in query_rows if row is not None)
                item_results.append({"item": search_url, "status": "success", "error": ""})
            except Exception as exc:
                LOG.warning("hiring_cafe_search_fetch_failed url=%s error=%s", search_url, exc)
                item_results.append({"item": search_url, "status": "failure", "error": str(exc)})

        deduped = _dedupe_rows(rows)
        self._fetch_meta = {
            "configured_search_urls": list(self.search_urls),
            "item_results": item_results,
            "fetched_count_before_dedupe": len(rows),
            "fetched_count_after_dedupe": len(deduped),
        }
        return deduped

    def get_fetch_meta(self) -> dict[str, object]:
        return dict(self._fetch_meta)


def _fetch_html(url: str, timeout_seconds: int) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        return response.read().decode("utf-8", errors="replace")


def _extract_hits(document: str) -> list[dict[str, Any]]:
    match = NEXT_DATA_RE.search(document)
    if not match:
        raise ValueError("Hiring Cafe page did not contain __NEXT_DATA__")
    payload = json.loads(match.group(1))
    page_props = payload.get("props", {}).get("pageProps", {})
    hits = page_props.get("ssrHits", []) if isinstance(page_props, dict) else []
    if not isinstance(hits, list):
        return []
    return [hit for hit in hits if isinstance(hit, dict)]


def _build_row(hit: dict[str, Any], search_url: str) -> dict | None:
    if bool(hit.get("is_hc_pinned")) or bool(hit.get("is_expired")):
        return None

    job_info = hit.get("job_information") if isinstance(hit.get("job_information"), dict) else {}
    processed = hit.get("v5_processed_job_data") if isinstance(hit.get("v5_processed_job_data"), dict) else {}
    company_data = hit.get("enriched_company_data") if isinstance(hit.get("enriched_company_data"), dict) else {}
    external_id = str(hit.get("objectID") or hit.get("id") or "").strip()
    title = str(job_info.get("title") or hit.get("job_title") or processed.get("core_job_title") or "").strip()
    company = str(processed.get("company_name") or company_data.get("name") or "").strip()
    apply_url = str(hit.get("apply_url") or hit.get("hc_apply_url") or "").strip()
    if not external_id or not title or not company:
        return None

    skills = _string_list(processed.get("technical_tools"))
    commitment = _string_list(processed.get("commitment"))
    role_activities = _string_list(processed.get("role_activities"))
    visa_sponsorship = processed.get("visa_sponsorship")
    description_parts = [
        title,
        str(processed.get("requirements_summary") or "").strip(),
        "Commitment: " + ", ".join(commitment) if commitment else "",
        "Role activities: " + ", ".join(role_activities) if role_activities else "",
        "Skills: " + ", ".join(skills) if skills else "",
    ]
    if visa_sponsorship is True:
        description_parts.append("Visa sponsorship is available.")
    elif visa_sponsorship is False:
        description_parts.append("Visa sponsorship is not available.")

    return {
        "source": "hiring_cafe",
        "source_detail": search_url,
        "source_metadata": {
            "external_apply_url": apply_url,
            "detail_quality_status": "structured_summary",
            "description_provenance": "hiring_cafe_ssr",
            "hiring_cafe_source": str(hit.get("source") or ""),
            "visa_sponsorship": visa_sponsorship,
        },
        "external_id": external_id,
        "url": apply_url or f"{search_url}#job-{external_id}",
        "title": title,
        "company": company,
        "location": str(processed.get("formatted_workplace_location") or "United States").strip(),
        "posted_at": str(processed.get("estimated_publish_date") or "").strip() or None,
        "description": "\n".join(part for part in description_parts if part),
        "skills": skills,
    }


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _dedupe_rows(rows: list[dict]) -> list[dict]:
    seen: set[str] = set()
    deduped: list[dict] = []
    for row in rows:
        key = str(row.get("external_id") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped
