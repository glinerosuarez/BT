from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from hashlib import sha1
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from job_hunter.sources.base import SourceConnector

LOG = logging.getLogger(__name__)

DETAIL_TEXT_SCRIPT = """
() => {
  const direct = document.querySelector('[data-view-name="job-detail-page"]');
  if (direct && (direct.innerText || '').trim()) {
    return (direct.innerText || '').trim();
  }
  let best = '';
  for (const node of Array.from(document.querySelectorAll('main, section, article, div'))) {
    const text = (node.innerText || '').trim();
    if (!text) continue;
    if (text.includes('About the job') || text.includes('Acerca del empleo') || text.includes('Job description')) {
      if (text.length > best.length) best = text;
    }
  }
  return best;
}
"""

EXPAND_MORE_SCRIPT = """
() => {
  let clicked = 0;
  for (const node of Array.from(document.querySelectorAll('button, a, div[role="button"], span[role="button"]'))) {
    const text = (node.innerText || '').trim();
    if (!text) continue;
    if (text !== 'Show more' && text !== 'See more' && text !== 'more') continue;
    const rect = node.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) continue;
    node.click();
    clicked += 1;
  }
  return clicked;
}
"""

JOB_CANDIDATES_SCRIPT = """
() => {
  const rows = [];
  const cards = Array.from(document.querySelectorAll('[data-view-name="job-search-job-card"]'));
  for (const card of cards) {
    const text = (card.innerText || '').trim();
    if (!text) continue;
    const anchor = card.querySelector('a[href*="/jobs/view/"]');
    rows.push({
      card_text: text,
      card_url: anchor ? (anchor.href || '') : '',
    });
  }
  return rows;
}
"""

RELATIVE_AGE_RE = re.compile(
    r"\b(?:(?:reposted|posted)\s*)?(?:(?:hace)\s*)?(\d+)\s*(hours?|hrs?|hr|days?|d|weeks?|wks?|wk|months?|mos?|mo|horas?|d[ií]as?|semanas?|meses?)\s*(?:ago)?\b",
    re.IGNORECASE,
)
JOB_URL_RE = re.compile(r"/jobs/view/(?:[^/]+/)?(\d+)")
LOCATION_HINT_RE = re.compile(
    r"\b(remote|remoto|hybrid|híbrido|on-site|onsite|presencial|united states|estados unidos|new york|nueva york|san francisco|los angeles|seattle|austin|boston|chicago|washington, dc|san jos[eé])\b",
    re.IGNORECASE,
)
LINKEDIN_CLOSED_PATTERNS = (
    "no longer accepting applications",
    "ya no acepta solicitudes",
    "ya no se aceptan solicitudes",
)
GENERIC_NOISE_LINES = {
    "jobs",
    "search",
    "saved",
    "easy apply",
    "apply",
    "join now",
    "sign in",
    "show more",
    "show less",
    "see more",
    "see less",
    "guardar",
    "solicitar",
    "solicitud sencilla",
    "visto",
    "empleos",
    "buscar",
    "adelántate a solicitar el empleo",
    "evaluando solicitudes de forma activa",
}
STOP_MARKERS = {
    "seniority level",
    "employment type",
    "job function",
    "industries",
    "get job alerts",
    "similar jobs",
    "people also viewed",
    "set alert",
    "benefits",
    "people you can reach out to",
    "about the company",
    "more jobs",
    "see more jobs like this",
    "looking for talent?",
    "questions? visit our help center.",
    "personas con las que puedes hablar",
    "more jobs",
    "ofertas de empleo similares",
    "más ofertas de empleo",
    "información exclusiva sobre",
    "about the company",
    "tendencia de contratación",
    "acerca de la empresa",
    "interested in working with us in the future?",
    "interesado en trabajar con nosotros en el futuro?",
    "trending employee content",
}

DETAIL_SPLIT_MARKERS = (
    "About the job",
    "Acerca del empleo",
    "Job description",
    "Save",
    "Apply",
    "Easy Apply",
    "Show match details",
    "Tailor my resume",
    "Create cover letter",
    "Help me stand out",
    "People you can reach out to",
    "Set alert for similar jobs",
    "Benefits found in job post",
    "About the company",
    "More jobs",
    "See more jobs like this",
    "Looking for talent?",
    "Questions? Visit our Help Center.",
    "Personas con las que puedes hablar",
    "Información exclusiva sobre",
    "Acerca de la empresa",
    "Más ofertas de empleo",
    "Tendencia de contratación",
)

