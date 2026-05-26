from __future__ import annotations

from job_hunter.sources.base import SourceConnector, get_json


class RemotiveSource(SourceConnector):
    def __init__(self) -> None:
        super().__init__(name="remotive")

    def fetch(self, timeout_seconds: int) -> list[dict]:
        data = get_json("https://remotive.com/api/remote-jobs", timeout_seconds)
        jobs = data.get("jobs", []) if isinstance(data, dict) else []
        results: list[dict] = []
        for item in jobs:
            if not isinstance(item, dict):
                continue
            tags = item.get("tags", [])
            description = item.get("description", "")
            results.append(
                {
                    "source": self.name,
                    "external_id": str(item.get("id") or item.get("url") or ""),
                    "url": item.get("url", ""),
                    "title": item.get("title", ""),
                    "company": item.get("company_name", ""),
                    "location": item.get("candidate_required_location", ""),
                    "posted_at": item.get("publication_date"),
                    "description": description,
                    "skills": tags if isinstance(tags, list) else [],
                }
            )
        return results
