from __future__ import annotations

import logging

from job_hunter.sources.base import SourceConnector, get_json

LOG = logging.getLogger(__name__)


class GreenhouseSource(SourceConnector):
    def __init__(self, board_tokens: list[str]) -> None:
        super().__init__(name="greenhouse")
        self.board_tokens = board_tokens
        self._fetch_meta: dict[str, int] = {}

    def fetch(self, timeout_seconds: int) -> list[dict]:
        dead_token_count = 0
        max_error_logs = 10
        logged_errors = 0
        item_results: list[dict[str, str]] = []
        results: list[dict] = []
        for board in self.board_tokens:
            try:
                data = get_json(
                    f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs",
                    timeout_seconds,
                    params={"content": "true"},
                )
            except Exception as exc:
                dead_token_count += 1
                if logged_errors < max_error_logs:
                    LOG.warning("greenhouse_board_fetch_failed board=%s error=%s", board, exc)
                    logged_errors += 1
                item_results.append({"item": board, "status": "failure", "error": str(exc)})
                continue

            item_results.append({"item": board, "status": "success", "error": ""})
            jobs = data.get("jobs", []) if isinstance(data, dict) else []
            for item in jobs:
                if not isinstance(item, dict):
                    continue
                location = item.get("location", {})
                results.append(
                    {
                        "source": self.name,
                        "source_detail": board,
                        "external_id": str(item.get("id") or item.get("absolute_url") or ""),
                        "url": item.get("absolute_url", ""),
                        "title": item.get("title", ""),
                        "company": board,
                        "location": location.get("name", "") if isinstance(location, dict) else "",
                        "posted_at": item.get("updated_at"),
                        "description": item.get("content", ""),
                        "skills": [],
                    }
                )
        self._fetch_meta = {"dead_token_count": dead_token_count, "item_results": item_results}
        suppressed = dead_token_count - logged_errors
        if suppressed > 0:
            LOG.warning("greenhouse_board_fetch_failures_suppressed count=%s", suppressed)
        return results

    def get_fetch_meta(self) -> dict[str, int]:
        return dict(self._fetch_meta)