LEADING_NOISE_PREFIXES = (
    "0 notifications",
    "ir al contenido principal",
    "skip to main content",
    "skip to primary content",
    "skip to contenido principal",
    "skip to al margen",
    "skip to aside",
    "skip to pie de página",
    "skip to footer",
    "inicio",
    "home",
    "mi red",
    "my network",
    "empleos",
    "jobs",
    "mensajes",
    "messaging",
    "notificaciones",
    "notifications",
    "yo",
    "me",
    "para negocios",
    "for business",
    "learning",
)

INVALID_HEADER_PREFIXES = (
    "save",
    "guardar",
    "notifications",
    "notificaciones",
    "use ai to assess how you fit",
    "utiliza ia para evaluar cómo encajas",
    "show match details",
    "tailor my resume",
    "create cover letter",
    "help me stand out",
)


class LinkedInSource(SourceConnector):
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
        super().__init__(name="linkedin")
        self.search_urls = search_urls
        self.profile_dir = profile_dir
        self.headless = headless
        self.max_results = max(max_results, 1)
        self.page_timeout_seconds = max(page_timeout_seconds, 5)
        self.max_posting_age_days = max(max_posting_age_days, 1)
        self.fetch_details = fetch_details

    def fetch(self, timeout_seconds: int) -> list[dict]:
        _ = timeout_seconds
        profile_path = Path(self.profile_dir).expanduser()
        profile_path.mkdir(parents=True, exist_ok=True)

        with sync_playwright() as playwright:
            results: list[dict] = []
            search_urls, job_urls = _partition_linkedin_urls(self.search_urls)
            total_search_urls = len(search_urls)
            for index, search_url in enumerate(search_urls, start=1):
                normalized_search_url = _normalize_search_url(search_url)
                LOG.info(
                    "linkedin_search_fetch_started index=%s total=%s url=%s",
                    index,
                    total_search_urls,
                    normalized_search_url,
                )
                context = self._launch_context(playwright, profile_path)
                try:
                    query_page = context.new_page()
                    query_page.set_default_timeout(self.page_timeout_seconds * 1000)
                    query_page.set_default_navigation_timeout(self.page_timeout_seconds * 1000)
                    try:
                        rows = self._fetch_search_page(query_page, normalized_search_url)
                    finally:
                        query_page.close()
                finally:
                    context.close()
                LOG.info(
                    "linkedin_search_fetch_finished index=%s total=%s url=%s fetched_count=%s",
                    index,
                    total_search_urls,
                    normalized_search_url,
                    len(rows),
                )
                results.extend(rows)
            for job_url in job_urls:
                context = self._launch_context(playwright, profile_path)
                try:
                    job_page = context.new_page()
                    job_page.set_default_timeout(self.page_timeout_seconds * 1000)
                    job_page.set_default_navigation_timeout(self.page_timeout_seconds * 1000)
                    try:
                        parsed = self._fetch_job_page(job_page, job_url)
                        if parsed is not None:
                            results.append(parsed)
                    finally:
                        job_page.close()
                finally:
                    context.close()
            return _dedupe_rows(results)

    def _launch_context(self, playwright, profile_path: Path):
        context = playwright.chromium.launch_persistent_context(
            str(profile_path),
            channel="chrome",
            headless=self.headless,
        )
        context.set_default_timeout(self.page_timeout_seconds * 1000)
        context.set_default_navigation_timeout(self.page_timeout_seconds * 1000)
        return context

    def _fetch_search_page(self, page, search_url: str) -> list[dict]:
        self._goto_linkedin_page(page, search_url)
        if _page_requires_login(page.url):
            raise RuntimeError("LinkedIn session not authenticated. Run `python -m job_hunter.linkedin_login` first.")

        candidates = self._load_search_candidates(page, search_url)
        rows: list[dict] = []
        max_cards = min(len(candidates), self.max_results)
        for card_index, candidate in enumerate(candidates[: self.max_results], start=1):
            card_text = str(candidate.get("card_text") or "").strip()
            candidate_url = _canonical_linkedin_job_url(str(candidate.get("card_url") or "").strip())
            card = _parse_card_text(card_text, fallback_url=candidate_url or search_url)
            title = str(card.get("title") or "").strip()
            if not title:
                continue
            if bool(card.get("is_reposted")):
                LOG.info(
                    "linkedin_card_skipped_reposted url=%s card_index=%s max_cards=%s title=%s",
                    search_url,
                    card_index,
                    max_cards,
                    title,
                )
                continue
            if _is_card_older_than_lookback(card, self.max_posting_age_days):
                LOG.info(
                    "linkedin_search_stopped_on_age url=%s card_index=%s max_cards=%s title=%s",
                    search_url,
                    card_index,
                    max_cards,
                    title,
                )
                break

            LOG.info(
                "linkedin_card_fetch_started url=%s card_index=%s max_cards=%s title=%s",
                search_url,
                card_index,
                max_cards,
                title,
            )
            detail_text = ""
            job_url = ""
            locator = page.locator('[data-view-name="job-search-job-card"]').nth(card_index - 1)
            locator_url = self._extract_card_job_url(locator)
            if locator_url:
                card["url"] = locator_url
                candidate_url = locator_url
            if self.fetch_details:
                click_succeeded = False
                try:
                    locator.scroll_into_view_if_needed(timeout=3000)
                    locator.click(timeout=5000)
                    page.wait_for_timeout(2000)
                    click_succeeded = True
                except PlaywrightTimeoutError:
                    try:
                        locator.evaluate("(el) => el.click()")
                        page.wait_for_timeout(2000)
                        click_succeeded = True
                        LOG.info(
                            "linkedin_card_click_fallback_used url=%s card_index=%s title=%s",
                            search_url,
                            card_index,
                            title,
                        )
                    except Exception:
                        LOG.warning("linkedin_card_click_timeout url=%s card_index=%s title=%s", search_url, card_index, title)
                if click_succeeded:
                    locator_url = self._extract_card_job_url(locator)
                    job_url = locator_url or candidate_url or _canonical_linkedin_job_url(page.url)
                    if not job_url:
                        selected_links = page.locator("a[href*='/jobs/view/']").evaluate_all(
                            """
                            (els) => els.map((el) => ({ text: (el.innerText || '').trim(), href: el.href || '' }))
                            """
                        )
                        if selected_links:
                            job_url = _canonical_linkedin_job_url(str(selected_links[0].get("href") or ""))
                    if job_url:
                        card["url"] = job_url
                        detail_text = self._fetch_detail_text_from_job_url(page.context, job_url)
            parsed = _build_row(
                card=card,
                detail_text=detail_text,
                search_url=search_url,
                detail_fetch_attempted=self.fetch_details,
            )
            if parsed is not None:
                rows.append(parsed)
                LOG.info(
                    "linkedin_card_fetch_finished url=%s card_index=%s max_cards=%s title=%s quality=%s resolved_url=%s",
                    search_url,
                    card_index,
                    max_cards,
                    title,
                    str(parsed.get("source_metadata", {}).get("detail_quality_status") or ""),
                    str(parsed.get("url") or ""),
                )
        return rows

    def _extract_card_job_url(self, locator) -> str:
        try:
            href = locator.locator("a[href*='/jobs/view/']").first.get_attribute("href", timeout=2000)
        except Exception:
            return ""
        return _canonical_linkedin_job_url(str(href or "").strip())

    def _load_search_candidates(self, page, search_url: str) -> list[dict]:
        wait_schedule_ms = (1000, 2500, 5000)
        candidates: list[dict] = []
        for attempt, wait_ms in enumerate(wait_schedule_ms, start=1):
            page.wait_for_timeout(wait_ms)
            raw_candidates = page.evaluate(JOB_CANDIDATES_SCRIPT) or []
            candidates = [item for item in raw_candidates if isinstance(item, dict) and str(item.get("card_text") or "").strip()]
            if candidates:
                if attempt > 1:
                    LOG.info(
                        "linkedin_search_candidates_retried url=%s attempt=%s candidate_count=%s",
                        search_url,
                        attempt,
                        len(candidates),
                    )
                return candidates
        LOG.warning("linkedin_search_candidates_empty url=%s", search_url)
        return candidates

    def _fetch_job_page(self, page, job_url: str) -> dict | None:
        self._goto_linkedin_page(page, job_url)
        if _page_requires_login(page.url):
            raise RuntimeError("LinkedIn session not authenticated. Run `python -m job_hunter.linkedin_login` first.")
        expanded = int(page.evaluate(EXPAND_MORE_SCRIPT) or 0)
        if expanded:
            page.wait_for_timeout(750)
        detail_text = str(page.evaluate(DETAIL_TEXT_SCRIPT) or "")
        if not detail_text.strip():
            detail_text = str(page.locator("body").inner_text() or "")
        detail = _parse_detail_text(detail_text)
        if bool(detail.get("is_reposted")):
            LOG.info("linkedin_job_skipped_reposted url=%s", job_url)
            return None
        if not detail.get("title") or not detail.get("company"):
            return None
        card = {
            "title": str(detail.get("title") or ""),
            "company": str(detail.get("company") or ""),
            "location": str(detail.get("location") or ""),
            "posted_at": str(detail.get("posted_at") or ""),
            "url": _canonical_linkedin_job_url(job_url),
            "card_text": "",
        }
        return _build_row(card=card, detail_text=detail_text, search_url=job_url, detail_fetch_attempted=True)

    def _fetch_detail_text_from_job_url(self, context, job_url: str) -> str:
        detail_page = context.new_page()
        detail_page.set_default_timeout(self.page_timeout_seconds * 1000)
        detail_page.set_default_navigation_timeout(self.page_timeout_seconds * 1000)
        try:
            self._goto_linkedin_page(detail_page, job_url)
            if _page_requires_login(detail_page.url):
                return ""
            expanded = int(detail_page.evaluate(EXPAND_MORE_SCRIPT) or 0)
            if expanded:
                detail_page.wait_for_timeout(750)
            detail_text = str(detail_page.evaluate(DETAIL_TEXT_SCRIPT) or "")
            if not detail_text.strip():
                detail_text = str(detail_page.locator("body").inner_text() or "")
            return detail_text
        except PlaywrightTimeoutError:
            LOG.warning("linkedin_direct_detail_timeout url=%s", job_url)
            return ""
        finally:
            detail_page.close()

    def _goto_linkedin_page(self, page, url: str, *, post_wait_ms: int = 2500) -> None:
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(post_wait_ms)


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


