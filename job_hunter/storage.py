from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from pathlib import Path

from job_hunter.models import JobRecord, PipelineOutcome


class JobStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def close(self) -> None:
        self._conn.close()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dedupe_key TEXT NOT NULL UNIQUE,
                source TEXT NOT NULL,
                external_id TEXT,
                url TEXT NOT NULL,
                title TEXT NOT NULL,
                company TEXT NOT NULL,
                location TEXT,
                is_internship INTEGER NOT NULL,
                posted_at TEXT,
                description TEXT,
                work_auth_signals TEXT,
                sponsorship_signals TEXT,
                skills TEXT,
                ingested_at TEXT NOT NULL,
                relevance_score REAL NOT NULL,
                eligibility_confidence REAL NOT NULL,
                eligibility_status TEXT NOT NULL,
                relevance_hits TEXT,
                age_days REAL,
                age_unknown INTEGER NOT NULL DEFAULT 1,
                source_detail TEXT,
                manual_fit_label TEXT,
                manual_fit_reason_codes TEXT,
                manual_labeled_at TEXT,
                notified INTEGER NOT NULL DEFAULT 0,
                notified_at TEXT
            );

            CREATE TABLE IF NOT EXISTS seen_events (
                dedupe_key TEXT PRIMARY KEY,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                seen_count INTEGER NOT NULL DEFAULT 1,
                notified INTEGER NOT NULL DEFAULT 0,
                notified_at TEXT
            );

            CREATE TABLE IF NOT EXISTS run_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                source_count INTEGER NOT NULL,
                passed_filter_count INTEGER NOT NULL,
                persisted_count INTEGER NOT NULL,
                notified_count INTEGER NOT NULL,
                duplicate_count INTEGER NOT NULL,
                error_count INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS source_run_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_log_id INTEGER NOT NULL,
                source_name TEXT NOT NULL,
                fetched_count INTEGER NOT NULL,
                rejected_age_count INTEGER NOT NULL,
                rejected_internship_count INTEGER NOT NULL,
                rejected_us_scope_count INTEGER NOT NULL,
                rejected_title_blacklist_count INTEGER NOT NULL DEFAULT 0,
                rejected_data_role_count INTEGER NOT NULL DEFAULT 0,
                rejected_policy_gate_count INTEGER NOT NULL DEFAULT 0,
                rejected_eligibility_count INTEGER NOT NULL,
                rejected_relevance_count INTEGER NOT NULL,
                persisted_count INTEGER NOT NULL,
                notified_count INTEGER NOT NULL,
                duplicate_count INTEGER NOT NULL,
                error_count INTEGER NOT NULL,
                dead_token_count INTEGER NOT NULL DEFAULT 0,
                feed_error_count INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(run_log_id) REFERENCES run_logs(id)
            );

            CREATE TABLE IF NOT EXISTS source_item_health (
                source_name TEXT NOT NULL,
                item_value TEXT NOT NULL,
                status TEXT NOT NULL,
                consecutive_failures INTEGER NOT NULL DEFAULT 0,
                consecutive_successes INTEGER NOT NULL DEFAULT 0,
                total_failures INTEGER NOT NULL DEFAULT 0,
                total_successes INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                last_checked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (source_name, item_value)
            );
            """
        )
        self._ensure_column("jobs", "age_days", "REAL")
        self._ensure_column("jobs", "age_unknown", "INTEGER NOT NULL DEFAULT 1")
        self._ensure_column("jobs", "source_detail", "TEXT")
        self._ensure_column("jobs", "manual_fit_label", "TEXT")
        self._ensure_column("jobs", "manual_fit_reason_codes", "TEXT")
        self._ensure_column("jobs", "manual_labeled_at", "TEXT")
        self._ensure_column("source_run_logs", "rejected_title_blacklist_count", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("source_run_logs", "rejected_data_role_count", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("source_run_logs", "rejected_policy_gate_count", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("source_run_logs", "dead_token_count", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("source_run_logs", "feed_error_count", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("source_item_health", "consecutive_successes", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("source_item_health", "total_failures", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("source_item_health", "total_successes", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("source_item_health", "last_error", "TEXT")
        self._ensure_column("source_item_health", "last_checked_at", "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP")
        self._conn.commit()

    def _ensure_column(self, table_name: str, column_name: str, column_def: str) -> None:
        rows = self._conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        existing = {str(row["name"]) for row in rows}
        if column_name in existing:
            return
        try:
            self._conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_def}")
        except sqlite3.OperationalError as exc:
            if "duplicate column name" not in str(exc).lower():
                raise

    def is_seen(self, dedupe_key: str) -> bool:
        row = self._conn.execute(
            "SELECT dedupe_key FROM seen_events WHERE dedupe_key = ? LIMIT 1",
            (dedupe_key,),
        ).fetchone()
        return row is not None

    def was_notified(self, dedupe_key: str) -> bool:
        row = self._conn.execute(
            "SELECT notified FROM seen_events WHERE dedupe_key = ? LIMIT 1",
            (dedupe_key,),
        ).fetchone()
        if row is None:
            return False
        return bool(row["notified"])

    def insert_job(self, job: JobRecord, dedupe_key: str) -> bool:
        now_iso = job.ingested_at
        payload = asdict(job)
        try:
            self._conn.execute(
                """
                INSERT INTO jobs (
                    dedupe_key, source, external_id, url, title, company,
                    location, is_internship, posted_at, description,
                    work_auth_signals, sponsorship_signals, skills, ingested_at,
                    relevance_score, eligibility_confidence, eligibility_status,
                    relevance_hits, age_days, age_unknown, source_detail
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    dedupe_key,
                    payload["source"],
                    payload["external_id"],
                    payload["url"],
                    payload["title"],
                    payload["company"],
                    payload["location"],
                    int(payload["is_internship"]),
                    payload["posted_at"],
                    payload["description"],
                    json.dumps(payload["work_auth_signals"]),
                    json.dumps(payload["sponsorship_signals"]),
                    json.dumps(payload["skills"]),
                    payload["ingested_at"],
                    payload["relevance_score"],
                    payload["eligibility_confidence"],
                    payload["eligibility_status"],
                    json.dumps(payload["relevance_hits"]),
                    payload["age_days"],
                    int(payload["age_unknown"]),
                    payload["source_detail"],
                ),
            )
            self._conn.execute(
                """
                INSERT INTO seen_events (dedupe_key, first_seen_at, last_seen_at, seen_count)
                VALUES (?, ?, ?, 1)
                """,
                (dedupe_key, now_iso, now_iso),
            )
            self._conn.commit()
            return True
        except sqlite3.IntegrityError:
            self._conn.execute(
                """
                UPDATE seen_events
                SET last_seen_at = ?, seen_count = seen_count + 1
                WHERE dedupe_key = ?
                """,
                (now_iso, dedupe_key),
            )
            self._conn.commit()
            return False

    def mark_notified(self, dedupe_key: str, notified: bool) -> None:
        if not notified:
            return
        self._conn.execute(
            """
            UPDATE seen_events
            SET notified = 1, notified_at = CURRENT_TIMESTAMP
            WHERE dedupe_key = ?
            """,
            (dedupe_key,),
        )
        self._conn.execute(
            """
            UPDATE jobs
            SET notified = 1, notified_at = CURRENT_TIMESTAMP
            WHERE dedupe_key = ?
            """,
            (dedupe_key,),
        )
        self._conn.commit()

    def log_run(self, outcome: PipelineOutcome) -> None:
        cursor = self._conn.execute(
            """
            INSERT INTO run_logs (
                source_count, passed_filter_count, persisted_count,
                notified_count, duplicate_count, error_count
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                outcome.source_count,
                outcome.passed_filter_count,
                outcome.persisted_count,
                outcome.notified_count,
                outcome.duplicate_count,
                outcome.error_count,
            ),
        )
        run_log_id = cursor.lastrowid
        for source_name, stats in outcome.source_stats.items():
            self._conn.execute(
                """
                INSERT INTO source_run_logs (
                    run_log_id, source_name, fetched_count, rejected_age_count,
                    rejected_internship_count, rejected_us_scope_count, rejected_title_blacklist_count,
                    rejected_data_role_count, rejected_policy_gate_count,
                    rejected_eligibility_count, rejected_relevance_count,
                    persisted_count, notified_count, duplicate_count, error_count,
                    dead_token_count, feed_error_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_log_id,
                    source_name,
                    stats.fetched_count,
                    stats.rejected_age_count,
                    stats.rejected_internship_count,
                    stats.rejected_us_scope_count,
                    stats.rejected_title_blacklist_count,
                    stats.rejected_data_role_count,
                    stats.rejected_policy_gate_count,
                    stats.rejected_eligibility_count,
                    stats.rejected_relevance_count,
                    stats.persisted_count,
                    stats.notified_count,
                    stats.duplicate_count,
                    stats.error_count,
                    stats.dead_token_count,
                    stats.feed_error_count,
                ),
            )
        self._conn.commit()

    def record_source_item_results(self, source_name: str, item_results: list[dict[str, str]]) -> None:
        for result in item_results:
            item_value = str(result.get("item", "")).strip()
            status = str(result.get("status", "")).strip().lower()
            error = str(result.get("error", "")).strip()
            if not item_value or status not in {"success", "failure"}:
                continue

            row = self._conn.execute(
                """
                SELECT consecutive_failures, consecutive_successes, total_failures, total_successes
                FROM source_item_health
                WHERE source_name = ? AND item_value = ?
                """,
                (source_name, item_value),
            ).fetchone()

            if status == "success":
                if row is None:
                    self._conn.execute(
                        """
                        INSERT INTO source_item_health (
                            source_name, item_value, status, consecutive_failures,
                            consecutive_successes, total_failures, total_successes,
                            last_error, last_checked_at
                        ) VALUES (?, ?, 'success', 0, 1, 0, 1, NULL, CURRENT_TIMESTAMP)
                        """,
                        (source_name, item_value),
                    )
                else:
                    self._conn.execute(
                        """
                        UPDATE source_item_health
                        SET status = 'success',
                            consecutive_failures = 0,
                            consecutive_successes = ?,
                            total_successes = ?,
                            last_error = NULL,
                            last_checked_at = CURRENT_TIMESTAMP
                        WHERE source_name = ? AND item_value = ?
                        """,
                        (
                            int(row["consecutive_successes"]) + 1,
                            int(row["total_successes"]) + 1,
                            source_name,
                            item_value,
                        ),
                    )
                continue

            if row is None:
                self._conn.execute(
                    """
                    INSERT INTO source_item_health (
                        source_name, item_value, status, consecutive_failures,
                        consecutive_successes, total_failures, total_successes,
                        last_error, last_checked_at
                    ) VALUES (?, ?, 'failure', 1, 0, 1, 0, ?, CURRENT_TIMESTAMP)
                    """,
                    (source_name, item_value, error or None),
                )
            else:
                self._conn.execute(
                    """
                    UPDATE source_item_health
                    SET status = 'failure',
                        consecutive_failures = ?,
                        consecutive_successes = 0,
                        total_failures = ?,
                        last_error = ?,
                        last_checked_at = CURRENT_TIMESTAMP
                    WHERE source_name = ? AND item_value = ?
                    """,
                    (
                        int(row["consecutive_failures"]) + 1,
                        int(row["total_failures"]) + 1,
                        error or None,
                        source_name,
                        item_value,
                    ),
                )
        self._conn.commit()

    def get_suppressed_items(self, source_name: str, min_failures: int) -> set[str]:
        if min_failures <= 0:
            return set()
        rows = self._conn.execute(
            """
            SELECT item_value
            FROM source_item_health
            WHERE source_name = ? AND status = 'failure' AND consecutive_failures >= ?
            """,
            (source_name, min_failures),
        ).fetchall()
        return {str(row["item_value"]) for row in rows}

    def get_source_item_health(self, source_name: str) -> list[sqlite3.Row]:
        return self._conn.execute(
            """
            SELECT source_name, item_value, status, consecutive_failures, consecutive_successes,
                   total_failures, total_successes, last_error, last_checked_at
            FROM source_item_health
            WHERE source_name = ?
            ORDER BY item_value
            """,
            (source_name,),
        ).fetchall()

    def list_jobs_for_labeling(self, limit: int = 20, unlabeled_only: bool = True) -> list[sqlite3.Row]:
        safe_limit = max(limit, 1)
        if unlabeled_only:
            query = """
                SELECT id, source, company, title, location, posted_at, url,
                       relevance_score, manual_fit_label, manual_fit_reason_codes, manual_labeled_at
                FROM jobs
                WHERE manual_fit_label IS NULL OR TRIM(manual_fit_label) = ''
                ORDER BY ingested_at DESC, id DESC
                LIMIT ?
            """
            return self._conn.execute(query, (safe_limit,)).fetchall()

        query = """
            SELECT id, source, company, title, location, posted_at, url,
                   relevance_score, manual_fit_label, manual_fit_reason_codes, manual_labeled_at
            FROM jobs
            ORDER BY ingested_at DESC, id DESC
            LIMIT ?
        """
        return self._conn.execute(query, (safe_limit,)).fetchall()

    def list_jobs_for_export(
        self,
        limit: int = 50,
        unlabeled_only: bool = True,
        source: str | None = None,
    ) -> list[sqlite3.Row]:
        safe_limit = max(limit, 1)
        clauses: list[str] = []
        params: list[object] = []
        if unlabeled_only:
            clauses.append("(manual_fit_label IS NULL OR TRIM(manual_fit_label) = '')")
        if source:
            clauses.append("source = ?")
            params.append(source)

        where_sql = ""
        if clauses:
            where_sql = "WHERE " + " AND ".join(clauses)

        query = f"""
            SELECT id, source, company, title, location, posted_at, url, description,
                   relevance_score, eligibility_status, eligibility_confidence,
                   manual_fit_label, manual_fit_reason_codes, manual_labeled_at
            FROM jobs
            {where_sql}
            ORDER BY ingested_at DESC, id DESC
            LIMIT ?
        """
        params.append(safe_limit)
        return self._conn.execute(query, tuple(params)).fetchall()

    def get_job_for_labeling(self, job_id: int) -> sqlite3.Row | None:
        return self._conn.execute(
            """
            SELECT id, source, company, title, location, posted_at, url, description,
                   relevance_score, manual_fit_label, manual_fit_reason_codes, manual_labeled_at
            FROM jobs
            WHERE id = ?
            LIMIT 1
            """,
            (job_id,),
        ).fetchone()

    def set_manual_fit_label(self, job_id: int, label: str, reason_codes: list[str]) -> bool:
        cursor = self._conn.execute(
            """
            UPDATE jobs
            SET manual_fit_label = ?, manual_fit_reason_codes = ?, manual_labeled_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (label, json.dumps(reason_codes), job_id),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def get_labeling_stats(self) -> dict[str, object]:
        totals = self._conn.execute(
            """
            SELECT
                COUNT(*) AS total_jobs,
                SUM(CASE WHEN manual_fit_label IS NOT NULL AND TRIM(manual_fit_label) <> '' THEN 1 ELSE 0 END) AS labeled_jobs,
                SUM(CASE WHEN manual_fit_label IS NULL OR TRIM(manual_fit_label) = '' THEN 1 ELSE 0 END) AS unlabeled_jobs
            FROM jobs
            """
        ).fetchone()
        by_label_rows = self._conn.execute(
            """
            SELECT COALESCE(NULLIF(TRIM(manual_fit_label), ''), 'unlabeled') AS fit_label, COUNT(*) AS count
            FROM jobs
            GROUP BY COALESCE(NULLIF(TRIM(manual_fit_label), ''), 'unlabeled')
            ORDER BY count DESC, fit_label
            """
        ).fetchall()
        by_source_rows = self._conn.execute(
            """
            SELECT source,
                   COUNT(*) AS total_jobs,
                   SUM(CASE WHEN manual_fit_label IS NOT NULL AND TRIM(manual_fit_label) <> '' THEN 1 ELSE 0 END) AS labeled_jobs,
                   SUM(CASE WHEN manual_fit_label IS NULL OR TRIM(manual_fit_label) = '' THEN 1 ELSE 0 END) AS unlabeled_jobs
            FROM jobs
            GROUP BY source
            ORDER BY total_jobs DESC, source
            """
        ).fetchall()
        return {
            "total_jobs": int(totals["total_jobs"] or 0),
            "labeled_jobs": int(totals["labeled_jobs"] or 0),
            "unlabeled_jobs": int(totals["unlabeled_jobs"] or 0),
            "by_fit_label": {str(row["fit_label"]): int(row["count"] or 0) for row in by_label_rows},
            "by_source": [
                {
                    "source": str(row["source"]),
                    "total_jobs": int(row["total_jobs"] or 0),
                    "labeled_jobs": int(row["labeled_jobs"] or 0),
                    "unlabeled_jobs": int(row["unlabeled_jobs"] or 0),
                }
                for row in by_source_rows
            ],
        }


def ensure_parent_dir(db_path: str) -> None:
    path = Path(db_path)
    if path.parent and str(path.parent) != ".":
        path.parent.mkdir(parents=True, exist_ok=True)
