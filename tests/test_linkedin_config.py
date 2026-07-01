from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from job_hunter.config import load_settings


class LinkedInConfigTests(unittest.TestCase):
    def test_defaults(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = load_settings()
        self.assertFalse(settings.use_linkedin)
        self.assertEqual(settings.linkedin_search_urls, [])
        self.assertTrue(settings.linkedin_profile_dir.endswith(".linkedin-profile"))
        self.assertTrue(settings.linkedin_headless)
        self.assertEqual(settings.linkedin_max_results, 25)
        self.assertEqual(settings.linkedin_page_timeout_seconds, 30)
        self.assertTrue(settings.linkedin_fetch_details)

    def test_requested_dotenv_loads_linkedin_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        "JOB_HUNTER_SOURCE_LINKEDIN=true",
                        "JOB_HUNTER_LINKEDIN_SEARCH_URLS=\"https://www.linkedin.com/jobs/search/?keywords=data%20engineer\"",
                        "JOB_HUNTER_LINKEDIN_HEADLESS=false",
                    ]
                ),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                settings = load_settings(load_dotenv=True, dotenv_path=str(env_path))
        self.assertTrue(settings.use_linkedin)
        self.assertEqual(settings.linkedin_search_urls, ["https://www.linkedin.com/jobs/search/?keywords=data%20engineer"])
        self.assertFalse(settings.linkedin_headless)


if __name__ == "__main__":
    unittest.main()