def _partition_linkedin_urls(urls: list[str]) -> tuple[list[str], list[str]]:
    search_urls: list[str] = []
    job_urls: list[str] = []
    for value in urls:
        parsed = urlparse(value)
        if "/jobs/view/" in parsed.path:
            job_urls.append(value)
        else:
            search_urls.append(value)
    return search_urls, job_urls


def _normalize_search_url(search_url: str) -> str:
    parsed = urlparse(search_url)
    query_pairs = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True) if key != "sortBy"]
    query_pairs.append(("sortBy", "DD"))
    normalized_query = urlencode(query_pairs, doseq=True)
    return urlunparse(parsed._replace(query=normalized_query))


def _canonical_linkedin_job_url(url: str) -> str:
    value = url.strip()
    if not value:
        return value
    parsed = urlparse(value)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    current_job_id = str(query.get("currentJobId") or "").strip()
    if current_job_id.isdigit():
        return urlunparse(parsed._replace(path=f"/jobs/view/{current_job_id}", query="", fragment=""))
    match = JOB_URL_RE.search(parsed.path)
    if not match:
        return ""
    return urlunparse(parsed._replace(path=f"/jobs/view/{match.group(1)}", query="", fragment=""))


def _page_requires_login(url: str) -> bool:
    lowered = url.lower()
    return "linkedin.com/login" in lowered or "/checkpoint/" in lowered or "/signup/" in lowered


