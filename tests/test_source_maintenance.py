from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from job_hunter.config import Settings
from job_hunter.pipeline import build_sources
from job_hunter.source_maintenance import _probe_item, run_source_maintenance
from job_hunter.sources.greenhouse import GreenhouseSource
from job_hunter.storage import JobStore


def make_settings(db_path: str, data_dir: Path) -> Settings:
    return Settings(
        db_path=db_path,
        poll_interval_minutes=15,
        request_timeout_seconds=10,
        use_arbeitnow=False,
        use_remotive=False,
        use_themuse=False,
        use_greenhouse=True,
        use_lever=False,
        use_rss=False,
        use_usajobs=False,
        use_adzuna=False,
        min_relevance_score=3.0,
        min_eligibility_confidence=0.4,
        notify_on_ambiguous_eligibility=True,
        max_posting_age_days=7,
        telegram_bot_token=None,
        telegram_chat_id=None,
        themuse_pages=2,
        greenhouse_boards=["dead-board", "live-board"],
        lever_companies=[],
        rss_feeds=[],
        title_blacklist_patterns=[r"\\brecruiter\\b"],
        data_role_title_patterns=[r"\\bdata (science|scientist)\\b"],
        non_data_title_patterns=[r"\\bdeveloper advocacy\\b"],
        min_data_signal_count=2,
        greenhouse_token_file=str(data_dir / "greenhouse_tokens.txt"),
        lever_token_file=str(data_dir / "lever_tokens.txt"),
        rss_feed_file=str(data_dir / "rss_feeds.txt"),
        greenhouse_quarantine_file=str(data_dir / "greenhouse_tokens.quarantine.txt"),
        lever_quarantine_file=str(data_dir / "lever_tokens.quarantine.txt"),
        rss_quarantine_file=str(data_dir / "rss_feeds.quarantine.txt"),
        source_failure_quarantine_threshold=1,
        source_restore_success_threshold=2,
        usajobs_user_agent=None,
        usajobs_auth_key=None,
        usajobs_results_per_page=250,
        adzuna_app_id=None,
        adzuna_app_key=None,
        adzuna_country="us",
        adzuna_pages=2,
    )


def _write(path: Path, lines: list[str]) -> None:
    payload = "\n".join(lines)
    if payload:
        payload += "\n"
    path.write_text(payload, encoding="utf-8")


def _read(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class SourceMaintenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.data_dir = root / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = str(root / "test.db")
        self.settings = make_settings(self.db_path, self.data_dir)
        self.store = JobStore(self.db_path)

        _write(Path(self.settings.greenhouse_token_file or ""), ["dead-board", "live-board"])
        _write(Path(self.settings.greenhouse_quarantine_file or ""), [])
        _write(Path(self.settings.lever_token_file or ""), [])
        _write(Path(self.settings.lever_quarantine_file or ""), [])
        _write(Path(self.settings.rss_feed_file or ""), [])
        _write(Path(self.settings.rss_quarantine_file or ""), [])

    def tearDown(self) -> None:
        self.store.close()
        self.temp_dir.cleanup()

    def test_maintenance_quarantines_failed_items(self) -> None:
        self.store.record_source_item_results(
            "greenhouse",
            [{"item": "dead-board", "status": "failure", "error": "HTTP Error 404"}],
        )

        summary = run_source_maintenance(self.settings, self.store, probe_quarantine=False)

        active = _read(Path(self.settings.greenhouse_token_file or ""))
        quarantine = _read(Path(self.settings.greenhouse_quarantine_file or ""))
        self.assertEqual(summary["quarantined_count"], 1)
        self.assertEqual(active, ["live-board"])
        self.assertEqual(quarantine, ["dead-board"])

    def test_maintenance_restores_recovered_items(self) -> None:
        _write(Path(self.settings.greenhouse_token_file or ""), ["live-board"])
        _write(Path(self.settings.greenhouse_quarantine_file or ""), ["dead-board"])
        self.store.record_source_item_results(
            "greenhouse",
            [{"item": "dead-board", "status": "success", "error": ""}],
        )
        self.store.record_source_item_results(
            "greenhouse",
            [{"item": "dead-board", "status": "success", "error": ""}],
        )

        summary = run_source_maintenance(self.settings, self.store, probe_quarantine=False)

        active = _read(Path(self.settings.greenhouse_token_file or ""))
        quarantine = _read(Path(self.settings.greenhouse_quarantine_file or ""))
        self.assertEqual(summary["restored_count"], 1)
        self.assertEqual(active, ["live-board", "dead-board"])
        self.assertEqual(quarantine, [])

    def test_build_sources_skips_suppressed_tokens(self) -> None:
        self.store.record_source_item_results(
            "greenhouse",
            [{"item": "dead-board", "status": "failure", "error": "HTTP Error 404"}],
        )

        sources = build_sources(self.settings, store=self.store)
        greenhouse_sources = [source for source in sources if isinstance(source, GreenhouseSource)]
        self.assertEqual(len(greenhouse_sources), 1)
        self.assertEqual(greenhouse_sources[0].board_tokens, ["live-board"])

    def test_rss_probe_rejects_malformed_xml(self) -> None:
        class FakeResponse:
            def __enter__(self) -> "FakeResponse":
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                return None

            def read(self) -> bytes:
                return b"<rss><channel><item></rss>"

        with patch("urllib.request.urlopen", return_value=FakeResponse()):
            ok, error = _probe_item("rss", "https://example.com/feed.xml", timeout_seconds=1)

        self.assertFalse(ok)
        self.assertIn("mismatched", error)


if __name__ == "__main__":
    unittest.main()
