from __future__ import annotations

import html
import logging
import re
import urllib.request
import xml.etree.ElementTree as ET

from job_hunter.sources.base import SourceConnector, USER_AGENT

LOG = logging.getLogger(__name__)


class RssSource(SourceConnector):
    def __init__(self, feeds: list[str]) -> None:
        super().__init__(name="rss")
        self.feeds = feeds
        self._fetch_meta: dict[str, int] = {}

    def fetch(self, timeout_seconds: int) -> list[dict]:
        feed_error_count = 0
        max_error_logs = 10
        logged_errors = 0
        item_results: list[dict[str, str]] = []
        results: list[dict] = []
        for feed_url in self.feeds:
            try:
                req = urllib.request.Request(feed_url, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
                    raw = resp.read()
            except Exception as exc:
                feed_error_count += 1
                if logged_errors < max_error_logs:
                    LOG.warning("rss_feed_fetch_failed feed=%s error=%s", feed_url, exc)
                    logged_errors += 1
                item_results.append({"item": feed_url, "status": "failure", "error": str(exc)})
                continue

            try:
                root = ET.fromstring(raw)
            except ET.ParseError as exc:
                feed_error_count += 1
                if logged_errors < max_error_logs:
                    LOG.warning("rss_feed_parse_failed feed=%s error=%s", feed_url, exc)
                    logged_errors += 1
                item_results.append({"item": feed_url, "status": "failure", "error": str(exc)})
                continue

            item_results.append({"item": feed_url, "status": "success", "error": ""})
            items = _find_items(root)
            for item in items:
                title = _text(item, "title")
                link = _text(item, "link")
                description = _clean_text(_text(item, "description") or _text(item, "summary"))
                company = _text(item, "author") or _text(item, "creator") or ""
                posted_at = _text(item, "pubDate") or _text(item, "published") or _text(item, "updated")
                if not title or not link:
                    continue

                results.append(
                    {
                        "source": self.name,
                        "source_detail": feed_url,
                        "external_id": link,
                        "url": link,
                        "title": title,
                        "company": company,
                        "location": "",
                        "posted_at": posted_at,
                        "description": description,
                        "skills": [],
                    }
                )
        self._fetch_meta = {"feed_error_count": feed_error_count, "item_results": item_results}
        suppressed = feed_error_count - logged_errors
        if suppressed > 0:
            LOG.warning("rss_feed_failures_suppressed count=%s", suppressed)
        return results

    def get_fetch_meta(self) -> dict[str, int]:
        return dict(self._fetch_meta)


def _find_items(root: ET.Element) -> list[ET.Element]:
    channel = root.find("channel")
    if channel is not None:
        return [node for node in channel.findall("item") if isinstance(node.tag, str)]

    ns = "{http://www.w3.org/2005/Atom}"
    return [node for node in root.findall(f"{ns}entry") if isinstance(node.tag, str)]


def _text(node: ET.Element, tag: str) -> str:
    direct = node.find(tag)
    if direct is not None and direct.text:
        return direct.text.strip()

    atom_ns = "{http://www.w3.org/2005/Atom}"
    atom = node.find(f"{atom_ns}{tag}")
    if atom is not None:
        if tag == "link":
            href = atom.attrib.get("href", "")
            return href.strip()
        if atom.text:
            return atom.text.strip()
    return ""


def _clean_text(value: str) -> str:
    text = html.unescape(value)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text
