from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from job_hunter.label_jobs import _parse_reason_codes, main
from job_hunter.models import JobRecord
from job_hunter.storage import JobStore


class LabelJobsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "test.db")
        self.store = JobStore(self.db_path)
        job = JobRecord(
            source="fake",
            external_id="job-1",
            url="https://example.com/job-1",
            title="Machine Learning Intern",
            company="Acme",
            location="Remote - US",
            is_internship=True,
            posted_at="2026-06-14T00:00:00+00:00",
            description="Build production ML systems.",
            ingested_at="2026-06-14T01:00:00+00:00",
            relevance_score=5.0,
            eligibility_confidence=0.6,
            eligibility_status="ambiguous",
        )
        self.store.insert_job(job, "dedupe-1")

    def tearDown(self) -> None:
        self.store.close()
        self.temp_dir.cleanup()

    def test_parse_reason_codes_deduplicates(self) -> None:
        values = _parse_reason_codes(
            "bad_fit_phd_only,bad_fit_phd_only,borderline_conflicting_work_auth,bad_fit_domain_mismatch"
        )
        self.assertEqual(values, ["bad_fit_phd_only", "borderline_conflicting_work_auth", "bad_fit_domain_mismatch"])

    def test_parse_reason_codes_rejects_unknown_values(self) -> None:
        with self.assertRaises(SystemExit):
            _parse_reason_codes("unknown_reason")

    def test_cli_label_updates_job(self) -> None:
        buffer = io.StringIO()
        with patch.dict("os.environ", {"JOB_HUNTER_DB_PATH": self.db_path}, clear=True):
            with patch("sys.argv", ["label_jobs.py", "label", "--job-id", "1", "--fit-label", "bad_fit", "--reason-codes", "bad_fit_phd_only"]):
                with redirect_stdout(buffer):
                    exit_code = main()

        self.assertEqual(exit_code, 0)
        payload = json.loads(buffer.getvalue().strip())
        self.assertEqual(payload["manual_fit_label"], "bad_fit")
        self.assertEqual(payload["manual_fit_reason_codes"], ["bad_fit_phd_only"])

        row = self.store.get_job_for_labeling(1)
        self.assertIsNotNone(row)
        self.assertEqual(row["manual_fit_label"], "bad_fit")
        self.assertEqual(json.loads(row["manual_fit_reason_codes"]), ["bad_fit_phd_only"])
        self.assertIsNotNone(row["manual_labeled_at"])

    def test_cli_bad_fit_requires_reason_code(self) -> None:
        buffer = io.StringIO()
        with patch.dict("os.environ", {"JOB_HUNTER_DB_PATH": self.db_path}, clear=True):
            with patch("sys.argv", ["label_jobs.py", "label", "--job-id", "1", "--fit-label", "bad_fit"]):
                with redirect_stdout(buffer):
                    exit_code = main()

        self.assertEqual(exit_code, 1)
        self.assertIn("requires at least one reason code", buffer.getvalue())

    def test_cli_stats_reports_labeling_coverage(self) -> None:
        buffer = io.StringIO()
        with patch.dict("os.environ", {"JOB_HUNTER_DB_PATH": self.db_path}, clear=True):
            with patch("sys.argv", ["label_jobs.py", "stats"]):
                with redirect_stdout(buffer):
                    exit_code = main()

        self.assertEqual(exit_code, 0)
        payload = json.loads(buffer.getvalue())
        self.assertEqual(payload["total_jobs"], 1)
        self.assertEqual(payload["unlabeled_jobs"], 1)
        self.assertEqual(payload["by_fit_label"]["unlabeled"], 1)

    def test_cli_export_writes_json_batch_by_default(self) -> None:
        output_path = Path(self.temp_dir.name) / "label-batch.json"
        buffer = io.StringIO()
        with patch.dict("os.environ", {"JOB_HUNTER_DB_PATH": self.db_path}, clear=True):
            with patch("sys.argv", ["label_jobs.py", "export", "--output", str(output_path), "--limit", "5"]):
                with redirect_stdout(buffer):
                    exit_code = main()

        self.assertEqual(exit_code, 0)
        payload = json.loads(buffer.getvalue())
        self.assertEqual(payload["exported_count"], 1)
        self.assertEqual(payload["format"], "json")
        rows = json.loads(output_path.read_text(encoding="utf-8"))
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["job_id"], 1)
        self.assertEqual(row["title"], "Machine Learning Intern")

    def test_cli_export_writes_markdown_batch(self) -> None:
        output_path = Path(self.temp_dir.name) / "label-batch.md"
        buffer = io.StringIO()
        with patch.dict("os.environ", {"JOB_HUNTER_DB_PATH": self.db_path}, clear=True):
            with patch(
                "sys.argv",
                ["label_jobs.py", "export", "--output", str(output_path), "--limit", "5", "--format", "markdown"],
            ):
                with redirect_stdout(buffer):
                    exit_code = main()

        self.assertEqual(exit_code, 0)
        payload = json.loads(buffer.getvalue())
        self.assertEqual(payload["format"], "markdown")
        text = output_path.read_text(encoding="utf-8")
        self.assertIn("# Labeling Batch", text)
        self.assertIn("## [1] Machine Learning Intern", text)


if __name__ == "__main__":
    unittest.main()
