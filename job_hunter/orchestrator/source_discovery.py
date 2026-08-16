from __future__ import annotations

import ipaddress
import re
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlparse

from job_hunter.config import Settings
from job_hunter.orchestrator.store import OrchestratorStore


@dataclass(frozen=True)
class DiscoveredSource:
    source_type: str
    source_value: str
    result_url: str
    query: str


class TavilySourceDiscovery:
    def __init__(self, *, settings: Settings, store: OrchestratorStore, client=None) -> None:
        self.settings = settings
        self.store = store
        if client is not None:
            self.client = client
            return
        if not settings.tavily_api_key:
            self.client = None
            return
        try:
            from tavily import TavilyClient
        except ModuleNotFoundError as exc:
            raise RuntimeError("Tavily SDK is not installed. Run `pip install -e .`.") from exc
        self.client = TavilyClient(api_key=settings.tavily_api_key)

    def discover(self, queries: list[str]) -> list[DiscoveredSource]:
        if self.client is None or not queries:
            return []
        discovered: dict[tuple[str, str], DiscoveredSource] = {}
        for query in queries:
            if self.store.discovery_credits_today(self.settings.orchestrator_timezone) >= self.settings.source_discovery_daily_credit_limit:
                break
            try:
                response = self.client.search(
                    query=query,
                    search_depth="basic",
                    topic="general",
                    max_results=self.settings.source_discovery_max_results,
                    country="united states",
                    include_answer=False,
                    include_raw_content=False,
                    include_images=False,
                    include_usage=True,
                    timeout=float(self.settings.request_timeout_seconds),
                )
                results = response.get("results", []) if isinstance(response, dict) else []
                credits = _extract_credits(response)
                self.store.record_discovery_usage(
                    query=query,
                    credits_used=credits,
                    result_count=len(results) if isinstance(results, list) else 0,
                    status="success",
                )
            except Exception as exc:
                self.store.record_discovery_usage(
                    query=query,
                    credits_used=0,
                    result_count=0,
                    status=type(exc).__name__,
                )
                continue
            for item in results if isinstance(results, list) else []:
                if not isinstance(item, dict):
                    continue
                result_url = str(item.get("url") or "").strip()
                parsed = classify_source_url(result_url, query=query)
                if parsed is not None:
                    discovered[(parsed.source_type, parsed.source_value)] = parsed
        return list(discovered.values())


def classify_source_url(url: str, *, query: str = "") -> DiscoveredSource | None:
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        return None
    host = parsed.hostname.lower().strip(".")
    segments = [segment for segment in parsed.path.split("/") if segment]
    if host in {"boards.greenhouse.io", "job-boards.greenhouse.io"} and segments:
        return DiscoveredSource("greenhouse", segments[0], url, query)
    if host == "jobs.lever.co" and segments:
        return DiscoveredSource("lever", segments[0], url, query)
    if host == "jobs.ashbyhq.com" and segments:
        return DiscoveredSource("ashby", segments[0], url, query)
    if host == "github.com" and len(segments) >= 2:
        repo = f"https://raw.githubusercontent.com/{segments[0]}/{segments[1]}/HEAD/README.md"
        return DiscoveredSource("github_repo", repo, url, query)
    lowered_path = parsed.path.lower()
    if lowered_path.endswith((".rss", ".xml")) or "/feed" in lowered_path or "rss" in lowered_path:
        return DiscoveredSource("rss", url, url, query)
    return None


def validate_public_https_url(url: str, *, resolver=socket.getaddrinfo) -> tuple[bool, str]:
    try:
        parsed = urlparse(url)
    except ValueError as exc:
        return False, f"invalid_url:{exc}"
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        return False, "https_required"
    try:
        addresses = resolver(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        return False, f"dns_error:{exc}"
    for address in addresses:
        raw = address[4][0]
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError:
            return False, "invalid_dns_address"
        if not ip.is_global:
            return False, "non_public_address"
    return True, "ok"


def probe_source(source_type: str, source_value: str, *, timeout_seconds: int = 20) -> tuple[bool, str]:
    url = _probe_url(source_type, source_value)
    if not url:
        return False, "unsupported_source_type"
    valid, reason = validate_public_https_url(url)
    if not valid:
        return False, reason
    req = urllib.request.Request(url, headers={"User-Agent": "job-hunter-source-probe/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=max(timeout_seconds, 1)) as response:
            if 200 <= int(getattr(response, "status", 200)) < 300:
                body = response.read(4096)
                return (bool(body), "ok" if body else "empty_response")
            return False, f"http_{getattr(response, 'status', 'unknown')}"
    except urllib.error.HTTPError as exc:
        return False, f"http_{exc.code}"
    except urllib.error.URLError as exc:
        return False, f"network_error:{exc.reason}"


def seed_source_registry(settings: Settings, store: OrchestratorStore) -> int:
    seeded = 0
    groups = {
        "greenhouse": settings.greenhouse_boards,
        "lever": settings.lever_companies,
        "rss": settings.rss_feeds,
        "github_repo": settings.github_repo_readmes,
        "ashby": settings.ashby_boards,
    }
    for source_type, values in groups.items():
        for value in values:
            before = len(store.list_sources())
            store.upsert_source(
                source_type=source_type,
                source_value=value,
                provenance="configured",
                status="active",
                rationale="imported from existing settings",
            )
            if len(store.list_sources()) > before:
                seeded += 1
    return seeded


def _probe_url(source_type: str, value: str) -> str:
    value = value.strip()
    if source_type == "greenhouse" and re.fullmatch(r"[A-Za-z0-9_-]+", value):
        return f"https://boards-api.greenhouse.io/v1/boards/{value}/jobs"
    if source_type == "lever" and re.fullmatch(r"[A-Za-z0-9_-]+", value):
        return f"https://api.lever.co/v0/postings/{value}?mode=json"
    if source_type == "ashby" and re.fullmatch(r"[A-Za-z0-9_-]+", value):
        return f"https://api.ashbyhq.com/posting-api/job-board/{value}"
    if source_type in {"rss", "github_repo"}:
        return value
    return ""


def _extract_credits(response: object) -> int:
    if not isinstance(response, dict):
        return 1
    usage = response.get("usage")
    if isinstance(usage, dict):
        for key in ("credits", "total_credits", "search_credits"):
            value = usage.get(key)
            if isinstance(value, (int, float)):
                return max(int(value), 1)
    return 1
