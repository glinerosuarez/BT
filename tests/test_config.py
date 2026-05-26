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
        self.assertEqual(settings.min_relevance_score, 3.0)
        self.assertEqual(settings.min_eligibility_confidence, 0.4)
        self.assertTrue(settings.notify_on_ambiguous_eligibility)


if __name__ == "__main__":
    unittest.main()
