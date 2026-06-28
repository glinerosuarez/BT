from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from hashlib import sha1
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from job_hunter.sources.base import SourceConnector

LOG = logging.getLogger(__name__)
DETAIL_TEXT_SCRIPT = """
() => {
  let best = null;
  let bestScore = 0;
  for (const node of Array.from(document.querySelectorAll('main, section, article, div'))) {
    const rect = node.getBoundingClientRect();
    const text = (node.innerText || '').trim();
    if (!text) continue;
    const hasJobDescription = text.includes('Job description');
    const hasAtAGlance = text.includes('At a glance');
    if (!hasJobDescription && !hasAtAGlance) continue;
    if (rect.left < window.innerWidth * 0.35) continue;
    const area = rect.width * rect.height;
    let score = area;
    if (hasJobDescription) score += 1_000_000_000;
    if (hasAtAGlance) score += 500_000_000;
    if (text.includes('US work authorization required')) score += 250_000_000;
    if (text.includes('Open to candidates with OPT/CPT')) score += 250_000_000;
    if (score <= bestScore) continue;
    bestScore = score;
    best = text;
  }
  return best || '';
}
"""
EXPAND_MORE_SCRIPT = """
() => {
  let clicked = 0;
  for (const node of Array.from(document.querySelectorAll('button, a, div[role="button"], span[role="button"]'))) {
    const text = (node.innerText || '').trim();
    if (text !== 'More') continue;
    const rect = node.getBoundingClientRect();
    if (rect.left < window.innerWidth * 0.35) continue;
    if (rect.width === 0 || rect.height === 0) continue;
    node.click();
    clicked += 1;
  }
  return clicked;
}
"""
RELATIVE_AGE_RE = re.compile(
    r"\b(?:(?:posted)\s+)?(\d+)\s*(hours?|hrs?|hr|days?|d|weeks?|wks?|wk|w|months?|mos?|mo)\s+ago\b",
    re.IGNORECASE,
)


