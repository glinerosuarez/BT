from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from hashlib import sha1
from pathlib import Path
from urllib.parse import urljoin, urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from job_hunter.sources.base import SourceConnector

LOG = logging.getLogger(__name__)

JOB_CANDIDATES_SCRIPT = """
() => {
  const seen = new Set();
  const rows = [];
  for (const anchor of Array.from(document.querySelectorAll('a[href*="/jobs/detail/"]'))) {
    const url = anchor.href || '';
    if (!url || seen.has(url)) continue;
    seen.add(url);
    let container = anchor;
    for (let depth = 0; depth < 5 && container.parentElement; depth += 1) {
      const parent = container.parentElement;
      const text = (parent.innerText || '').trim();
      if (text && text.length <= 3000) container = parent;
      else break;
    }
    const text = (container.innerText || anchor.innerText || '').trim();
    if (text) rows.push({ url, text });
  }
  return rows;
}
"""

DETAIL_TEXT_SCRIPT = """
() => {
  let best = '';
  for (const node of Array.from(document.querySelectorAll('main, article, section, div'))) {
    const text = (node.innerText || '').trim();
    if (text.length < 120) continue;
    if (!/job description|description|responsibilities|qualifications/i.test(text)) continue;
    if (text.length > best.length) best = text;
  }
  return best;
}
"""

EXTERNAL_APPLY_URL_SCRIPT = """
() => {
  for (const node of Array.from(document.querySelectorAll('a[href]'))) {
    const text = (node.innerText || node.getAttribute('aria-label') || '').trim().toLowerCase();
    if (text === 'apply' || text === 'apply now' || text === 'apply for this job') return node.href || '';
  }
  return '';
}
"""

RELATIVE_AGE_RE = re.compile(
    r"\b(?:posted\s+)?(\d+)\s*(hours?|hrs?|hr|days?|weeks?|wks?|wk|months?|mos?|mo)\s+ago\b",
    re.IGNORECASE,
)
AGE_LINE_RE = re.compile(r"\b(?:posted\s+)?\d+\s*(?:hours?|hrs?|hr|days?|weeks?|wks?|wk|months?|mos?|mo)\s+ago\b", re.IGNORECASE)
LOCATION_RE = re.compile(r"\b(remote|hybrid|on-?site|united states|[A-Za-z .'-]+,\s*[A-Z]{2})\b", re.IGNORECASE)
DETAIL_MARKERS = ("job description", "description", "about the job", "responsibilities")
DETAIL_STOP_MARKERS = ("qualifications", "benefits", "about the company", "similar jobs", "report this job")
NOISE_LINES = {
    "jobs",
    "job search",
    "save",
    "apply",
    "apply now",
    "view job",
    "job details",
}


