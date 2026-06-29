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
SECURITY_VERIFICATION_MARKERS = (
    "performing security verification",
    "this website uses a security service to protect against malicious bots",
    "performance and security by cloudflare",
)


class HandshakeSecurityVerificationError(RuntimeError):
    pass
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
SUMMARY_BETA_POLLUTION_PHRASES = (
    "aligns closely with the user's query",
    "aligns closely with the user's interest",
    "this job description highlights",
    "this role as a",
    "highly relevant to the user's interest",
)
SUMMARY_BETA_SECTION_RE = re.compile(
    r"summary beta\b.*?(?=(at a glance\b|job description\b|what they're looking for\b|about the employer\b|similar jobs\b))",
    flags=re.IGNORECASE | re.DOTALL,
)
SUMMARY_BETA_INLINE_PREFIX_RE = re.compile(
    r"^(.*?)(summary beta\b.*)$",
    flags=re.IGNORECASE,
)


class HandshakeSource(SourceConnector):
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
        super().__init__(name="handshake")
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
        security_verification_blocked_count = 0
        item_results: list[dict[str, str]] = []

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
                search_urls, job_urls = _partition_handshake_urls(self.search_urls)
                for search_url in search_urls:
                    normalized_search_url = _normalize_search_url(search_url)
                    try:
                        rows = self._fetch_search_page(page, normalized_search_url)
                    except HandshakeSecurityVerificationError as exc:
                        security_verification_blocked_count += 1
                        item_results.append({"item": normalized_search_url, "status": "failure", "error": str(exc)})
                        LOG.warning("handshake_search_security_verification_blocked url=%s", normalized_search_url)
                        continue
                    item_results.append({"item": normalized_search_url, "status": "success", "error": ""})
                    results.extend(rows)
                for job_url in job_urls:
                    try:
                        parsed = self._fetch_job_page(page, job_url)
                    except HandshakeSecurityVerificationError as exc:
                        security_verification_blocked_count += 1
                        item_results.append({"item": job_url, "status": "failure", "error": str(exc)})
                        LOG.warning("handshake_job_security_verification_blocked url=%s", job_url)
                        continue
                    item_results.append({"item": job_url, "status": "success", "error": ""})
                    if parsed is not None:
                        results.append(parsed)
                self._fetch_meta = {
                    "security_verification_blocked_count": security_verification_blocked_count,
                    "item_results": item_results,
                }
                return _dedupe_rows(results)
            finally:
                context.close()

    def get_fetch_meta(self) -> dict[str, object]:
        return dict(self._fetch_meta)

    def _fetch_search_page(self, page, search_url: str) -> list[dict]:
        self._goto_handshake_page(page, search_url)
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
            if _is_card_older_than_lookback(card, self.max_posting_age_days):
                break
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
            detail_fetch_attempted = self.fetch_details
            detail_click_succeeded = False
            detail_fetch_mode = "none"
            if self.fetch_details:
                discovered_url = _discover_card_url(page, title)
                if discovered_url:
                    card_url = _job_search_url_to_jobs_url(discovered_url) or discovered_url
                locator = _find_detail_trigger(page, title)
                try:
                    if locator is not None:
                        card_url = _job_search_url_to_jobs_url(
                            urljoin(page.url, locator.get_attribute("href") or card_url)
                        ) or card_url
                        locator.click(timeout=5000)
                        detail_click_succeeded = True
                        detail_fetch_mode = "panel_click"
                        page.wait_for_timeout(1500)
                        expanded = int(page.evaluate(EXPAND_MORE_SCRIPT) or 0)
                        if expanded:
                            page.wait_for_timeout(750)
                        card_url = _resolve_job_url(page, title, card_url)
                        detail_text = str(page.evaluate(DETAIL_TEXT_SCRIPT) or "")
                except PlaywrightTimeoutError:
                    detail_text = ""
                if not detail_text.strip() and card_url:
                    fallback_detail_text = self._fetch_detail_text_from_job_url(page.context, card_url)
                    if fallback_detail_text.strip():
                        detail_text = fallback_detail_text
                        detail_fetch_mode = "direct_page_fallback"
            if not card_url:
                card_url = _infer_card_url(page.url, search_url, title)
            parsed = _build_row(
                card_text=card_text,
                detail_text=detail_text,
                search_url=search_url,
                card_url=card_url,
                detail_fetch_attempted=detail_fetch_attempted,
                detail_click_succeeded=detail_click_succeeded,
                detail_fetch_mode=detail_fetch_mode,
            )
            if parsed is not None:
                rows.append(parsed)
        return rows

    def _fetch_job_page(self, page, job_url: str) -> dict | None:
        self._goto_handshake_page(page, job_url, post_wait_ms=1500)
        if "joinhandshake.com/login" in page.url or "users/sign_in" in page.url:
            raise RuntimeError(
                "Handshake session not authenticated. Run `python -m job_hunter.handshake_login` first."
            )
        expanded = int(page.evaluate(EXPAND_MORE_SCRIPT) or 0)
        if expanded:
            page.wait_for_timeout(750)
        detail_text = str(page.evaluate(DETAIL_TEXT_SCRIPT) or "")
        if not detail_text.strip():
            detail_text = str(page.locator("body").inner_text() or "")
        return _build_row_from_job_page(
            detail_text=detail_text,
            job_url=job_url,
            detail_fetch_attempted=True,
            detail_click_succeeded=True,
        )

    def _fetch_detail_text_from_job_url(self, context, job_url: str) -> str:
        detail_page = context.new_page()
        detail_page.set_default_timeout(self.page_timeout_seconds * 1000)
        try:
            self._goto_handshake_page(detail_page, job_url, post_wait_ms=1500)
            if "joinhandshake.com/login" in detail_page.url or "users/sign_in" in detail_page.url:
                return ""
            expanded = int(detail_page.evaluate(EXPAND_MORE_SCRIPT) or 0)
            if expanded:
                detail_page.wait_for_timeout(750)
            detail_text = str(detail_page.evaluate(DETAIL_TEXT_SCRIPT) or "")
            if not detail_text.strip():
                detail_text = str(detail_page.locator("body").inner_text() or "")
            return detail_text
        except PlaywrightTimeoutError:
            return ""
        finally:
            detail_page.close()

    def _goto_handshake_page(self, page, url: str, *, post_wait_ms: int = 3000) -> None:
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(post_wait_ms)
        if not _page_body_has_security_verification(page):
            return
        LOG.warning("handshake_security_verification_waiting url=%s", url)
        for wait_ms in (5000, 10000):
            page.wait_for_timeout(wait_ms)
            if not _page_body_has_security_verification(page):
                LOG.info("handshake_security_verification_cleared url=%s wait_ms=%s", url, wait_ms)
                return
        raise HandshakeSecurityVerificationError(f"Handshake security verification blocked fetch for url={url}")


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


