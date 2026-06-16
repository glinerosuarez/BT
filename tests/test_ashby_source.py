from __future__ import annotations

import unittest
from unittest.mock import patch

from job_hunter.sources.ashby import AshbySource


SAMPLE_HTML = """
<html>
  <body>
    <script>
      window.__appData = {
        "organization": {"name": "Etched"},
        "jobBoard": {
          "jobPostings": [
            {
              "id": "abc123",
              "jobId": "job-1",
              "title": "Inference Intern",
              "publishedDate": "2026-05-19",
              "updatedAt": "2026-05-22T23:45:14.915Z",
              "locationName": "San Jose",
              "secondaryLocations": [{"locationName": "Remote - US"}],
              "teamName": "Software",
              "departmentName": "Software",
              "employmentType": "Intern",
              "workplaceType": "OnSite",
              "compensationTierSummary": null,
              "isListed": true
            }
          ]
        }
      };
    </script>
  </body>
</html>
"""


class FakeResponse:
    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return SAMPLE_HTML.encode("utf-8")


class AshbySourceTests(unittest.TestCase):
    def test_fetch_parses_public_job_board_payload(self) -> None:
        source = AshbySource(board_slugs=["Etched"])

        with patch("urllib.request.urlopen", return_value=FakeResponse()):
            rows = source.fetch(timeout_seconds=5)

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["source"], "ashby")
        self.assertEqual(row["company"], "Etched")
        self.assertEqual(row["title"], "Inference Intern")
        self.assertEqual(row["posted_at"], "2026-05-19")
        self.assertIn("Remote - US", row["location"])
        self.assertIn("Employment type: Intern.", row["description"])


if __name__ == "__main__":
    unittest.main()