class InterstrideSource(SourceConnector):
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
                rows: list[dict] = []
                for search_url in self.search_urls:
                    rows.extend(self._fetch_search_page(page, search_url))
                self._fetch_meta = {"configured_query_keys": list(self.search_urls)}
                return _dedupe_rows(rows)
            finally:
                context.close()

    def get_fetch_meta(self) -> dict[str, object]:
        return dict(self._fetch_meta)

    def _fetch_search_page(self, page, search_url: str) -> list[dict]:
        page.goto(search_url, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        _raise_for_auth_wall(page)
        candidates = page.evaluate(JOB_CANDIDATES_SCRIPT) or []
        rows: list[dict] = []
        for candidate in candidates[: self.max_results]:
            if not isinstance(candidate, dict):
                continue
            card = _parse_card(str(candidate.get("text") or ""), str(candidate.get("url") or ""))
            if not card["title"] or not card["company"] or not card["url"]:
                continue
            if _is_older_than_lookback(card["posted_at"], self.max_posting_age_days):
                continue
            detail_text = ""
            external_apply_url = ""
            if self.fetch_details:
                detail_text, external_apply_url = self._fetch_detail(page.context, card["url"])
            rows.append(_build_row(card, detail_text, search_url, self.fetch_details, external_apply_url))
        return rows

    def _fetch_detail(self, context, job_url: str) -> tuple[str, str]:
        detail_page = context.new_page()
        detail_page.set_default_timeout(self.page_timeout_seconds * 1000)
        detail_page.set_default_navigation_timeout(self.page_timeout_seconds * 1000)
        try:
            detail_page.goto(job_url, wait_until="domcontentloaded")
            detail_page.wait_for_timeout(1500)
            _raise_for_auth_wall(detail_page)
            text = str(detail_page.evaluate(DETAIL_TEXT_SCRIPT) or "")
            if not text.strip():
                text = str(detail_page.locator("body").inner_text() or "")
            external_apply_url = str(detail_page.evaluate(EXTERNAL_APPLY_URL_SCRIPT) or "")
            return text, _safe_external_apply_url(external_apply_url)
        except PlaywrightTimeoutError:
            LOG.warning("interstride_detail_timeout url=%s", job_url)
            return "", ""
        finally:
            detail_page.close()


def _raise_for_auth_wall(page) -> None:
    url = str(page.url or "").lower()
    if "/login" in url or "/sign-in" in url:
        raise RuntimeError("Interstride session not authenticated. Run `python -m job_hunter.interstride_login` first.")
    text = str(page.locator("body").inner_text() or "").lower()
    if "sign in to interstride" in text or "log in to interstride" in text:
        raise RuntimeError("Interstride session not authenticated. Run `python -m job_hunter.interstride_login` first.")


def _parse_card(text: str, url: str) -> dict[str, str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    lines = [line for line in lines if line.lower() not in NOISE_LINES]
    title = lines[0] if lines else ""
    company = lines[1] if len(lines) > 1 else ""
    location = next((line for line in lines[2:] if LOCATION_RE.search(line)), "")
    age_line = next((line for line in lines if AGE_LINE_RE.search(line)), "")
    return {
        "title": title,
        "company": company,
        "location": location,
        "posted_at": _relative_age_to_iso(age_line) or "",
        "url": _canonical_job_url(url),
    }


def _extract_description(detail_text: str) -> str:
    lines = [line.strip() for line in detail_text.splitlines() if line.strip()]
    start = 0
    for index, line in enumerate(lines):
        if line.lower() in DETAIL_MARKERS:
            start = index + 1
            break
    kept: list[str] = []
    for line in lines[start:]:
        if line.lower() in DETAIL_STOP_MARKERS:
            break
        if line.lower() not in NOISE_LINES:
            kept.append(line)
    return "\n".join(kept).strip()


def _build_row(card: dict[str, str], detail_text: str, search_url: str, detail_fetch_attempted: bool, external_apply_url: str) -> dict:
    description = _extract_description(detail_text) or card["title"]
    job_url = card["url"]
    external_id = _job_id(job_url) or sha1(job_url.encode("utf-8")).hexdigest()
    detail_status = "detail_complete" if len(description) >= 200 else ("detail_partial" if detail_text else "card_only")
    metadata: dict[str, object] = {
        "detail_fetch_attempted": detail_fetch_attempted,
        "detail_quality_status": detail_status,
        "resolved_job_url": job_url,
    }
    if external_apply_url:
        metadata["external_apply_url"] = external_apply_url
    return {
        "source": "interstride",
        "source_detail": search_url,
        "source_metadata": metadata,
        "external_id": external_id,
        "url": job_url,
        "title": card["title"],
        "company": card["company"],
        "location": card["location"],
        "posted_at": card["posted_at"] or None,
        "description": description,
        "skills": [],
    }


def _relative_age_to_iso(value: str) -> str | None:
    match = RELATIVE_AGE_RE.search(value)
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2).lower()
    if unit.startswith(("hour", "hr")):
        delta = timedelta(hours=amount)
    elif unit.startswith("day"):
        delta = timedelta(days=amount)
    elif unit.startswith(("week", "wk")):
        delta = timedelta(weeks=amount)
    else:
        delta = timedelta(days=30 * amount)
    return (datetime.now(timezone.utc) - delta).isoformat()


def _is_older_than_lookback(posted_at: str, max_days: int) -> bool:
    if not posted_at:
        return False
    try:
        return datetime.fromisoformat(posted_at).astimezone(timezone.utc) < datetime.now(timezone.utc) - timedelta(days=max_days)
    except ValueError:
        return False


def _canonical_job_url(value: str) -> str:
    if not value:
        return ""
    return urljoin("https://student.interstride.com", value)


def _job_id(url: str) -> str:
    match = re.search(r"/jobs/(?:detail|job-details)/([^/?#]+)", urlparse(url).path)
    return match.group(1) if match else ""


def _safe_external_apply_url(url: str) -> str:
    if not url:
        return ""
    return "" if "student.interstride.com" in urlparse(url).netloc else url


def _dedupe_rows(rows: list[dict]) -> list[dict]:
    seen: set[str] = set()
    result: list[dict] = []
    for row in rows:
        key = str(row.get("external_id") or row.get("url") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result
