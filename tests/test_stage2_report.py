from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from job_hunter.config import Settings
from job_hunter.pipeline import run_pipeline
from job_hunter.storage import JobStore
from job_hunter.stage2_report import main


class FakeSource:
    name = "fake"

    def __init__(self, payload):
        self.payload = payload

    def fetch(self, timeout_seconds: int):
        _ = timeout_seconds
        return self.payload


class FakeEmbeddingDiagnostics:
    def __init__(self) -> None:
        self.model_name = "fake-model"
        self.requested_device = "cpu"
        self.device = "cpu"
        self.device_source = "explicit"
        self.local_files_only = True
        self.batch_size = 16
        self.total_texts = 1
        self.total_batches = 1
        self.embedding_dimension = 3
        self.max_sequence_length = 8
        self.token_lengths = [10]
        self.overflow_tokens_per_text = [2]
        self.total_input_tokens = 10
        self.total_truncated_tokens = 2
        self.max_observed_tokens = 10
        self.truncated_count = 1
        self.truncated_indices = [0]
        self.truncated_job_rate = 1.0
        self.truncated_token_share = 0.2
        self.avg_overflow_tokens_on_truncated_jobs = 2.0
        self.p95_overflow_tokens = 2


class FakeEmbeddingResult:
    def __init__(self) -> None:
        self.diagnostics = FakeEmbeddingDiagnostics()


class FakeEmbeddingBackend:
    def __init__(
        self,
        model_name: str = "default",
        device: str | None = None,
        local_files_only: bool = True,
    ) -> None:
        self.model_name = model_name
        self.requested_device = device or "cpu"
        self.device = device or "cpu"
        self.local_files_only = local_files_only
        self.seen_texts: list[str] = []

    def embed_texts(self, texts: list[str], *, batch_size: int):
        self.seen_texts = list(texts)
        result = FakeEmbeddingResult()
        result.diagnostics.model_name = self.model_name
        result.diagnostics.requested_device = self.device
        result.diagnostics.device = self.device
        result.diagnostics.device_source = "explicit" if self.device != "auto" else "detected"
        result.diagnostics.local_files_only = self.local_files_only
        result.diagnostics.batch_size = batch_size
        result.diagnostics.total_texts = len(texts)
        return result


class FakeSemanticResult:
    def __init__(self) -> None:
        self.semantic_base_score = 0.84
        self.semantic_match_score = 0.77
        self.semantic_match_label = "pass"
        self.semantic_match_reason_codes = ["semantic_profile_data_engineering", "semantic_similarity_high"]
        self.semantic_research_heaviness_score = 0.07
        self.semantic_adjustment_reason_codes = ["semantic_penalty_masters_signal"]
        self.semantic_profile_id = "data_engineering"
        self.semantic_model_name = "fake-semantic-model"
        self.semantic_scorer_version = "semantic_shadow_v1"
        self.semantic_text_hash = "semantic-hash-1"


class FakeSemanticScorer:
    def __init__(self, backend) -> None:
        self.backend = backend

    def score_job_text(self, job_text: str):
        _ = job_text
        return FakeSemanticResult()


