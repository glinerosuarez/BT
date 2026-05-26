from __future__ import annotations

import logging

from job_hunter.sources.base import SourceConnector, get_json

LOG = logging.getLogger(__name__)


class GreenhouseSource(SourceConnector):
    def __init__(self, board_tokens: list[str]) -> None:
        super().__init__(name="greenhouse")
        self.board_tokens = board_tokens

    def fetch(self, timeout_seconds: int) -> list[dict]:
        results: list[dict] = []
        for board in self.board_tokens:
            try:
                data = get_json(
                    f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs",
                    timeout_seconds,
                    params={"content": "true"},
                )
            except Exception as exc:
                LOG.warning("greenhouse_board_fetch_failed board=%s error=%s", board, exc)
                continue

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
        return results