def _parse_card_text(card_text: str, *, fallback_url: str) -> dict[str, str]:
    lines = [line.strip() for line in card_text.splitlines() if line.strip()]
    lines = _strip_leading_noise(lines)
    title = ""
    company = ""
    location = ""
    posted_at = ""
    age_line = ""
    if len(lines) >= 2:
        first = _strip_verified_marker(lines[0])
        second = _strip_verified_marker(lines[1])
        if _normalize_title_token(first) and _normalize_title_token(first) == _normalize_title_token(second):
            title = second
            lines = lines[2:]
    if len(lines) >= 2 and _looks_like_role_title(lines[1]) and not _looks_like_role_title(lines[0]):
        company = lines[0]
        title = lines[1]
        lines = lines[2:]
    for line in lines:
        lowered = line.lower()
        if lowered in GENERIC_NOISE_LINES or _starts_with_noise_prefix(lowered):
            continue
        cleaned_line = _strip_verified_marker(line)
        if not title and _looks_like_role_title(cleaned_line) and not _looks_like_age_line(cleaned_line):
            title = cleaned_line
            continue
        normalized = _normalize_title_token(cleaned_line)
        if title and normalized == _normalize_title_token(title):
            continue
        if not title and not _looks_like_age_line(cleaned_line) and not _looks_like_location(cleaned_line):
            title = cleaned_line
            continue
        if not company and not _looks_like_age_line(cleaned_line) and not _looks_like_location(cleaned_line):
            company = cleaned_line
            continue
        if not location and _looks_like_location(cleaned_line):
            location = cleaned_line
        if not age_line and _looks_like_age_line(line):
            age_line = line

    if age_line:
        posted_at = _relative_age_to_iso(age_line) or ""
    return {
        "title": title,
        "company": company,
        "location": location,
        "posted_at": posted_at,
        "url": _canonical_linkedin_job_url(fallback_url),
        "card_text": card_text.strip(),
        "age_line": age_line,
        "is_reposted": _is_reposted_age_line(age_line),
    }