class HandshakeSource(SourceConnector):
    def __init__(
        self,
        search_urls: list[str],
        profile_dir: str,
        headless: bool,
        max_results: int,
        page_timeout_seconds: int,
        fetch_details: bool = True,
    ) -> None:
        super().__init__(name="handshake")
        self.search_urls = search_urls
        self.profile_dir = profile_dir
        self.headless = headless
        self.max_results = max(max_results, 1)
        self.page_timeout_seconds = max(page_timeout_seconds, 5)
        self.fetch_details = fetch_details

    def fetch(self, timeout_seconds: int) -> list[dict]:
        _ = timeout_seconds
        profile_path = Path(self.profile_dir).expanduser()
        profile_path.mkdir(parents=True, exist_ok=True)

        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                str(profile_path),
                channel="chrome",
                headless=self.headless,
            )
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.set_default_timeout(self.page_timeout_seconds * 1000)
                results: list[dict] = []
                for search_url in self.search_urls:
                    normalized_search_url = _normalize_search_url(search_url)
                    results.extend(self._fetch_search_page(page, normalized_search_url))
                return _dedupe_rows(results)
            finally:
                context.close()

    def _fetch_search_page(self, page, search_url: str) -> list[dict]:
        page.goto(search_url, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        if "joinhandshake.com/login" in page.url or "users/sign_in" in page.url:
            raise RuntimeError(
                "Handshake session not authenticated. Run `python -m job_hunter.handshake_login` first."
            )

        try:
            page.get_by_text(re.compile(r"jobs? found", re.IGNORECASE)).first.wait_for(timeout=5000)
        except PlaywrightTimeoutError:
            LOG.warning("handshake_results_count_not_found url=%s", search_url)

        body_text = page.locator("body").inner_text()
        card_payloads = _extract_cards_from_page_text(body_text)
        rows: list[dict] = []
        for card in card_payloads[: self.max_results]:
            company = str(card.get("company") or "")
            title = str(card.get("title") or "")
            meta = str(card.get("meta") or "")
            location = str(card.get("location") or "")
            freshness = str(card.get("freshness") or "")
            if not company or not title:
                continue
            card_text = "\n".join([company, title, meta, location, freshness])
            card_url = ""
            detail_text = ""
            if self.fetch_details:
                locator = page.get_by_role("button", name=title, exact=False).first
                try:
                    card_url = urljoin(page.url, locator.get_attribute("href") or "")
                    locator.click(timeout=5000)
                    page.wait_for_timeout(1500)
                    expanded = int(page.evaluate(EXPAND_MORE_SCRIPT) or 0)
                    if expanded:
                        page.wait_for_timeout(750)
                    card_url = _resolve_job_url(page, title, card_url)
                    detail_text = str(page.evaluate(DETAIL_TEXT_SCRIPT) or "")
                except PlaywrightTimeoutError:
                    detail_text = ""
            if not card_url:
                card_url = _infer_card_url(page.url, search_url, title)
            parsed = _build_row(
                card_text=card_text,
                detail_text=detail_text,
                search_url=search_url,
                card_url=card_url,
            )
            if parsed is not None:
                rows.append(parsed)
        return rows


def _dedupe_rows(rows: list[dict]) -> list[dict]:
    seen: set[str] = set()
    deduped: list[dict] = []
    for row in rows:
        key = str(row.get("external_id") or row.get("url") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def _normalize_search_url(search_url: str) -> str:
    parsed = urlparse(search_url)
    query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
    filtered_pairs = [(key, value) for key, value in query_pairs if key != "sort"]
    filtered_pairs.append(("sort", "posted_date_desc"))
    normalized_query = urlencode(filtered_pairs, doseq=True)
    return urlunparse(parsed._replace(query=normalized_query))


def _extract_cards_from_page_text(body_text: str) -> list[dict[str, str]]:
    lines = [line.strip() for line in body_text.splitlines() if line.strip()]
    cards: list[dict[str, str]] = []
    i = 0
    while i + 5 < len(lines):
        if lines[i] == "Jobs" and i > 20:
            break
        company = lines[i]
        title = lines[i + 1]
        meta = lines[i + 2]
        location = lines[i + 3]
        bullet = lines[i + 4]
        freshness = lines[i + 5]
        if not _looks_like_card(meta, bullet, freshness):
            i += 1
            continue
        cards.append(
            {
                "company": company,
                "title": title,
                "meta": meta,
                "location": location,
                "freshness": freshness,
            }
        )
        i += 6
    return cards


def _build_row(card_text: str, detail_text: str, search_url: str, card_url: str = "") -> dict | None:
    card = _parse_card_text(card_text)
    if card is None:
        return None
    detail = _parse_detail_text(detail_text)
    title = detail.get("title") or card["title"]
    company = detail.get("company") or card["company"]
    location = detail.get("location") or card["location"]
    posted_at = detail.get("posted_at") or card["posted_at"]
    description = detail.get("description") or detail_text or card_text
    compensation_type = _classify_compensation_from_source(card_text=card_text, detail_text=detail_text)
    external_id = f"{company}|{title}|{location}|{posted_at or ''}"
    url = card_url or f"{search_url}#jobhunter-{sha1(external_id.encode('utf-8')).hexdigest()[:12]}"
    return {
        "source": "handshake",
        "source_detail": search_url,
        "external_id": external_id,
        "url": url,
        "title": title,
        "company": company,
        "location": location,
        "posted_at": posted_at,
        "description": description,
        "compensation_type": compensation_type,
        "skills": [],
    }


def _classify_compensation_from_source(card_text: str, detail_text: str) -> str:
    primary_detail = detail_text
    for marker in ("Similar Jobs", "About the employer", "Alumni in similar roles", "Alumni at this employer"):
        if marker in primary_detail:
            primary_detail = primary_detail.split(marker, 1)[0]
    blob = f"{card_text}\n{primary_detail}".lower()
    if "unpaid" in blob:
        return "unpaid"
    if re.search(r"\$\s*\d", blob) or re.search(r"\b\d+\s*-\s*\d+\s*/\s*(hr|hour|year)\b", blob):
        return "paid"
    if re.search(r"\b(pay|paid|salary|stipend|compensation|hourly)\b", blob):
        return "paid"
    return "unknown"


def _infer_card_url(page_url: str, search_url: str, title: str) -> str:
    _ = title
    if "/job-search/" in page_url:
        return page_url
    return search_url


def _resolve_job_url(page, title: str, fallback_url: str) -> str:
    direct_links = page.locator("a").evaluate_all(
        """
        (els, targetTitle) => els
          .map((el) => ({ text: (el.innerText || '').trim(), href: el.href || '' }))
          .filter((item) => item.href.includes('/jobs/') && item.text === targetTitle)
        """,
        title,
    )
    if direct_links:
        return str(direct_links[0].get("href") or fallback_url)
    transformed = _job_search_url_to_jobs_url(fallback_url)
    return transformed or fallback_url


def _job_search_url_to_jobs_url(url: str) -> str:
    parsed = urlparse(url)
    match = re.match(r"^/job-search/(\d+)$", parsed.path)
    if not match:
        return ""
    return urlunparse(parsed._replace(path=f"/jobs/{match.group(1)}", query=""))


def _parse_card_text(card_text: str) -> dict[str, str] | None:
    lines = [line.strip() for line in card_text.splitlines() if line.strip()]
    if len(lines) < 4:
        return None
    company = lines[0]
    title = lines[1]
    location = lines[3]
    posted_at = _relative_age_to_iso(lines[4]) if len(lines) >= 5 else None
    return {
        "company": company,
        "title": title,
        "location": location,
        "posted_at": posted_at or "",
    }


def _parse_detail_text(detail_text: str) -> dict[str, str]:
    lines = [line.strip() for line in detail_text.splitlines() if line.strip()]
    if not lines:
        return {}

    title = ""
    company = ""
    location = ""
    posted_at = ""
    description = _trim_detail_text(lines) or detail_text

    for idx, line in enumerate(lines):
        if line.startswith("Posted "):
            posted_at = _relative_age_to_iso(line) or ""
            if idx >= 1 and not title:
                title = lines[idx - 1]
            if lines and not company:
                company = lines[0]
            break

    for idx, line in enumerate(lines):
        if line == "At a glance":
            for lookahead in lines[idx + 1 : idx + 8]:
                if "based in" in lookahead.lower() or "remote" in lookahead.lower():
                    location = lookahead
                    break
            break

    return {
        "title": title,
        "company": company,
        "location": location,
        "posted_at": posted_at,
        "description": description,
    }


def _trim_detail_text(lines: list[str]) -> str:
    kept: list[str] = []
    stop_markers = (
        "about the employer",
        "similar jobs",
        "alumni in similar roles",
        "alumni at this employer",
    )
    skipping_summary_beta = False
    for line in lines:
        lowered = line.lower()
        if any(marker in lowered for marker in stop_markers):
            break
        if lowered == "summary beta":
            skipping_summary_beta = True
            continue
        if skipping_summary_beta:
            if lowered in {"at a glance", "job description"}:
                skipping_summary_beta = False
            else:
                continue
        if kept and kept[-1].lower().startswith("save") and lowered.startswith("apply"):
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def _relative_age_to_iso(text: str) -> str | None:
    match = RELATIVE_AGE_RE.search(text)
    if match:
        amount = int(match.group(1))
        unit = match.group(2).lower()
        delta = _relative_unit_to_delta(amount, unit)
        if delta is not None:
            return (datetime.now(timezone.utc) - delta).date().isoformat()
    lowered = text.lower()
    if "new" == lowered.strip() or lowered.strip().endswith("∙ new") or lowered.strip().endswith("· new"):
        return datetime.now(timezone.utc).date().isoformat()
    if "today" in lowered:
        return datetime.now(timezone.utc).date().isoformat()
    if "yesterday" in lowered:
        return (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
    return None


def _relative_unit_to_delta(amount: int, unit: str) -> timedelta | None:
    if unit in {"hour", "hours", "hr", "hrs"}:
        return timedelta(hours=amount)
    if unit in {"day", "days", "d"}:
        return timedelta(days=amount)
    if unit in {"week", "weeks", "wk", "wks", "w"}:
        return timedelta(days=amount * 7)
    if unit in {"month", "months", "mo", "mos"}:
        return timedelta(days=amount * 30)
    return None


def _looks_like_card(meta: str, bullet: str, freshness: str) -> bool:
    if not re.search(r"\bintern(ship)?\b|\bco[- ]?op\b", meta, flags=re.IGNORECASE):
        return False
    if bullet != "∙":
        return False
    return bool(
        re.match(
            r"^(new|\d+\s*(hours?|hrs?|hr|days?|d|weeks?|wks?|wk|w|months?|mos?|mo)\s+ago)$",
            freshness,
            flags=re.IGNORECASE,
        )
    )
