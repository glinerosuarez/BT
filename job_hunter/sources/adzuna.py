from __future__ import annotations

from job_hunter.sources.base import SourceConnector, get_json


class AdzunaSource(SourceConnector):
    def __init__(self, country: str, app_id: str, app_key: str, pages: int = 2) -> None:
        super().__init__(name="adzuna")
        self.country = country.lower()
        self.app_id = app_id
        self.app_key = app_key
        self.pages = max(pages, 1)

    def fetch(self, timeout_seconds: int) -> list[dict]:
        results: list[dict] = []
        for page in range(1, self.pages + 1):
            data = get_json(
                f"https://api.adzuna.com/v1/api/jobs/{self.country}/search/{page}",
                timeout_seconds,
                params={
                    "app_id": self.app_id,
                    "app_key": self.app_key,
                    "results_per_page": 50,
                    "what": "internship machine learning data science",
                    "where": "United States",
                    "content-type": "application/json",
                },
            )
            items = data.get("results", []) if isinstance(data, dict) else []
            for item in items:
                if not isinstance(item, dict):
                    continue
                company = item.get("company", {})
                location = item.get("location", {})
                results.append(
                    {
                        "source": self.name,
                        "external_id": str(item.get("id") or item.get("redirect_url") or ""),
                        "url": item.get("redirect_url", ""),
                        "title": item.get("title", ""),
                        "company": company.get("display_name", "") if isinstance(company, dict) else "",
                        "location": location.get("display_name", "") if isinstance(location, dict) else "",
                        "posted_at": item.get("created"),
                        "description": item.get("description", ""),
                        "skills": [],
                    }
                )
        return results