def _parse_detail_text(detail_text: str) -> dict[str, str]:
    normalized_text = _normalize_detail_text(detail_text)
    lines = [line.strip() for line in normalized_text.splitlines() if line.strip()]
    lines = _strip_leading_noise(lines)
    if not lines:
        return {}

    title = ""
    company = ""
    location = ""
    posted_at = ""
    posted_line = ""

    header_lines = lines[:25]
    header_preview = [_clean_location(line) for line in header_lines[:4]]
    if len(header_preview) >= 3 and _looks_like_role_title(header_preview[1]) and not _looks_like_role_title(header_preview[0]):
        company = header_preview[0]
        title = header_preview[1]
        location = header_preview[2] if _looks_like_location(header_preview[2]) else location
        header_lines = header_lines[3:]
    if len(header_lines) >= 2 and _looks_like_role_title(header_lines[1]) and not _looks_like_role_title(header_lines[0]):
        company = header_lines[0]
        title = header_lines[1]
        header_lines = header_lines[2:]

    for line in header_lines:
        lowered = line.lower()
        if lowered in {"about the job", "job description", "acerca del empleo"}:
            break
        if not title and lowered not in GENERIC_NOISE_LINES and "about the job" not in lowered and "acerca del empleo" not in lowered and not _looks_like_age_line(line) and not _looks_like_location(line):
            title = line
            continue
        if title and not company and lowered not in GENERIC_NOISE_LINES and line != title and not _looks_like_age_line(line) and not _looks_like_location(line):
            company = line
            continue
        cleaned_line = _clean_location(line)
        if not location and _looks_like_location(cleaned_line):
            location = cleaned_line
        if not posted_line and _looks_like_age_line(line):
            posted_line = line

    if posted_line:
        posted_at = _relative_age_to_iso(posted_line) or ""

    description = _extract_description(lines)
    accepting_applications = not _is_linkedin_closed(lines)
    return {
        "title": title,
        "company": company,
        "location": location,
        "posted_at": posted_at,
        "description": description,
        "accepting_applications": accepting_applications,
        "posted_line": posted_line,
        "is_reposted": _is_reposted_age_line(posted_line),
    }


def _extract_description(lines: list[str]) -> str:
    start_idx = 0
    for index, line in enumerate(lines):
        lowered = line.lower()
        if lowered in {"about the job", "job description", "acerca del empleo"}:
            start_idx = index + 1
            break

    kept: list[str] = []
    for line in lines[start_idx:]:
        lowered = line.lower()
        if lowered in GENERIC_NOISE_LINES:
            continue
        if lowered in STOP_MARKERS:
            break
        kept.append(line)
    return "\n".join(kept).strip()