def _partition_handshake_urls(urls: list[str]) -> tuple[list[str], list[str]]:
    search_urls: list[str] = []
    job_urls: list[str] = []
    for value in urls:
        parsed = urlparse(value)
        if "/jobs/" in parsed.path:
            job_urls.append(value)
        else:
            search_urls.append(value)
    return search_urls, job_urls


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


def _is_card_older_than_lookback(card: dict[str, str], max_posting_age_days: int) -> bool:
    posted_at = _relative_age_to_iso(str(card.get("freshness") or ""))
    if not posted_at:
        return False
    try:
        posted_date = datetime.fromisoformat(posted_at).date()
    except ValueError:
        return False
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_posting_age_days)).date()
    return posted_date < cutoff


def _build_row(
    card_text: str,
    detail_text: str,
    search_url: str,
    card_url: str = "",
    *,
    detail_fetch_attempted: bool = True,
    detail_click_succeeded: bool = False,
    detail_fetch_mode: str = "none",
) -> dict | None:
    raw_detail_text = detail_text
    cleaned_detail_text = _clean_summary_beta_text(detail_text)
    card = _parse_card_text(card_text)
    if card is None:
        return None
    detail = _parse_detail_text(cleaned_detail_text)
    detail_title = str(detail.get("title") or "").strip()
    recovered_from_card_title = _looks_like_polluted_title(detail_title)
    title = card["title"] if recovered_from_card_title else (detail_title or card["title"])
    company = detail.get("company") or card["company"]
    location = detail.get("location") or card["location"]
    posted_at = detail.get("posted_at") or card["posted_at"]
    external_id = f"{company}|{title}|{location}|{posted_at or ''}"
    url = card_url or f"{search_url}#jobhunter-{sha1(external_id.encode('utf-8')).hexdigest()[:12]}"
    source_metadata = _build_source_metadata(
        card=card,
        detail=detail,
        effective_title=title,
        card_text=card_text,
        raw_detail_text=raw_detail_text,
        cleaned_detail_text=cleaned_detail_text,
        url=url,
        detail_fetch_attempted=detail_fetch_attempted,
        detail_click_succeeded=detail_click_succeeded,
        detail_fetch_mode=detail_fetch_mode,
    )
    description = _choose_description(
        card_text=card_text,
        cleaned_detail_text=cleaned_detail_text,
        parsed_detail_description=str(detail.get("description") or ""),
        detail_quality_status=str(source_metadata.get("detail_quality_status") or ""),
    )
    compensation_type = _classify_compensation_from_source(card_text=card_text, detail_text=cleaned_detail_text)
    return {
        "source": "handshake",
        "source_detail": search_url,
        "source_metadata": source_metadata,
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


def _build_row_from_job_page(
    *,
    detail_text: str,
    job_url: str,
    detail_fetch_attempted: bool,
    detail_click_succeeded: bool,
) -> dict | None:
    raw_detail_text = detail_text
    cleaned_detail_text = _clean_summary_beta_text(detail_text)
    detail = _parse_detail_text(cleaned_detail_text)
    title = str(detail.get("title") or "").strip()
    company = str(detail.get("company") or "").strip()
    location = str(detail.get("location") or "").strip()
    posted_at = str(detail.get("posted_at") or "").strip()
    if not title or not company:
        return None
    card_text = "\n".join(part for part in [company, title, location, posted_at] if part)
    source_metadata = _build_source_metadata(
        card={"title": title, "company": company},
        detail=detail,
        effective_title=title,
        card_text=card_text,
        raw_detail_text=raw_detail_text,
        cleaned_detail_text=cleaned_detail_text,
        url=job_url,
        detail_fetch_attempted=detail_fetch_attempted,
        detail_click_succeeded=detail_click_succeeded,
        detail_fetch_mode="direct_job_page",
    )
    description = _choose_description(
        card_text=card_text,
        cleaned_detail_text=cleaned_detail_text,
        parsed_detail_description=str(detail.get("description") or ""),
        detail_quality_status=str(source_metadata.get("detail_quality_status") or ""),
    )
    compensation_type = _classify_compensation_from_source(card_text=card_text, detail_text=cleaned_detail_text)
    external_id = f"{company}|{title}|{location}|{posted_at}"
    return {
        "source": "handshake",
        "source_detail": job_url,
        "source_metadata": source_metadata,
        "external_id": external_id,
        "url": job_url,
        "title": title,
        "company": company,
        "location": location,
        "posted_at": posted_at,
        "description": description,
        "compensation_type": compensation_type,
        "skills": [],
    }


def _choose_description(
    *,
    card_text: str,
    cleaned_detail_text: str,
    parsed_detail_description: str,
    detail_quality_status: str,
) -> str:
    if detail_quality_status in {"detail_polluted", "detail_mismatch"}:
        if parsed_detail_description.strip():
            return parsed_detail_description
        return card_text
    return parsed_detail_description or cleaned_detail_text or card_text


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


def _page_body_has_security_verification(page) -> bool:
    try:
        body_text = str(page.locator("body").inner_text() or "")
    except Exception:
        return False
    lowered = body_text.lower()
    return all(marker in lowered for marker in SECURITY_VERIFICATION_MARKERS)


def _discover_card_url(page, title: str) -> str:
    candidates = page.locator("a").evaluate_all(
        """
        (els, targetTitle) => els
          .map((el) => ({ text: (el.innerText || '').trim(), href: el.href || '' }))
          .filter((item) => item.href.includes('/jobs/') || item.href.includes('/job-search/'))
        """,
        title,
    )
    return _select_best_card_url(candidates, title)


def _select_best_card_url(candidates: list[dict[str, str]], title: str) -> str:
    target = _normalize_title_token(title)
    for candidate in candidates:
        text = str(candidate.get("text") or "")
        href = str(candidate.get("href") or "")
        if not href:
            continue
        normalized_text = _normalize_title_token(text)
        if normalized_text == target:
            return href
    for candidate in candidates:
        text = str(candidate.get("text") or "")
        href = str(candidate.get("href") or "")
        if not href:
            continue
        normalized_text = _normalize_title_token(text)
        if target and target in normalized_text:
            return href
    return ""


def _find_detail_trigger(page, title: str):
    for role in ("button", "link"):
        locator = page.get_by_role(role, name=title, exact=False).first
        try:
            locator.wait_for(timeout=1000)
            return locator
        except PlaywrightTimeoutError:
            continue
    return None


def _job_search_url_to_jobs_url(url: str) -> str:
    parsed = urlparse(url)
    match = re.match(r"^/job-search/(\d+)$", parsed.path)
    if not match:
        return ""
    return urlunparse(parsed._replace(path=f"/jobs/{match.group(1)}", query=""))


def _build_source_metadata(
    *,
    card: dict[str, str],
    detail: dict[str, str],
    effective_title: str,
    card_text: str,
    raw_detail_text: str,
    cleaned_detail_text: str,
    url: str,
    detail_fetch_attempted: bool,
    detail_click_succeeded: bool,
    detail_fetch_mode: str,
) -> dict[str, object]:
    raw_detail = raw_detail_text.strip()
    cleaned_detail = cleaned_detail_text.strip()
    normalized_description = str(detail.get("description") or "").strip()
    detail_title = str(detail.get("title") or "").strip()
    detail_title_polluted = _looks_like_polluted_title(detail_title)
    effective_title_matches_card = _normalize_title_token(effective_title) == _normalize_title_token(card["title"])
    title_matches = effective_title_matches_card
    contains_job_description = "job description" in cleaned_detail.lower()
    contains_at_a_glance = "at a glance" in cleaned_detail.lower()
    had_summary_beta = _looks_like_summary_beta_pollution(raw_detail)
    description_polluted = _looks_like_summary_beta_pollution(normalized_description) or _looks_like_summary_beta_pollution(cleaned_detail)
    detail_polluted = (detail_title_polluted and not effective_title_matches_card) or description_polluted
    detail_complete = (
        bool(cleaned_detail)
        and title_matches
        and not detail_polluted
        and contains_job_description
        and len(normalized_description) > len(card_text)
    )
    fallback_reason = ""

    if not cleaned_detail:
        detail_quality_status = "card_only"
        fallback_reason = "missing_detail_text"
    elif detail_polluted:
        detail_quality_status = "detail_polluted"
        fallback_reason = "polluted_title" if detail_title_polluted and not effective_title_matches_card else "summary_beta_pollution"
    elif not title_matches:
        detail_quality_status = "detail_mismatch"
        fallback_reason = "title_mismatch"
    elif detail_complete:
        detail_quality_status = "detail_complete"
    else:
        detail_quality_status = "detail_partial"
        if not contains_job_description:
            fallback_reason = "missing_job_description_marker"
        elif len(normalized_description) <= len(card_text):
            fallback_reason = "detail_not_richer_than_card"

    return {
        "detail_fetch_attempted": detail_fetch_attempted,
        "detail_click_succeeded": detail_click_succeeded,
        "detail_fetch_mode": detail_fetch_mode,
        "detail_panel_found": bool(cleaned_detail),
        "detail_contains_job_description": contains_job_description,
        "detail_contains_at_a_glance": contains_at_a_glance,
        "detail_text_length": len(cleaned_detail),
        "detail_title_matches_card": title_matches,
        "detail_title_polluted": detail_title_polluted,
        "detail_title_recovered_from_card": detail_title_polluted and effective_title_matches_card,
        "detail_had_summary_beta": had_summary_beta,
        "detail_quality_status": detail_quality_status,
        "detail_fallback_reason": fallback_reason,
        "resolved_job_url": url,
    }


def _normalize_title_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _looks_like_summary_beta_pollution(value: str) -> bool:
    lowered = value.lower()
    if "summary beta" in lowered:
        return True
    return any(phrase in lowered for phrase in SUMMARY_BETA_POLLUTION_PHRASES)


def _looks_like_polluted_title(value: str) -> bool:
    title = value.strip()
    if not title:
        return False
    lowered = title.lower()
    if _looks_like_summary_beta_pollution(title):
        return True
    if " is seeking " in lowered or " is looking for " in lowered:
        return True
    if len(title) >= 140:
        return True
    if title.count(".") >= 2:
        return True
    return False


def _clean_summary_beta_text(value: str) -> str:
    if not value:
        return value
    cleaned = value
    previous = None
    while previous != cleaned:
        previous = cleaned
        cleaned = SUMMARY_BETA_SECTION_RE.sub("", cleaned)
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


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

    posted_idx = _find_posted_line_index(lines)
    title = ""
    company = ""
    location = ""
    posted_at = ""
    description = _trim_detail_text(lines, posted_idx=posted_idx) or detail_text

    if posted_idx is not None:
        posted_at = _relative_age_to_iso(lines[posted_idx]) or ""
        if posted_idx >= 1 and not title:
            title = lines[posted_idx - 1]
        company = _extract_company_near_posted(lines, posted_idx) or company
    elif lines:
        company = lines[0]

    for idx, line in enumerate(lines):
        if line == "At a glance":
            for lookahead in lines[idx + 1 : idx + 8]:
                if "based in" in lookahead.lower() or "remote" in lookahead.lower():
                    location = lookahead
                    break
            break

    refined_title = _extract_job_description_title(lines)
    if _should_prefer_refined_title(refined_title, title):
        title = refined_title

    return {
        "title": title,
        "company": company,
        "location": location,
        "posted_at": posted_at,
        "description": description,
    }


def _trim_detail_text(lines: list[str], *, posted_idx: int | None = None) -> str:
    kept: list[str] = []
    stop_markers = (
        "about the employer",
        "similar jobs",
        "alumni in similar roles",
        "alumni at this employer",
        "what they're looking for",
        "what this job offers",
    )
    skipping_summary_beta = False
    start_idx = _detail_content_start_index(lines, posted_idx)
    for line in lines[start_idx:]:
        lowered = line.lower()
        if any(marker in lowered for marker in stop_markers):
            break
        if "summary beta" in lowered:
            matched = SUMMARY_BETA_INLINE_PREFIX_RE.match(line)
            if matched:
                prefix = matched.group(1).strip()
                if prefix:
                    kept.append(prefix)
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


def _find_posted_line_index(lines: list[str]) -> int | None:
    for idx, line in enumerate(lines):
        if line.startswith("Posted "):
            return idx
    return None


def _extract_company_near_posted(lines: list[str], posted_idx: int) -> str:
    if posted_idx >= 3:
        return lines[posted_idx - 3]
    if lines:
        return lines[0]
    return ""


def _detail_content_start_index(lines: list[str], posted_idx: int | None) -> int:
    if posted_idx is None:
        return 0
    if posted_idx >= 3:
        return posted_idx - 3
    return 0


def _extract_job_description_title(lines: list[str]) -> str:
    for idx, line in enumerate(lines):
        if line.lower() != "job description":
            continue
        for candidate in lines[idx + 1 : idx + 6]:
            lowered = candidate.lower()
            if lowered in {"at a glance", "about the employer", "similar jobs"}:
                break
            if "intern" in lowered:
                return candidate
    return ""


def _should_prefer_refined_title(candidate: str, current: str) -> bool:
    candidate = candidate.strip()
    current = current.strip()
    if not candidate:
        return False
    if not current:
        return True
    if candidate == current:
        return False
    generic_current = re.fullmatch(
        r"(ai|ml|software|data|backend|platform)?\s*engineering?\s+intern(ship)?|(software|engineering|data)\s+intern(ship)?|intern(ship)?",
        current.lower(),
    )
    if generic_current and len(candidate) > len(current):
        return True
    if current.lower() in candidate.lower() and len(candidate) > len(current):
        return True
    return False


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
