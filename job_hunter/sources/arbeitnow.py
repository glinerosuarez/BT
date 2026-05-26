from __future__ import annotations

from job_hunter.sources.base import SourceConnector, get_json


class ArbeitnowSource(SourceConnector):
    def __init__(self) -> None:
        super().__init__(name="arbeitnow")

    def fetch(self, timeout_seconds: int) -> list[dict]:
        data = get_json("https://www.arbeitnow.com/api/job-board-api", timeout_seconds)
        items = data.get("data", []) if isinstance(data, dict) else []
        results: list[dict] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            tags = item.get("tags", [])
            description = item.get("description", "")
            results.append(
                {
                    "source": self.name,
                    "external_id": str(item.get("slug") or item.get("url") or ""),
                    "url": item.get("url", ""),
                    "title": item.get("title", ""),
                    "company": item.get("company_name", ""),
                    "location": item.get("location", ""),
                    "posted_at": item.get("created_at") or item.get("date"),
                    "description": description,
                    "skills": tags if isinstance(tags, list) else [],
                }
            )
        return results
