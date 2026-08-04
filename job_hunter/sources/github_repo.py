from __future__ import annotations

import hashlib
import html
import logging
import re
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlparse

from job_hunter.sources.base import SourceConnector, USER_AGENT, clamp_bulk_source_timeout

LOG = logging.getLogger(__name__)
TABLE_HEADER = "| Company | Role | Location | Application/Link | Date Posted |"
BACK_TO_TOP_MARKER = "[⬆️ Back to Top"
MARKDOWN_LINK_RE = re.compile(r"\[[^\]]*\]\((https?://[^)]+)\)")
HTTP_URL_RE = re.compile(r"https?://[^\s)]+")
MONTH_DAY_RE = re.compile(r"^(?P<month>[A-Za-z]{3,9})\s+(?P<day>\d{1,2})$")
DETAIL_MIN_TEXT_LENGTH = 400


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


class GithubRepoSource(SourceConnector):
    def __init__(self, readme_urls: list[str], max_posting_age_days: int = 7) -> None:
        super().__init__(name="github_repo")
        self.readme_urls = readme_urls
        self.max_posting_age_days = max_posting_age_days

    def fetch(self, timeout_seconds: int) -> list[dict]:
        results: list[dict] = []
        detail_timeout_seconds = clamp_bulk_source_timeout(timeout_seconds)
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
                detail_text = _fetch_detail_text(url, detail_timeout_seconds) if row["application_url"] else ""
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
                            "description_provenance": "github_repo_detail" if detail_text else "github_repo_listing",
                            "listing_description": listing_description,
                        },
                    }
                )
        return results


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
