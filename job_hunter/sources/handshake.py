from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from hashlib import sha1
from pathlib import Path
from urllib.parse import urljoin

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from job_hunter.sources.base import SourceConnector

LOG = logging.getLogger(__name__)
DETAIL_TEXT_SCRIPT = """
() => {
  let best = null;
  let bestArea = 0;
  for (const node of Array.from(document.querySelectorAll('main, section, article, div'))) {
    const rect = node.getBoundingClientRect();
    const text = (node.innerText || '').trim();
    if (!text || !text.includes('Job description')) continue;
    if (rect.left < window.innerWidth * 0.35) continue;
    const area = rect.width * rect.height;
    if (area <= bestArea) continue;
    bestArea = area;
    best = text;
  }
  return best || '';
}
"""
POSTED_RELATIVE_RE = re.compile(r"\bPosted\s+(\d+)\s+days?\s+ago\b", re.IGNORECASE)
CARD_AGE_RE = re.compile(r"\b(\d+)\s*d\s+ago\b", re.IGNORECASE)


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
                    results.extend(self._fetch_search_page(page, search_url))
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
        "skills": [],
    }


def _infer_card_url(page_url: str, search_url: str, title: str) -> str:
    _ = title
    if "/job-search/" in page_url:
        return page_url
    return search_url


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
    description = detail_text

    for idx, line in enumerate(lines):
        if line.startswith("Posted "):
            posted_at = _relative_age_to_iso(line) or ""
            if idx >= 1 and not title:
                title = lines[idx - 1]
            if lines and not company:
                company = lines[0]
            break

    for idx, line in enumerate(lines):
        if line == "Job description":
            description = "\n".join(lines[idx + 1 :]).strip()
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


def _relative_age_to_iso(text: str) -> str | None:
    match = POSTED_RELATIVE_RE.search(text)
    if match:
        days = int(match.group(1))
        return (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    match = CARD_AGE_RE.search(text)
    if match:
        days = int(match.group(1))
        return (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    lowered = text.lower()
    if "new" == lowered.strip() or lowered.strip().endswith("∙ new") or lowered.strip().endswith("· new"):
        return datetime.now(timezone.utc).date().isoformat()
    if "today" in lowered:
        return datetime.now(timezone.utc).date().isoformat()
    if "yesterday" in lowered:
        return (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
    return None


def _looks_like_card(meta: str, bullet: str, freshness: str) -> bool:
    if not re.search(r"\bintern(ship)?\b|\bco[- ]?op\b", meta, flags=re.IGNORECASE):
        return False
    if bullet != "∙":
        return False
    return bool(re.match(r"^(new|\d+[dhw] ago)$", freshness, flags=re.IGNORECASE))
