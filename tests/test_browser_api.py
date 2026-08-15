from __future__ import annotations

import unittest

from job_hunter.browser_api import load_browser_api, normalize_browser_backend


class BrowserApiTests(unittest.TestCase):
    def test_normalize_backend_accepts_supported_values(self) -> None:
        self.assertEqual(normalize_browser_backend("playwright"), "playwright")
        self.assertEqual(normalize_browser_backend("Patchright"), "patchright")
        with self.assertRaises(ValueError):
            normalize_browser_backend("chromium")

    def test_both_backends_are_importable(self) -> None:
        self.assertEqual(load_browser_api("playwright").name, "playwright")
        self.assertEqual(load_browser_api("patchright").name, "patchright")


if __name__ == "__main__":
    unittest.main()