def _normalize_detail_text(value: str) -> str:
    text = value
    for marker in DETAIL_SPLIT_MARKERS:
        text = text.replace(marker, f"\n{marker}\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _build_row(
    *,
    card: dict[str, str],
    detail_text: str,
    search_url: str,
    detail_fetch_attempted: bool,
) -> dict | None:
    detail = _parse_detail_text(detail_text)
    if bool(detail.get("is_reposted")) or bool(card.get("is_reposted")):
        return None
    detail_title = str(detail.get("title") or "").strip()
    detail_company = str(detail.get("company") or "").strip()
    card_title = str(card.get("title") or "").strip()
    card_company = str(card.get("company") or "").strip()
    use_detail_title = _is_valid_title(detail_title)
    if use_detail_title and not _looks_like_role_title(detail_title) and _looks_like_role_title(card_title):
        use_detail_title = False
    title = detail_title if use_detail_title else card_title

    use_detail_company = _is_valid_company(detail_company)
    if use_detail_company and _normalize_title_token(detail_company) == _normalize_title_token(title) and _normalize_title_token(card_company) != _normalize_title_token(title):
        use_detail_company = False
    company = detail_company if use_detail_company else card_company
    company = _clean_company(company)

    detail_location = str(detail.get("location") or "").strip()
    location = detail_location if _is_valid_location(detail_location) else str(card.get("location") or "").strip()
    posted_at = str(detail.get("posted_at") or card.get("posted_at") or "").strip()
    description = str(detail.get("description") or "").strip() or str(card.get("card_text") or "").strip()
    job_url = _canonical_linkedin_job_url(str(card.get("url") or "").strip())

    if not title or not job_url:
        return None

    job_id = _linkedin_job_id(job_url)
    external_id = job_id or f"{company}|{title}|{location}|{posted_at or ''}"
    detail_status = "detail_complete" if detail_text.strip() and len(description) >= 200 else ("card_only" if not detail_text.strip() else "detail_partial")
    source_metadata = {
        "detail_fetch_attempted": detail_fetch_attempted,
        "detail_quality_status": detail_status,
        "resolved_job_url": job_url,
        "accepting_applications": bool(detail.get("accepting_applications", True)),
    }
    return {
        "source": "linkedin",
        "source_detail": search_url,
        "source_metadata": source_metadata,
        "external_id": external_id,
        "url": job_url,
        "title": title,
        "company": company,
        "location": location,
        "posted_at": posted_at,
        "description": description,
        "compensation_type": _classify_compensation(card_text=str(card.get("card_text") or ""), detail_text=detail_text),
        "skills": [],
    }


def _linkedin_job_id(url: str) -> str:
    match = JOB_URL_RE.search(urlparse(url).path)
    if not match:
        return ""
    return match.group(1)


def _classify_compensation(*, card_text: str, detail_text: str) -> str:
    blob = f"{card_text}\n{detail_text}".lower()
    if "unpaid" in blob:
        return "unpaid"
    if re.search(r"\$\s*\d", blob) or re.search(r"\b\d+\s*-\s*\d+\s*/\s*(hr|hour|year)\b", blob):
        return "paid"
    if re.search(r"\b(pay|paid|salary|stipend|compensation|hourly)\b", blob):
        return "paid"
    return "unknown"


def _is_card_older_than_lookback(card: dict[str, str], max_posting_age_days: int) -> bool:
    posted_at = str(card.get("posted_at") or "")
    if not posted_at:
        return False
    try:
        posted_date = datetime.fromisoformat(posted_at).date()
    except ValueError:
        return False
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_posting_age_days)).date()
    return posted_date < cutoff


def _looks_like_location(value: str) -> bool:
    value = _clean_location(value)
    if len(value) > 120:
        return False
    if LOCATION_HINT_RE.search(value):
        return True
    return "," in value and len(value.split()) <= 8


def _looks_like_age_line(value: str) -> bool:
    return RELATIVE_AGE_RE.search(value) is not None


