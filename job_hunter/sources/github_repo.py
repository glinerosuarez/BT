from __future__ import annotations

import hashlib
import logging
import re
import urllib.request
from datetime import datetime, timezone

from job_hunter.sources.base import SourceConnector, USER_AGENT

LOG = logging.getLogger(__name__)
TABLE_HEADER = "| Company | Role | Location | Application/Link | Date Posted |"
BACK_TO_TOP_MARKER = "[⬆️ Back to Top"
MARKDOWN_LINK_RE = re.compile(r"\[[^\]]*\]\((https?://[^)]+)\)")
HTTP_URL_RE = re.compile(r"https?://[^\s)]+")
MONTH_DAY_RE = re.compile(r"^(?P<month>[A-Za-z]{3,9})\s+(?P<day>\d{1,2})$")


class GithubRepoSource(SourceConnector):
    def __init__(self, readme_urls: list[str]) -> None:
        super().__init__(name="github_repo")
        self.readme_urls = readme_urls

    def fetch(self, timeout_seconds: int) -> list[dict]:
        results: list[dict] = []
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
                external_id = row["application_url"] or _fallback_external_id(
                    readme_url=readme_url,
                    company=company,
                    role=role,
                    location=location,
                    date_text=date_text,
                )
                posted_at = _normalize_posted_at(date_text)
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
                        "description": (
                            f"Imported from GitHub internship repository. "
                            f"Repository-listed date: {date_text}."
                        ),
                        "skills": [],
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
        return markdown_match.group(1).strip()
    plain_match = HTTP_URL_RE.search(value)
    if plain_match:
        return plain_match.group(0).strip()
    return ""


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


def _fallback_external_id(readme_url: str, company: str, role: str, location: str, date_text: str) -> str:
    payload = "||".join([readme_url, company, role, location, date_text])
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()
    return f"github-repo:{digest}"