def make_settings(db_path: str) -> Settings:
    return Settings(
        db_path=db_path,
        poll_interval_minutes=15,
        request_timeout_seconds=10,
        use_arbeitnow=False,
        use_remotive=False,
        use_themuse=False,
        use_greenhouse=False,
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
        greenhouse_boards=[],
        lever_companies=[],
        rss_feeds=[],
        github_repo_readmes=[],
        ashby_boards=[],
        handshake_search_urls=[],
        title_blacklist_patterns=[r"\brecruiter\b"],
        data_role_title_patterns=[r"\b(machine learning|ml)\b", r"\bdata (science|scientist)\b", r"\bdata engineer(ing)?\b"],
        non_data_title_patterns=[r"\bdeveloper advocacy\b"],
        policy_reject_patterns=[r"\bph\.?d\.?\b"],
        min_data_signal_count=1,
        greenhouse_token_file=None,
        lever_token_file=None,
        rss_feed_file=None,
        greenhouse_quarantine_file=None,
        lever_quarantine_file=None,
        rss_quarantine_file=None,
        source_failure_quarantine_threshold=3,
        source_restore_success_threshold=2,
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


class Stage2ReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "test.db")
        self.settings = make_settings(self.db_path)
        self.store = JobStore(self.db_path)
        payload = [
            {
                "source": "fake",
                "external_id": "job-1",
                "url": "https://example.com/job-1",
                "title": "Data Engineering Intern",
                "company": "Finz",
                "location": "Remote - US",
                "posted_at": "2026-06-25T00:00:00+00:00",
                "description": "Build production ML systems with Python and SQL for our internship program.",
                "skills": ["python", "sql"],
            }
        ]
        with patch("job_hunter.pipeline.build_sources", return_value=[FakeSource(payload)]):
            run_pipeline(self.settings, self.store, None)
        self.store.close()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_list_renders_shadow_rows(self) -> None:
        buffer = io.StringIO()
        with patch("job_hunter.stage2_report.load_settings", return_value=self.settings):
            with patch("sys.argv", ["stage2_report.py", "list", "--limit", "5"]):
                with redirect_stdout(buffer):
                    rc = main()
        output = buffer.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("Data Engineering Intern", output)
        self.assertIn("stage2=", output)

    def test_show_renders_job_text_snapshot(self) -> None:
        buffer = io.StringIO()
        with patch("job_hunter.stage2_report.load_settings", return_value=self.settings):
            with patch("sys.argv", ["stage2_report.py", "show", "--job-id", "1"]):
                with redirect_stdout(buffer):
                    rc = main()
        output = buffer.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("job_text_snapshot:", output)
        self.assertIn("TITLE: Data Engineering Intern", output)

    def test_export_labeled_writes_json(self) -> None:
        store = JobStore(self.db_path)
        store.set_manual_fit_label(1, "good_fit", ["good_fit_ml_engineering"])
        store.close()

        output_path = Path(self.temp_dir.name) / "stage2-labeled.json"
        buffer = io.StringIO()
        with patch("job_hunter.stage2_report.load_settings", return_value=self.settings):
            with patch("sys.argv", ["stage2_report.py", "export-labeled", "--output", str(output_path), "--limit", "10"]):
                with redirect_stdout(buffer):
                    rc = main()
        output = buffer.getvalue()
        self.assertEqual(rc, 0)
        self.assertTrue(output_path.exists())
        payload = output_path.read_text(encoding="utf-8")
        self.assertIn("good_fit", payload)
        self.assertIn("job_text_snapshot", payload)
        self.assertIn("Wrote 1 labeled Stage 2 rows", output)

    def test_embedding_diagnostics_renders_text(self) -> None:
        buffer = io.StringIO()
        with patch("job_hunter.stage2_report.load_settings", return_value=self.settings):
            with patch("job_hunter.stage2_report._load_local_embedding_backend", return_value=FakeEmbeddingBackend):
                with patch(
                    "sys.argv",
                    ["stage2_report.py", "embedding-diagnostics", "--limit", "5", "--batch-size", "16"],
                ):
                    with redirect_stdout(buffer):
                        rc = main()
        output = buffer.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("sample_size=1", output)
        self.assertIn("requested_device=cpu", output)
        self.assertIn("truncated_job_rate=1.0000", output)
        self.assertIn("top_truncated_jobs:", output)
        self.assertIn("Data Engineering Intern", output)

    def test_embedding_diagnostics_renders_json(self) -> None:
        buffer = io.StringIO()
        with patch("job_hunter.stage2_report.load_settings", return_value=self.settings):
            with patch("job_hunter.stage2_report._load_local_embedding_backend", return_value=FakeEmbeddingBackend):
                with patch(
                    "sys.argv",
                    ["stage2_report.py", "embedding-diagnostics", "--limit", "5", "--format", "json"],
                ):
                    with redirect_stdout(buffer):
                        rc = main()
        output = buffer.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn('"sample_size": 1', output)
        self.assertIn('"local_files_only": true', output)
        self.assertIn('"truncated_token_share": 0.2', output)
        self.assertIn('"top_truncated_jobs"', output)

    def test_embedding_diagnostics_allow_network_flips_local_files_only(self) -> None:
        buffer = io.StringIO()
        with patch("job_hunter.stage2_report.load_settings", return_value=self.settings):
            with patch("job_hunter.stage2_report._load_local_embedding_backend", return_value=FakeEmbeddingBackend):
                with patch(
                    "sys.argv",
                    [
                        "stage2_report.py",
                        "embedding-diagnostics",
                        "--limit",
                        "5",
                        "--allow-network",
                        "--device",
                        "mps",
                        "--format",
                        "json",
                    ],
                ):
                    with redirect_stdout(buffer):
                        rc = main()
        output = buffer.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn('"requested_device": "mps"', output)
        self.assertIn('"device_source": "explicit"', output)
        self.assertIn('"local_files_only": false', output)

    def test_semantic_backfill_updates_rows(self) -> None:
        buffer = io.StringIO()
        with patch("job_hunter.stage2_report.load_settings", return_value=self.settings):
            with patch("job_hunter.stage2_report._load_local_embedding_backend", return_value=FakeEmbeddingBackend):
                with patch("job_hunter.stage2_report._load_semantic_shadow_scorer", return_value=FakeSemanticScorer):
                    with patch(
                        "sys.argv",
                        ["stage2_report.py", "semantic-backfill", "--limit", "5"],
                    ):
                        with redirect_stdout(buffer):
                            rc = main()
        output = buffer.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("updated_count=1", output)
        self.assertIn("semantic=pass", output)
        self.assertIn("base=0.84", output)
        self.assertIn("penalty=0.07", output)
        store = JobStore(self.db_path)
        row = store.get_stage2_job(1)
        store.close()
        self.assertEqual(row["semantic_match_label"], "pass")
        self.assertEqual(row["semantic_profile_id"], "data_engineering")

    def test_semantic_backfill_renders_json(self) -> None:
        buffer = io.StringIO()
        with patch("job_hunter.stage2_report.load_settings", return_value=self.settings):
            with patch("job_hunter.stage2_report._load_local_embedding_backend", return_value=FakeEmbeddingBackend):
                with patch("job_hunter.stage2_report._load_semantic_shadow_scorer", return_value=FakeSemanticScorer):
                    with patch(
                        "sys.argv",
                        ["stage2_report.py", "semantic-backfill", "--limit", "5", "--format", "json"],
                    ):
                        with redirect_stdout(buffer):
                            rc = main()
        output = buffer.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn('"updated_count": 1', output)
        self.assertIn('"semantic_match_label": "pass"', output)

    def test_disagreement_report_renders_counts_and_rows(self) -> None:
        store = JobStore(self.db_path)
        store.update_semantic_shadow(
            1,
            semantic_match_score=0.77,
            semantic_match_label="pass",
            semantic_match_reason_codes=["semantic_similarity_high"],
            semantic_base_score=0.84,
            semantic_research_heaviness_score=0.07,
            semantic_adjustment_reason_codes=["semantic_penalty_masters_signal"],
            semantic_profile_id="data_engineering",
            semantic_model_name="fake-semantic-model",
            semantic_scorer_version="semantic_shadow_v1",
            semantic_text_hash="semantic-hash-1",
        )
        store.set_manual_fit_label(1, "bad_fit", ["bad_fit_content_role"])
        store.close()

        buffer = io.StringIO()
        with patch("job_hunter.stage2_report.load_settings", return_value=self.settings):
            with patch(
                "sys.argv",
                ["stage2_report.py", "disagreement-report", "--limit", "5", "--format", "text"],
            ):
                with redirect_stdout(buffer):
                    rc = main()
        output = buffer.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("disagreement_count=1", output)
        self.assertIn("deterministic_vs_manual=1", output)
        self.assertIn("semantic_vs_manual=1", output)
        self.assertIn("Data Engineering Intern", output)
        self.assertIn("normalized_manual=reject", output)
        self.assertIn("axes=deterministic_vs_manual,semantic_vs_manual", output)


if __name__ == "__main__":
    unittest.main()
