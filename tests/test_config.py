from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from job_hunter.config import load_settings


class ConfigTests(unittest.TestCase):
    def test_defaults(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = load_settings()
        self.assertEqual(settings.poll_interval_minutes, 15)
        self.assertTrue(settings.use_arbeitnow)
        self.assertTrue(settings.use_greenhouse)
        self.assertTrue(settings.use_lever)
        self.assertTrue(settings.use_rss)
        self.assertEqual(settings.min_relevance_score, 3.0)
        self.assertEqual(settings.min_eligibility_confidence, 0.4)
        self.assertTrue(settings.notify_on_ambiguous_eligibility)
        self.assertEqual(settings.max_posting_age_days, 7)
        self.assertTrue(settings.greenhouse_boards)
        self.assertTrue(settings.lever_companies)
        self.assertTrue(settings.rss_feeds)
        self.assertTrue(settings.title_blacklist_patterns)
        self.assertIsNotNone(settings.greenhouse_token_file)


if __name__ == "__main__":
    unittest.main()
