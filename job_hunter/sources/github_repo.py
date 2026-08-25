from __future__ import annotations

import hashlib
import html
import logging
import re
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlparse

from typing import Any

from job_hunter.browser_api import load_browser_api, normalize_browser_backend
from job_hunter.sources.base import SourceConnector, USER_AGENT, clamp_bulk_source_timeout

LOG = logging.getLogger(__name__)
TABLE_HEADER = "| Company | Role | Location | Application/Link | Date Posted |"
BACK_TO_TOP_MARKER = "[⬆️ Back to Top"
MARKDOWN_LINK_RE = re.compile(r"\[[^\]]*\]\((https?://[^)]+)\)")
HTTP_URL_RE = re.compile(r"https?://[^\s)]+")
MONTH_DAY_RE = re.compile(r"^(?P<month>[A-Za-z]{3,9})\s+(?P<day>\d{1,2})$")
DETAIL_MIN_TEXT_LENGTH = 400

ATS_DESCRIPTION_SCRIPT = """
() => {
  const selectors = [
    "[data-automation-id='jobPostingDescription']",
    "#content",
    "#job-description",
    ".job-description",
    "[class*='description']",
    "[class*='job-detail']",
    "[class*='posting-description']",
    ".sfdc_richtext",
    "article",
    "main",
  ];
  for (const sel of selectors) {
    const node = document.querySelector(sel);
    if (node && (node.innerText || '').trim().length > 100) {
      return (node.innerText || '').trim();
    }
  }
  return (document.body ? (document.body.innerText || '') : '').trim();
}
"""


