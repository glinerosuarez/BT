from __future__ import annotations

import unittest

from job_hunter.sources.apple import _build_row, _search_payload


SAMPLE_PAYLOAD = {
    "searchResults": [
        {
            "id": "abc",
            "positionId": "200700001-1234",
            "postingTitle": "Machine Learning Engineer Intern",
            "postDateInGMT": "2026-07-20T00:00:00.000Z",
            "jobSummary": "Build and deploy machine learning systems with Python.",
            "teamName": "Siri",
            "weeklyHours": "40 Hours",
            "locations": [{"cityName": "Cupertino", "stateName": "California", "countryName": "United States"}],
        }
    ]
}


class AppleJobsSourceTests(unittest.TestCase):
    def test_build_row_maps_structured_search_result(self) -> None:
        row = _build_row(SAMPLE_PAYLOAD["searchResults"][0], "machine learning intern")

        self.assertEqual(row["source"], "apple")
        self.assertEqual(row["external_id"], "200700001-1234")
        self.assertEqual(row["company"], "Apple")
        self.assertEqual(row["title"], "Machine Learning Engineer Intern")
        self.assertIn("Cupertino, California, United States", row["location"])
        self.assertIn("Build and deploy", row["description"])
        self.assertEqual(row["source_metadata"]["detail_quality_status"], "search_summary")
        self.assertEqual(row["url"], "https://jobs.apple.com/en-us/details/200700001-1234")

    def test_search_payload_uses_us_scope_and_internship_query(self) -> None:
        payload = _search_payload("machine learning intern")

        self.assertEqual(payload["query"], "machine learning intern")
        self.assertEqual(payload["filters"]["locations"], ["postLocation-USA"])
        self.assertEqual(payload["sort"], "relevance")


if __name__ == "__main__":
    unittest.main()