def _is_reposted_age_line(value: str) -> bool:
    return "reposted" in value.lower()


def _relative_age_to_iso(value: str) -> str | None:
    match = RELATIVE_AGE_RE.search(value)
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2).lower()
    delta: timedelta
    if unit.startswith(("hour", "hr", "hora")):
        delta = timedelta(hours=amount)
    elif unit.startswith(("day", "d", "día", "dia")):
        delta = timedelta(days=amount)
    elif unit.startswith(("week", "wk", "w", "semana")):
        delta = timedelta(weeks=amount)
    else:
        delta = timedelta(days=30 * amount)
    return (datetime.now(timezone.utc) - delta).isoformat()


def _normalize_title_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _strip_leading_noise(lines: list[str]) -> list[str]:
    start = 0
    while start < len(lines):
        lowered = lines[start].lower()
        if lowered.isdigit():
            start += 1
            continue
        if lowered in GENERIC_NOISE_LINES or any(_matches_noise_prefix(lowered, prefix) for prefix in LEADING_NOISE_PREFIXES):
            start += 1
            continue
        break
    return lines[start:]


def _matches_noise_prefix(value: str, prefix: str) -> bool:
    if value == prefix:
        return True
    return value.startswith(f"{prefix} ")


def _starts_with_noise_prefix(value: str) -> bool:
    return any(_matches_noise_prefix(value, prefix) for prefix in LEADING_NOISE_PREFIXES)


def _looks_like_role_title(value: str) -> bool:
    lowered = value.lower()
    if any(marker in lowered for marker in ("empleo verificado", "verified job")):
        return True
    return any(
        token in lowered
        for token in (
            "intern",
            "engineer",
            "scientist",
            "analyst",
            "developer",
            "researcher",
            "co-op",
            "machine learning",
            "ai/ml",
            " ai ",
            "data ",
        )
    )


def _clean_location(value: str) -> str:
    primary = value.split("·", 1)[0].strip()
    return primary


def _clean_company(value: str) -> str:
    cleaned = value.strip()
    if re.search(r"[A-Za-z]", cleaned):
        cleaned = re.sub(r"(?<=\D)\s+\d+$", "", cleaned).strip()
    return cleaned


def _strip_verified_marker(value: str) -> str:
    return re.sub(r"\s*\((verified job|empleo verificado)\)\s*", " ", value, flags=re.IGNORECASE).strip()


def _is_linkedin_closed(lines: list[str]) -> bool:
    for line in lines:
        lowered = line.strip().lower()
        if any(pattern in lowered for pattern in LINKEDIN_CLOSED_PATTERNS):
            return True
    return False


def _is_valid_title(value: str) -> bool:
    lowered = value.strip().lower()
    if not lowered:
        return False
    if any(lowered.startswith(prefix) for prefix in INVALID_HEADER_PREFIXES):
        return False
    if "saveapply" in lowered or "guardarsolicitar" in lowered:
        return False
    if "show match details" in lowered or "tailor my resume" in lowered or "create cover letter" in lowered:
        return False
    if "•" in value:
        return False
    if lowered.isdigit():
        return False
    return True


def _is_valid_company(value: str) -> bool:
    lowered = value.strip().lower()
    if not lowered:
        return False
    if any(lowered.startswith(prefix) for prefix in INVALID_HEADER_PREFIXES):
        return False
    if _starts_with_noise_prefix(lowered):
        return False
    if _looks_like_compensation_line(value):
        return False
    if lowered.isdigit():
        return False
    return True


def _is_valid_location(value: str) -> bool:
    stripped = value.strip()
    if not stripped:
        return False
    if "•" in stripped:
        return False
    if _looks_like_role_title(stripped):
        return False
    if stripped.lower().startswith(("save", "guardar", "apply", "solicitar")):
        return False
    return _looks_like_location(stripped)


def _looks_like_compensation_line(value: str) -> bool:
    lowered = value.lower()
    if re.search(r"[$€£]\s*\d", value):
        return True
    if re.search(r"\b\d[\d,.]*\s*(usd|eur|gbp)\b", lowered):
        return True
    return "/yr" in lowered or "/hr" in lowered or "/hour" in lowered
