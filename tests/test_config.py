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
        self.assertFalse(settings.use_github_repos)
        self.assertTrue(settings.use_ashby)
        self.assertFalse(settings.use_handshake)
        self.assertEqual(settings.min_relevance_score, 3.0)
        self.assertEqual(settings.min_eligibility_confidence, 0.4)
        self.assertTrue(settings.notify_on_ambiguous_eligibility)
        self.assertEqual(settings.max_posting_age_days, 7)
        self.assertTrue(settings.greenhouse_boards)
        self.assertTrue(settings.lever_companies)
        self.assertTrue(settings.rss_feeds)
        self.assertTrue(settings.github_repo_readmes)
        self.assertTrue(settings.ashby_boards)
        self.assertEqual(settings.handshake_search_urls, [])
        self.assertTrue(settings.title_blacklist_patterns)
        self.assertTrue(settings.data_role_title_patterns)
        self.assertTrue(settings.non_data_title_patterns)
        self.assertTrue(settings.policy_reject_patterns)
        self.assertEqual(settings.min_data_signal_count, 2)
        self.assertIsNotNone(settings.greenhouse_token_file)
        self.assertIsNotNone(settings.greenhouse_quarantine_file)
        self.assertIsNotNone(settings.lever_quarantine_file)
        self.assertIsNotNone(settings.rss_quarantine_file)
        self.assertEqual(settings.source_failure_quarantine_threshold, 2)
        self.assertEqual(settings.source_restore_success_threshold, 2)
        self.assertTrue(settings.handshake_profile_dir.endswith(".handshake-profile"))
        self.assertTrue(settings.handshake_headless)
        self.assertEqual(settings.handshake_max_results, 25)
        self.assertEqual(settings.handshake_page_timeout_seconds, 30)
        self.assertTrue(settings.handshake_fetch_details)


if __name__ == "__main__":
    unittest.main()
