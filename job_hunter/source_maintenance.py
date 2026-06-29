from __future__ import annotations

import logging
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from job_hunter.config import Settings
from job_hunter.sources.base import USER_AGENT, clamp_bulk_source_timeout, get_json
from job_hunter.storage import JobStore

LOG = logging.getLogger(__name__)


class SourceFileSet:
    def __init__(self, source_name: str, active_file: str | None, quarantine_file: str | None) -> None:
        self.source_name = source_name
        self.active_file = active_file
        self.quarantine_file = quarantine_file


SOURCE_FILESETS = (
    lambda settings: SourceFileSet("greenhouse", settings.greenhouse_token_file, settings.greenhouse_quarantine_file),
    lambda settings: SourceFileSet("lever", settings.lever_token_file, settings.lever_quarantine_file),
    lambda settings: SourceFileSet("rss", settings.rss_feed_file, settings.rss_quarantine_file),
)


def run_source_maintenance(
    settings: Settings,
    store: JobStore,
    *,
    probe_active: bool = False,
    probe_quarantine: bool = True,
    probe_limit_per_source: int = 20,
    timeout_seconds: int | None = None,
) -> dict[str, int]:
    timeout = clamp_bulk_source_timeout(timeout_seconds or settings.request_timeout_seconds)
    summary = {
        "quarantined_count": 0,
        "restored_count": 0,
        "active_probe_success_count": 0,
        "active_probe_failure_count": 0,
        "quarantine_probe_success_count": 0,
        "quarantine_probe_failure_count": 0,
    }

    for fileset_factory in SOURCE_FILESETS:
        fileset = fileset_factory(settings)
        active_path = _as_path(fileset.active_file)
        quarantine_path = _as_path(fileset.quarantine_file)
        if active_path is None or quarantine_path is None:
            continue

        active_items = _read_items(active_path)
        quarantine_items = _read_items(quarantine_path)

        if probe_active and active_items:
            active_results = _probe_items(fileset.source_name, active_items, timeout_seconds=timeout)
            store.record_source_item_results(fileset.source_name, active_results)
            summary["active_probe_success_count"] += sum(1 for row in active_results if row["status"] == "success")
            summary["active_probe_failure_count"] += sum(1 for row in active_results if row["status"] == "failure")

        health_rows = store.get_source_item_health(fileset.source_name)
        health_by_item = {str(row["item_value"]).strip().lower(): row for row in health_rows}

        demote: list[str] = []
        for item in active_items:
            row = health_by_item.get(item.strip().lower())
            if row is None:
                continue
            if str(row["status"]) != "failure":
                continue
            if int(row["consecutive_failures"]) < max(settings.source_failure_quarantine_threshold, 1):
                continue
            demote.append(item)

        for item in demote:
            if item in active_items:
                active_items.remove(item)
            if item not in quarantine_items:
                quarantine_items.append(item)
        summary["quarantined_count"] += len(demote)

        if probe_quarantine and quarantine_items:
            probe_items = quarantine_items[: max(probe_limit_per_source, 0)]
            quarantine_results = _probe_items(fileset.source_name, probe_items, timeout_seconds=timeout)
            store.record_source_item_results(fileset.source_name, quarantine_results)
            summary["quarantine_probe_success_count"] += sum(1 for row in quarantine_results if row["status"] == "success")
            summary["quarantine_probe_failure_count"] += sum(1 for row in quarantine_results if row["status"] == "failure")
            health_rows = store.get_source_item_health(fileset.source_name)
            health_by_item = {str(row["item_value"]).strip().lower(): row for row in health_rows}

        restore: list[str] = []
        for item in quarantine_items:
            row = health_by_item.get(item.strip().lower())
            if row is None:
                continue
            if str(row["status"]) != "success":
                continue
            if int(row["consecutive_successes"]) < max(settings.source_restore_success_threshold, 1):
                continue
            restore.append(item)

        for item in restore:
            if item in quarantine_items:
                quarantine_items.remove(item)
            if item not in active_items:
                active_items.append(item)
        summary["restored_count"] += len(restore)

        _write_items(active_path, active_items)
        _write_items(quarantine_path, quarantine_items)

    LOG.info("source_maintenance_completed %s", summary)
    return summary


def _probe_items(source_name: str, items: list[str], timeout_seconds: int) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    for item in items:
        ok, error = _probe_item(source_name, item, timeout_seconds)
        results.append(
            {
                "item": item,
                "status": "success" if ok else "failure",
                "error": "" if ok else error,
            }
        )
    return results


def _probe_item(source_name: str, item: str, timeout_seconds: int) -> tuple[bool, str]:
    try:
        if source_name == "greenhouse":
            get_json(
                f"https://boards-api.greenhouse.io/v1/boards/{item}/jobs",
                timeout_seconds,
                params={"content": "false"},
            )
            return True, ""
        if source_name == "lever":
            get_json(
                f"https://api.lever.co/v0/postings/{item}",
                timeout_seconds,
                params={"mode": "json", "limit": 1},
            )
            return True, ""
        if source_name == "rss":
            req = urllib.request.Request(item, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
                raw = resp.read()
            ET.fromstring(raw)
            return True, ""
        return False, "unsupported_source"
    except Exception as exc:
        return False, str(exc)


def _as_path(path_value: str | None) -> Path | None:
    if not path_value:
        return None
    return Path(path_value).expanduser()


def _read_items(path: Path) -> list[str]:
    if not path.exists() or not path.is_file():
        return []
    items: list[str] = []
    seen: set[str] = set()
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            value = line.strip()
            if not value or value.startswith("#"):
                continue
            key = value.lower()
            if key in seen:
                continue
            seen.add(key)
            items.append(value)
    return items


def _write_items(path: Path, items: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "\n".join(item for item in items if item.strip())
    if payload:
        payload = payload + "\n"
    path.write_text(payload, encoding="utf-8")
