from __future__ import annotations

from job_hunter.sources.base import SourceConnector, get_json


class TheMuseSource(SourceConnector):
    def __init__(self, pages: int = 2) -> None:
        super().__init__(name="themuse")
        self.pages = pages

    def fetch(self, timeout_seconds: int) -> list[dict]:
        results: list[dict] = []
        for page in range(1, self.pages + 1):
            data = get_json(
                "https://www.themuse.com/api/public/jobs",
                timeout_seconds,
                params={"page": page, "descending": "true"},
            )
            items = data.get("results", []) if isinstance(data, dict) else []
            for item in items:
                if not isinstance(item, dict):
                    continue
                locations = item.get("locations", [])
                location_text = ", ".join(
                    str(loc.get("name", ""))
                    for loc in locations
                    if isinstance(loc, dict) and loc.get("name")
                )
                company = item.get("company", {})
                refs = item.get("refs", {})
                results.append(
                    {
                        "source": self.name,
                        "external_id": str(item.get("id") or refs.get("landing_page") or ""),
                        "url": refs.get("landing_page", ""),
                        "title": item.get("name", ""),
                        "company": company.get("name", "") if isinstance(company, dict) else "",
                        "location": location_text,
                        "posted_at": item.get("publication_date"),
                        "description": item.get("contents", ""),
                        "skills": [],
                    }
                )
        return results
