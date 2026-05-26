from __future__ import annotations

from job_hunter.sources.base import SourceConnector, get_json


class USAJobsSource(SourceConnector):
    def __init__(self, user_agent: str, auth_key: str, results_per_page: int = 250) -> None:
        super().__init__(name="usajobs")
        self.user_agent = user_agent
        self.auth_key = auth_key
        self.results_per_page = max(results_per_page, 1)

    def fetch(self, timeout_seconds: int) -> list[dict]:
        data = get_json(
            "https://data.usajobs.gov/api/search",
            timeout_seconds,
            params={
                "Keyword": "intern OR internship OR co-op OR machine learning OR data science",
                "ResultsPerPage": self.results_per_page,
            },
            headers={
                "Host": "data.usajobs.gov",
                "User-Agent": self.user_agent,
                "Authorization-Key": self.auth_key,
            },
        )

        search_result = data.get("SearchResult", {}) if isinstance(data, dict) else {}
        items = search_result.get("SearchResultItems", []) if isinstance(search_result, dict) else []
        results: list[dict] = []

        for item in items:
            if not isinstance(item, dict):
                continue
            descriptor = item.get("MatchedObjectDescriptor", {})
            if not isinstance(descriptor, dict):
                continue
            locations = descriptor.get("PositionLocationDisplay", "")
            qualification = _extract_qualification(descriptor)
            results.append(
                {
                    "source": self.name,
                    "external_id": str(descriptor.get("PositionID") or descriptor.get("PositionURI") or ""),
                    "url": descriptor.get("PositionURI", ""),
                    "title": descriptor.get("PositionTitle", ""),
                    "company": descriptor.get("OrganizationName", "USAJobs"),
                    "location": locations,
                    "posted_at": descriptor.get("PublicationStartDate"),
                    "description": qualification,
                    "skills": [],
                }
            )
        return results


def _extract_qualification(descriptor: dict) -> str:
    user_area = descriptor.get("UserArea", {})
    if not isinstance(user_area, dict):
        return ""
    details = user_area.get("Details", {})
    if not isinstance(details, dict):
        return ""
    lines: list[str] = []
    for key in ("JobSummary", "MajorDuties", "QualificationSummary", "Education"):
        value = details.get(key)
        if isinstance(value, str) and value.strip():
            lines.append(value.strip())
    return " ".join(lines)