class _VisibleTextParser(HTMLParser):
    """Extract human-readable text while ignoring executable and styling content."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignored_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        _ = attrs
        if tag.lower() in {"script", "style", "noscript", "svg"}:
            self._ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"} and self._ignored_depth:
            self._ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth:
            self.parts.append(data)


class _BrowserDetailFetcher:
    def __init__(self, browser_backend: str, headless: bool, page_timeout_seconds: int) -> None:
        self.browser_backend = normalize_browser_backend(browser_backend)
        self.headless = headless
        self.page_timeout_seconds = max(page_timeout_seconds, 5)
        self._playwright_ctx: Any | None = None
        self._browser: Any | None = None
        self._context: Any | None = None

    def _ensure_browser(self) -> None:
        if self._context is not None:
            return
        browser_api = load_browser_api(self.browser_backend)
        self._playwright_ctx = browser_api.sync_playwright()
        playwright = self._playwright_ctx.start()
        try:
            self._browser = playwright.chromium.launch(headless=self.headless, channel="chrome")
        except Exception:
            self._browser = playwright.chromium.launch(headless=self.headless)
        self._context = self._browser.new_context(user_agent=USER_AGENT)
        self._context.set_default_timeout(self.page_timeout_seconds * 1000)
        self._context.set_default_navigation_timeout(self.page_timeout_seconds * 1000)

    def fetch_text(self, url: str) -> str:
        try:
            self._ensure_browser()
            if self._context is None:
                return ""
            page = self._context.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded")
                for wait_ms in (1500, 2000, 2500, 3000):
                    page.wait_for_timeout(wait_ms)
                    text = str(page.evaluate(ATS_DESCRIPTION_SCRIPT) or "").strip()
                    if not text or len(text) < DETAIL_MIN_TEXT_LENGTH:
                        text = str(page.locator("body").inner_text() or "").strip()
                    text = re.sub(r"\s+", " ", text).strip()
                    if len(text) >= DETAIL_MIN_TEXT_LENGTH:
                        return text
                return ""
            finally:
                page.close()
        except Exception as exc:
            LOG.info("github_repo_browser_detail_fetch_failed url=%s error=%s", url, exc)
            return ""

    def close(self) -> None:
        if self._context is not None:
            try:
                self._context.close()
            except Exception:
                pass
            self._context = None
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright_ctx is not None:
            try:
                self._playwright_ctx.stop()
            except Exception:
                pass
            self._playwright_ctx = None


class GithubRepoSource(SourceConnector):
    def __init__(
        self,
        readme_urls: list[str],
        max_posting_age_days: int = 7,
        browser_backend: str = "playwright",
        headless: bool = True,
        enable_browser_fallback: bool = True,
        page_timeout_seconds: int = 15,
    ) -> None:
        super().__init__(name="github_repo")
        self.readme_urls = readme_urls
        self.max_posting_age_days = max_posting_age_days
        self.browser_backend = browser_backend
        self.headless = headless
        self.enable_browser_fallback = enable_browser_fallback
        self.page_timeout_seconds = page_timeout_seconds
        self._fetch_meta: dict[str, object] = {}

    def fetch(self, timeout_seconds: int) -> list[dict]:
        results: list[dict] = []
        detail_timeout_seconds = clamp_bulk_source_timeout(timeout_seconds)
        browser_fetcher: _BrowserDetailFetcher | None = None
        if self.enable_browser_fallback:
            browser_fetcher = _BrowserDetailFetcher(
                browser_backend=self.browser_backend,
                headless=self.headless,
                page_timeout_seconds=self.page_timeout_seconds,
            )
        try:
            for readme_url in self.readme_urls:
                try:
                    req = urllib.request.Request(readme_url, headers={"User-Agent": USER_AGENT})
                    with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
                        markdown = resp.read().decode("utf-8", errors="replace")
                except Exception as exc:
                    LOG.warning("github_repo_fetch_failed readme=%s error=%s", readme_url, exc)
                    continue

                for index, row in enumerate(_parse_markdown_table(markdown), start=1):
                    company = row["company"]
                    role = row["role"]
                    location = row["location"]
                    date_text = row["date_posted"]
                    url = row["application_url"] or f"{readme_url}#row-{index}"
                    if row["application_url"] and _is_generic_listing_url(url):
                        LOG.info("github_repo_row_skipped_generic_listing_url url=%s role=%s", url, role)
                        continue
                    external_id = row["application_url"] or _fallback_external_id(
                        readme_url=readme_url,
                        company=company,
                        role=role,
                        location=location,
                        date_text=date_text,
                    )
                    posted_at = _normalize_posted_at(date_text)
                    if posted_at and not _is_within_lookback(posted_at, self.max_posting_age_days):
                        continue
                    listing_description = (
                        f"Imported from GitHub internship repository. "
                        f"Repository-listed date: {date_text}."
                    )
                    detail_text = ""
                    provenance = "github_repo_listing"
                    if row["application_url"]:
                        detail_text = _fetch_detail_text(url, detail_timeout_seconds)
                        if detail_text:
                            provenance = "github_repo_detail"
                        elif browser_fetcher is not None:
                            detail_text = browser_fetcher.fetch_text(url)
                            if detail_text:
                                provenance = "github_repo_browser_detail"

                    detail_quality_status = "detail_complete" if detail_text else "summary_only"
                    results.append(
                        {
                            "source": self.name,
                            "source_detail": readme_url,
                            "external_id": external_id,
                            "url": url,
                            "title": role,
                            "company": company,
                            "location": location,
                            "posted_at": posted_at,
                            "description": detail_text or listing_description,
                            "skills": [],
                            "source_metadata": {
                                "external_apply_url": row["application_url"],
                                "detail_fetch_attempted": bool(row["application_url"]),
                                "detail_quality_status": detail_quality_status,
                                "description_provenance": provenance,
                                "listing_description": listing_description,
                            },
                        }
                    )
        finally:
            if browser_fetcher is not None:
                browser_fetcher.close()
        return results

    def get_fetch_meta(self) -> dict[str, object]:
        return dict(self._fetch_meta)


def _parse_markdown_table(markdown: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    in_table = False
    pending: str | None = None
    last_company = ""

    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        if not in_table:
            if TABLE_HEADER in line:
                in_table = True
            continue

        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(BACK_TO_TOP_MARKER) or stripped.startswith("## "):
            break
        if stripped.startswith("|"):
            if pending is not None:
                parsed, last_company = _parse_row(pending, last_company)
                if parsed is not None:
                    rows.append(parsed)
            pending = stripped
            continue
        if pending is not None:
            pending = f"{pending} {stripped}"

    if pending is not None:
        parsed, _ = _parse_row(pending, last_company)
        if parsed is not None:
            rows.append(parsed)
    return rows


def _parse_row(row: str, last_company: str) -> tuple[dict[str, str] | None, str]:
    if set(row) <= {"|", "-", " "}:
        return None, last_company
    if "Company" in row and "Date Posted" in row:
        return None, last_company

    cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
    if len(cells) != 5:
        return None, last_company

    company, role, location, application_cell, date_posted = cells
    if company == "↳":
        company = last_company
    elif company:
        last_company = company

    if not company or not role or not date_posted:
        return None, last_company

    return (
        {
            "company": _clean_cell(company),
            "role": _clean_cell(role),
            "location": _clean_cell(location),
            "application_url": _extract_url(application_cell),
            "date_posted": _clean_cell(date_posted),
        },
        last_company,
    )


def _clean_cell(value: str) -> str:
    text = value.replace("**", "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _extract_url(value: str) -> str:
    markdown_match = MARKDOWN_LINK_RE.search(value)
    if markdown_match:
        return _clean_extracted_url(markdown_match.group(1))
    plain_match = HTTP_URL_RE.search(value)
    if plain_match:
        return _clean_extracted_url(plain_match.group(0))
    return ""


def _clean_extracted_url(value: str) -> str:
    # Some repository rows append HTML badges immediately after a Markdown link.
    url = html.unescape(value).strip()
    url = re.split(r"[\s\"'<]", url, maxsplit=1)[0]
    return url.rstrip(".,;")


def _is_generic_listing_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.netloc.lower() != "apply.careers.microsoft.com":
        return False
    if parsed.path.rstrip("/").lower() != "/careers":
        return False
    query = parse_qs(parsed.query)
    return bool(query.get("query") or query.get("start"))


def _fetch_detail_text(url: str, timeout_seconds: int) -> str:
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            content_type = str(resp.headers.get("Content-Type", "")).lower()
            if "html" not in content_type:
                return ""
            document = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        LOG.info("github_repo_detail_fetch_failed url=%s error=%s", url, exc)
        return ""

    parser = _VisibleTextParser()
    try:
        parser.feed(document)
        parser.close()
    except Exception as exc:
        LOG.info("github_repo_detail_parse_failed url=%s error=%s", url, exc)
        return ""

    text = re.sub(r"\s+", " ", " ".join(parser.parts)).strip()
    return text if len(text) >= DETAIL_MIN_TEXT_LENGTH else ""


def _normalize_posted_at(value: str) -> str | None:
    match = MONTH_DAY_RE.match(value.strip())
    if not match:
        return None
    month = match.group("month")
    day = int(match.group("day"))
    now = datetime.now(timezone.utc)
    year = now.year
    try:
        parsed = datetime.strptime(f"{month} {day} {year}", "%b %d %Y")
    except ValueError:
        try:
            parsed = datetime.strptime(f"{month} {day} {year}", "%B %d %Y")
        except ValueError:
            return None
    if parsed.month > now.month + 1:
        parsed = parsed.replace(year=year - 1)
    return parsed.replace(tzinfo=timezone.utc).isoformat()


def _is_within_lookback(posted_at: str, max_posting_age_days: int) -> bool:
    if max_posting_age_days <= 0:
        return True
    try:
        posted_dt = datetime.fromisoformat(posted_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    age_seconds = (datetime.now(timezone.utc) - posted_dt).total_seconds()
    return age_seconds <= max_posting_age_days * 86400


def _fallback_external_id(readme_url: str, company: str, role: str, location: str, date_text: str) -> str:
    payload = "||".join([readme_url, company, role, location, date_text])
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()
    return f"github-repo:{digest}"
