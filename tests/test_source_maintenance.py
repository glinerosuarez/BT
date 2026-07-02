from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
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
        use_github_repos=False,
        use_ashby=False,
        use_handshake=False,
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
        github_repo_readmes=[],
        ashby_boards=[],
        handshake_search_urls=[],
        title_blacklist_patterns=[r"\\brecruiter\\b"],
        data_role_title_patterns=[r"\\bdata (science|scientist)\\b"],
        non_data_title_patterns=[r"\\bdeveloper advocacy\\b"],
        policy_reject_patterns=[
            r"\\bph\\.?d\\.?\\b",
            r"\\bdoctoral\\b",
            r"\\beconomics team\\b",
            r"\\boperations research\\b",
        ],
        min_data_signal_count=2,
        greenhouse_token_file=str(data_dir / "greenhouse_tokens.txt"),
        lever_token_file=str(data_dir / "lever_tokens.txt"),
        rss_feed_file=str(data_dir / "rss_feeds.txt"),
        greenhouse_quarantine_file=str(data_dir / "greenhouse_tokens.quarantine.txt"),
        lever_quarantine_file=str(data_dir / "lever_tokens.quarantine.txt"),
        rss_quarantine_file=str(data_dir / "rss_feeds.quarantine.txt"),
        source_failure_quarantine_threshold=1,
        source_restore_success_threshold=2,
        source_probe_limit_per_run=5,
        handshake_profile_dir=".handshake-profile",
        handshake_headless=True,
        handshake_max_results=25,
        handshake_page_timeout_seconds=30,
        handshake_fetch_details=True,
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

    def test_list_recent_handshake_quarantined_urls(self) -> None:
        self.store._conn.execute(
            """
            INSERT INTO jobs (
                dedupe_key, source, external_id, url, title, company, location, is_internship,
                posted_at, description, compensation_type, work_auth_signals, sponsorship_signals,
                skills, ingested_at, relevance_score, eligibility_confidence, eligibility_status,
                relevance_hits, source_quality_status, source_quality_reason_codes
            ) VALUES (?, 'handshake', ?, ?, ?, ?, ?, 1, ?, ?, 'unknown', '[]', '[]', '[]', ?, 0.0, 0.0, 'ambiguous', '[]', ?, '[]')
            """,
            (
                "dq1",
                "job-1",
                "https://app.joinhandshake.com/jobs/111",
                "Data Engineering Intern",
                "Example",
                "Remote",
                recent_posted_at := "2026-06-28",
                "Example description",
                datetime.now(timezone.utc).isoformat(),
                "detail_polluted",
            ),
        )
        self.store._conn.execute(
            """
            INSERT INTO jobs (
                dedupe_key, source, external_id, url, title, company, location, is_internship,
                posted_at, description, compensation_type, work_auth_signals, sponsorship_signals,
                skills, ingested_at, relevance_score, eligibility_confidence, eligibility_status,
                relevance_hits, source_quality_status, source_quality_reason_codes
            ) VALUES (?, 'handshake', ?, ?, ?, ?, ?, 1, ?, ?, 'unknown', '[]', '[]', '[]', ?, 0.0, 0.0, 'ambiguous', '[]', ?, '[]')
            """,
            (
                "dq2",
                "job-2",
                "https://app.joinhandshake.com/jobs/222",
                "Data Engineering Intern",
                "Example",
                "Remote",
                recent_posted_at,
                "Example description",
                datetime.now(timezone.utc).isoformat(),
                "detail_complete",
            ),
        )
        self.store._conn.commit()

        urls = self.store.list_recent_handshake_quarantined_urls(days=7, limit=10)
        self.assertEqual(urls, ["https://app.joinhandshake.com/jobs/111"])

    def test_list_recent_handshake_suspect_urls_includes_summary_beta_rows(self) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        self.store._conn.execute(
            """
            INSERT INTO jobs (
                dedupe_key, source, external_id, url, title, company, location, is_internship,
                posted_at, description, compensation_type, work_auth_signals, sponsorship_signals,
                skills, ingested_at, relevance_score, eligibility_confidence, eligibility_status,
                relevance_hits, source_quality_status, source_quality_reason_codes
            ) VALUES (?, 'handshake', ?, ?, ?, ?, ?, 1, ?, ?, 'unknown', '[]', '[]', '[]', ?, 0.0, 0.0, 'ambiguous', '[]', ?, '[]')
            """,
            (
                "suspect1",
                "job-suspect",
                "https://app.joinhandshake.com/jobs/333",
                "Business Analyst Intern",
                "Example",
                "Remote",
                "2026-06-28",
                "Save Share Apply externally Summary Beta This job is about data engineering. Job description Real business analyst role.",
                now_iso,
                "detail_complete",
            ),
        )
        self.store._conn.execute(
            """
            INSERT INTO jobs (
                dedupe_key, source, external_id, url, title, company, location, is_internship,
                posted_at, description, compensation_type, work_auth_signals, sponsorship_signals,
                skills, ingested_at, relevance_score, eligibility_confidence, eligibility_status,
                relevance_hits, source_quality_status, source_quality_reason_codes
            ) VALUES (?, 'handshake', ?, ?, ?, ?, ?, 1, ?, ?, 'unknown', '[]', '[]', '[]', ?, 0.0, 0.0, 'ambiguous', '[]', ?, '[]')
            """,
            (
                "suspect2",
                "job-polluted",
                "https://app.joinhandshake.com/jobs/444",
                "Communications Intern",
                "Example",
                "Remote",
                "2026-06-28",
                "Example description",
                now_iso,
                "detail_polluted",
            ),
        )
        self.store._conn.commit()

        urls = self.store.list_recent_handshake_suspect_urls(days=30, limit=10)
        self.assertEqual(
            urls,
            [
                "https://app.joinhandshake.com/jobs/444",
                "https://app.joinhandshake.com/jobs/333",
            ],
        )

    def test_cleanup_handshake_duplicate_rows_keeps_cleaner_canonical_row(self) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        self.store._conn.execute(
            """
            INSERT INTO jobs (
                dedupe_key, source, external_id, url, title, company, location, is_internship,
                posted_at, description, compensation_type, work_auth_signals, sponsorship_signals,
                skills, ingested_at, relevance_score, eligibility_confidence, eligibility_status,
                relevance_hits, source_quality_status, source_quality_reason_codes, notified
            ) VALUES (?, 'handshake', ?, ?, ?, ?, ?, 1, ?, ?, 'unknown', '[]', '[]', '[]', ?, 0.0, 0.0, 'ambiguous', '[]', ?, '[]', 1)
            """,
            (
                "clean-key",
                "job-clean",
                "https://app.joinhandshake.com/jobs/11149721",
                "AI Engineering Intern, Voice & LLM Systems",
                "Presto",
                "Remote, based in United States",
                "2026-06-23",
                "Presto\nInternet & Software\nAI Engineering Intern, Voice & LLM Systems\nPosted 5 days ago",
                now_iso,
                "detail_complete",
            ),
        )
        self.store._conn.execute(
            """
            INSERT INTO seen_events (dedupe_key, first_seen_at, last_seen_at, seen_count, notified)
            VALUES (?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 1, 1)
            """,
            ("clean-key",),
        )
        self.store._conn.execute(
            """
            INSERT INTO jobs (
                dedupe_key, source, external_id, url, title, company, location, is_internship,
                posted_at, description, compensation_type, work_auth_signals, sponsorship_signals,
                skills, ingested_at, relevance_score, eligibility_confidence, eligibility_status,
                relevance_hits, source_quality_status, source_quality_reason_codes, notified
            ) VALUES (?, 'handshake', ?, ?, ?, ?, ?, 1, ?, ?, 'unknown', '[]', '[]', '[]', ?, 0.0, 0.0, 'ambiguous', '[]', ?, '[]', 1)
            """,
            (
                "noisy-key",
                "job-noisy",
                "https://app.joinhandshake.com/jobs/11149721",
                "AI Engineering Intern, Voice & LLM Systems",
                "Presto",
                "Remote, based in United States",
                "2026-06-23",
                "Skip to content Explore Jobs Inbox Feed AI showcase Events People Employers Career center AI work Get the app 28 Presto noisy body",
                now_iso,
                "detail_complete",
            ),
        )
        self.store._conn.execute(
            """
            INSERT INTO seen_events (dedupe_key, first_seen_at, last_seen_at, seen_count, notified)
            VALUES (?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 1, 1)
            """,
            ("noisy-key",),
        )
        self.store._conn.commit()

        summary = self.store.cleanup_handshake_duplicate_rows()
        self.assertEqual(summary["deleted_count"], 1)
        rows = self.store._conn.execute(
            "SELECT id, dedupe_key, description FROM jobs WHERE url = 'https://app.joinhandshake.com/jobs/11149721'"
        ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["dedupe_key"], "clean-key")
        self.assertNotIn("Skip to content", rows[0]["description"])

    def test_cleanup_handshake_duplicate_rows_merges_normalized_url_and_stage2_fields(self) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        self.store._conn.execute(
            """
            INSERT INTO jobs (
                dedupe_key, source, external_id, url, title, company, location, is_internship,
                posted_at, description, compensation_type, work_auth_signals, sponsorship_signals,
                skills, ingested_at, relevance_score, eligibility_confidence, eligibility_status,
                relevance_hits, source_quality_status, source_quality_reason_codes,
                profile_match_score, profile_match_label, profile_match_reason_codes,
                profile_version, scorer_version, job_text_version, job_text_snapshot,
                semantic_match_score, semantic_match_label, semantic_match_reason_codes,
                semantic_base_score, semantic_research_heaviness_score, semantic_adjustment_reason_codes,
                semantic_profile_id, semantic_model_name, semantic_scorer_version, semantic_text_hash
            ) VALUES (?, 'handshake', ?, ?, ?, ?, ?, 1, ?, ?, 'unknown', '[]', '[]', '[]', ?, 0.0, 0.0, 'ambiguous', '[]', ?, '[]', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "search-key",
                "job-search",
                "https://app.joinhandshake.com/job-search/11149721?query=machine+learning&sort=posted_date_desc",
                "AI Engineering Intern, Voice & LLM Systems",
                "Presto",
                "Remote",
                "2026-06-27",
                "Search row description",
                now_iso,
                "",
                0.92,
                "pass",
                '["target_title_alignment"]',
                "default_v1",
                "shadow_rules_v1",
                "job_text_v1",
                "TITLE: AI Engineering Intern",
                0.81,
                "pass",
                '["semantic_similarity_high"]',
                0.79,
                0.05,
                '["semantic_penalty_masters_signal"]',
                "data_engineering",
                "fake-semantic-model",
                "semantic_shadow_v1",
                "abc123",
            ),
        )
        self.store._conn.execute(
            """
            INSERT INTO jobs (
                dedupe_key, source, external_id, url, title, company, location, is_internship,
                posted_at, description, compensation_type, work_auth_signals, sponsorship_signals,
                skills, ingested_at, relevance_score, eligibility_confidence, eligibility_status,
                relevance_hits, source_quality_status, source_quality_reason_codes
            ) VALUES (?, 'handshake', ?, ?, ?, ?, ?, 1, ?, ?, 'unknown', '[]', '[]', '[]', ?, 0.0, 0.0, 'ambiguous', '[]', ?, '[]')
            """,
            (
                "detail-key",
                "job-detail",
                "https://app.joinhandshake.com/jobs/11149721?searchId=abc",
                "AI Engineering Intern, Voice & LLM Systems",
                "Presto",
                "Remote",
                "2026-06-28",
                "Clean direct job page description",
                now_iso,
                "detail_complete",
            ),
        )
        self.store._conn.commit()

        summary = self.store.cleanup_handshake_duplicate_rows()
        self.assertEqual(summary["deleted_count"], 1)
        row = self.store._conn.execute(
            """
            SELECT id, dedupe_key, url, source_quality_status,
                   profile_match_score, profile_match_label, profile_match_reason_codes,
                   profile_version, scorer_version, job_text_version, job_text_snapshot,
                   semantic_match_score, semantic_match_label, semantic_match_reason_codes,
                   semantic_profile_id, semantic_model_name, semantic_scorer_version, semantic_text_hash
            FROM jobs
            WHERE source = 'handshake'
            """
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["dedupe_key"], "detail-key")
        self.assertEqual(row["source_quality_status"], "detail_complete")
        self.assertEqual(row["profile_match_label"], "pass")
        self.assertAlmostEqual(float(row["profile_match_score"]), 0.92)
        self.assertEqual(row["job_text_version"], "job_text_v1")
        self.assertIn("AI Engineering Intern", row["job_text_snapshot"])
        self.assertEqual(row["semantic_match_label"], "pass")
        self.assertAlmostEqual(float(row["semantic_match_score"]), 0.81)
        self.assertEqual(row["semantic_profile_id"], "data_engineering")
        self.assertEqual(row["semantic_model_name"], "fake-semantic-model")
        self.assertEqual(row["semantic_scorer_version"], "semantic_shadow_v1")
        self.assertEqual(row["semantic_text_hash"], "abc123")

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

    def test_canonical_handshake_row_ignores_search_id_noise(self) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        self.store._conn.execute(
            """
            INSERT INTO jobs (
                dedupe_key, source, external_id, url, title, company, location, is_internship,
                posted_at, description, compensation_type, work_auth_signals, sponsorship_signals,
                skills, ingested_at, relevance_score, eligibility_confidence, eligibility_status,
                relevance_hits, source_quality_status, source_quality_reason_codes, notified
            ) VALUES (?, 'handshake', ?, ?, ?, ?, ?, 1, ?, ?, 'unknown', '[]', '[]', '[]', ?, 0.0, 0.0, 'ambiguous', '[]', ?, '[]', 0)
            """,
            (
                "dirty-key",
                "job-dirty",
                "https://app.joinhandshake.com/jobs/11162812?searchId=old",
                "Citizens for Responsibility and Ethics in Washington (CREW) is seeking a paid full-time or part-time communications intern.",
                "Example",
                "Remote",
                "2026-06-28",
                "Dirty description",
                now_iso,
                "detail_polluted",
            ),
        )
        self.store._conn.execute(
            """
            INSERT INTO jobs (
                dedupe_key, source, external_id, url, title, company, location, is_internship,
                posted_at, description, compensation_type, work_auth_signals, sponsorship_signals,
                skills, ingested_at, relevance_score, eligibility_confidence, eligibility_status,
                relevance_hits, source_quality_status, source_quality_reason_codes, notified
            ) VALUES (?, 'handshake', ?, ?, ?, ?, ?, 1, ?, ?, 'unknown', '[]', '[]', '[]', ?, 0.0, 0.0, 'ambiguous', '[]', ?, '[]', 0)
            """,
            (
                "clean-key",
                "job-clean",
                "https://app.joinhandshake.com/jobs/11162812?searchId=new",
                "Communications Intern",
                "Example",
                "Remote",
                "2026-06-28",
                "Clean description",
                now_iso,
                "detail_complete",
            ),
        )
        self.store._conn.commit()

        rows = self.store.list_jobs_for_labeling(limit=10, unlabeled_only=False)
        handshake_rows = [row for row in rows if row["source"] == "handshake"]
        self.assertEqual(len(handshake_rows), 1)
        self.assertEqual(handshake_rows[0]["title"], "Communications Intern")

    def test_canonical_handshake_row_merges_job_search_and_jobs_urls(self) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        self.store._conn.execute(
            """
            INSERT INTO jobs (
                dedupe_key, source, external_id, url, title, company, location, is_internship,
                posted_at, description, compensation_type, work_auth_signals, sponsorship_signals,
                skills, ingested_at, relevance_score, eligibility_confidence, eligibility_status,
                relevance_hits, source_quality_status, source_quality_reason_codes, notified
            ) VALUES (?, 'handshake', ?, ?, ?, ?, ?, 1, ?, ?, 'unknown', '[]', '[]', '[]', ?, 0.0, 0.0, 'ambiguous', '[]', ?, '[]', 0)
            """,
            (
                "search-key",
                "job-search-shape",
                "https://app.joinhandshake.com/job-search/11162812?query=data+engineer+intern&page=1",
                "Communications Intern",
                "Example",
                "Remote",
                "2026-06-28",
                "Card-derived description",
                now_iso,
                "card_only",
            ),
        )
        self.store._conn.execute(
            """
            INSERT INTO jobs (
                dedupe_key, source, external_id, url, title, company, location, is_internship,
                posted_at, description, compensation_type, work_auth_signals, sponsorship_signals,
                skills, ingested_at, relevance_score, eligibility_confidence, eligibility_status,
                relevance_hits, source_quality_status, source_quality_reason_codes, notified
            ) VALUES (?, 'handshake', ?, ?, ?, ?, ?, 1, ?, ?, 'unknown', '[]', '[]', '[]', ?, 0.0, 0.0, 'ambiguous', '[]', ?, '[]', 0)
            """,
            (
                "jobs-key",
                "job-detail-shape",
                "https://app.joinhandshake.com/jobs/11162812?searchId=new",
                "Communications Intern",
                "Example",
                "Remote",
                "2026-06-28",
                "Detail description",
                now_iso,
                "detail_complete",
            ),
        )
        self.store._conn.commit()

        rows = self.store.list_jobs_for_labeling(limit=10, unlabeled_only=False)
        handshake_rows = [row for row in rows if row["source"] == "handshake"]
        self.assertEqual(len(handshake_rows), 1)
        self.assertEqual(handshake_rows[0]["url"], "https://app.joinhandshake.com/jobs/11162812?searchId=new")

    def test_canonical_handshake_row_prefers_clean_title_over_polluted_title(self) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        self.store._conn.execute(
            """
            INSERT INTO jobs (
                dedupe_key, source, external_id, url, title, company, location, is_internship,
                posted_at, description, compensation_type, work_auth_signals, sponsorship_signals,
                skills, ingested_at, relevance_score, eligibility_confidence, eligibility_status,
                relevance_hits, source_quality_status, source_quality_reason_codes, notified
            ) VALUES (?, 'handshake', ?, ?, ?, ?, ?, 1, ?, ?, 'unknown', '[]', '[]', '[]', ?, 0.0, 0.0, 'ambiguous', '[]', ?, '[]', 0)
            """,
            (
                "polluted-title-key",
                "job-polluted-title",
                "https://app.joinhandshake.com/jobs/11162812?searchId=old",
                "Citizens for Responsibility and Ethics in Washington (CREW) is seeking a paid full-time or part-time communications intern. As a nonpartisan nonprofit government watchdog.",
                "Example",
                "Remote",
                "2026-06-28",
                "Detail description",
                now_iso,
                "detail_complete",
            ),
        )
        self.store._conn.execute(
            """
            INSERT INTO jobs (
                dedupe_key, source, external_id, url, title, company, location, is_internship,
                posted_at, description, compensation_type, work_auth_signals, sponsorship_signals,
                skills, ingested_at, relevance_score, eligibility_confidence, eligibility_status,
                relevance_hits, source_quality_status, source_quality_reason_codes, notified
            ) VALUES (?, 'handshake', ?, ?, ?, ?, ?, 1, ?, ?, 'unknown', '[]', '[]', '[]', ?, 0.0, 0.0, 'ambiguous', '[]', ?, '[]', 0)
            """,
            (
                "clean-title-key",
                "job-clean-title",
                "https://app.joinhandshake.com/jobs/11162812?searchId=new",
                "Communications Intern",
                "Example",
                "Remote",
                "2026-06-28",
                "Detail description",
                now_iso,
                "detail_complete",
            ),
        )
        self.store._conn.commit()

        rows = self.store.list_jobs_for_labeling(limit=10, unlabeled_only=False)
        handshake_rows = [row for row in rows if row["source"] == "handshake"]
        self.assertEqual(len(handshake_rows), 1)
        self.assertEqual(handshake_rows[0]["title"], "Communications Intern")


if __name__ == "__main__":
    unittest.main()
