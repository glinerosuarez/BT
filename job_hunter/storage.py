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
                compensation_type TEXT NOT NULL DEFAULT 'unknown',
                work_auth_signals TEXT,
                sponsorship_signals TEXT,
                skills TEXT,
                ingested_at TEXT NOT NULL,
                relevance_score REAL NOT NULL,
                eligibility_confidence REAL NOT NULL,
                eligibility_status TEXT NOT NULL,
                relevance_hits TEXT,
                role_relevance_label TEXT,
                role_relevance_reason_codes TEXT,
                policy_gate_status TEXT,
                policy_gate_reason_codes TEXT,
                profile_match_score REAL NOT NULL DEFAULT 0.0,
                profile_match_label TEXT,
                profile_match_reason_codes TEXT,
                profile_version TEXT,
                scorer_version TEXT,
                job_text_version TEXT,
                job_text_snapshot TEXT,
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
                normalized_count INTEGER NOT NULL DEFAULT 0,
                rejected_missing_core_fields_count INTEGER NOT NULL DEFAULT 0,
                after_stage_1a_count INTEGER NOT NULL DEFAULT 0,
                after_stage_1b_count INTEGER NOT NULL DEFAULT 0,
                after_stage_1c_count INTEGER NOT NULL DEFAULT 0,
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
                normalized_count INTEGER NOT NULL DEFAULT 0,
                rejected_missing_core_fields_count INTEGER NOT NULL DEFAULT 0,
                rejected_age_count INTEGER NOT NULL,
                after_stage_1a_count INTEGER NOT NULL DEFAULT 0,
                rejected_internship_count INTEGER NOT NULL,
                rejected_us_scope_count INTEGER NOT NULL,
                rejected_title_blacklist_count INTEGER NOT NULL DEFAULT 0,
                rejected_data_role_count INTEGER NOT NULL DEFAULT 0,
                after_stage_1b_count INTEGER NOT NULL DEFAULT 0,
                rejected_policy_gate_count INTEGER NOT NULL DEFAULT 0,
                after_stage_1c_count INTEGER NOT NULL DEFAULT 0,
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
        self._ensure_column("jobs", "compensation_type", "TEXT NOT NULL DEFAULT 'unknown'")
        self._ensure_column("jobs", "role_relevance_label", "TEXT")
        self._ensure_column("jobs", "role_relevance_reason_codes", "TEXT")
        self._ensure_column("jobs", "policy_gate_status", "TEXT")
        self._ensure_column("jobs", "policy_gate_reason_codes", "TEXT")
        self._ensure_column("jobs", "profile_match_score", "REAL NOT NULL DEFAULT 0.0")
        self._ensure_column("jobs", "profile_match_label", "TEXT")
        self._ensure_column("jobs", "profile_match_reason_codes", "TEXT")
        self._ensure_column("jobs", "profile_version", "TEXT")
        self._ensure_column("jobs", "scorer_version", "TEXT")
        self._ensure_column("jobs", "job_text_version", "TEXT")
        self._ensure_column("jobs", "job_text_snapshot", "TEXT")
        self._ensure_column("run_logs", "normalized_count", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("run_logs", "rejected_missing_core_fields_count", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("run_logs", "after_stage_1a_count", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("run_logs", "after_stage_1b_count", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("run_logs", "after_stage_1c_count", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("source_run_logs", "normalized_count", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("source_run_logs", "rejected_missing_core_fields_count", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("source_run_logs", "after_stage_1a_count", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("source_run_logs", "rejected_title_blacklist_count", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("source_run_logs", "rejected_data_role_count", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("source_run_logs", "after_stage_1b_count", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("source_run_logs", "rejected_policy_gate_count", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("source_run_logs", "after_stage_1c_count", "INTEGER NOT NULL DEFAULT 0")
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
                    compensation_type,
                    work_auth_signals, sponsorship_signals, skills, ingested_at,
                    relevance_score, eligibility_confidence, eligibility_status,
                    relevance_hits, role_relevance_label, role_relevance_reason_codes,
                    policy_gate_status, policy_gate_reason_codes, profile_match_score,
                    profile_match_label, profile_match_reason_codes, profile_version,
                    scorer_version, job_text_version, job_text_snapshot,
                    age_days, age_unknown, source_detail
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    payload["compensation_type"],
                    json.dumps(payload["work_auth_signals"]),
                    json.dumps(payload["sponsorship_signals"]),
                    json.dumps(payload["skills"]),
                    payload["ingested_at"],
                    payload["relevance_score"],
                    payload["eligibility_confidence"],
                    payload["eligibility_status"],
                    json.dumps(payload["relevance_hits"]),
                    payload["role_relevance_label"],
                    json.dumps(payload["role_relevance_reason_codes"]),
                    payload["policy_gate_status"],
                    json.dumps(payload["policy_gate_reason_codes"]),
                    payload["profile_match_score"],
                    payload["profile_match_label"],
                    json.dumps(payload["profile_match_reason_codes"]),
                    payload["profile_version"],
                    payload["scorer_version"],
                    payload["job_text_version"],
                    payload["job_text_snapshot"],
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
            self.update_existing_job(job, dedupe_key)
            self._conn.commit()
            return False

    def update_existing_job(self, job: JobRecord, dedupe_key: str) -> None:
        row = self._conn.execute(
            """
            SELECT description, url, source_detail
            , job_text_snapshot
            FROM jobs
            WHERE dedupe_key = ?
            LIMIT 1
            """,
            (dedupe_key,),
        ).fetchone()
        if row is None:
            return

        existing_description = str(row["description"] or "")
        new_description = job.description or ""
        description = existing_description
        if len(new_description.strip()) > len(existing_description.strip()):
            description = new_description

        existing_url = str(row["url"] or "")
        url = existing_url
        if job.url and "#jobhunter-" not in job.url:
            url = job.url
        elif not existing_url:
            url = job.url

        source_detail = str(row["source_detail"] or "")
        if job.source_detail:
            source_detail = job.source_detail
        existing_job_text_snapshot = str(row["job_text_snapshot"] or "")
        job_text_snapshot = existing_job_text_snapshot
        if job.job_text_snapshot:
            job_text_snapshot = job.job_text_snapshot

        self._conn.execute(
            """
            UPDATE jobs
            SET url = ?,
                description = ?,
                compensation_type = ?,
                location = ?,
                posted_at = ?,
                work_auth_signals = ?,
                sponsorship_signals = ?,
                relevance_score = ?,
                eligibility_confidence = ?,
                eligibility_status = ?,
                relevance_hits = ?,
                role_relevance_label = ?,
                role_relevance_reason_codes = ?,
                policy_gate_status = ?,
                policy_gate_reason_codes = ?,
                profile_match_score = ?,
                profile_match_label = ?,
                profile_match_reason_codes = ?,
                profile_version = ?,
                scorer_version = ?,
                job_text_version = ?,
                job_text_snapshot = ?,
                age_days = ?,
                age_unknown = ?,
                source_detail = ?
            WHERE dedupe_key = ?
            """,
            (
                url,
                description,
                job.compensation_type,
                job.location,
                job.posted_at,
                json.dumps(job.work_auth_signals),
                json.dumps(job.sponsorship_signals),
                job.relevance_score,
                job.eligibility_confidence,
                job.eligibility_status,
                json.dumps(job.relevance_hits),
                job.role_relevance_label,
                json.dumps(job.role_relevance_reason_codes),
                job.policy_gate_status,
                json.dumps(job.policy_gate_reason_codes),
                job.profile_match_score,
                job.profile_match_label,
                json.dumps(job.profile_match_reason_codes),
                job.profile_version,
                job.scorer_version,
                job.job_text_version,
                job_text_snapshot,
                job.age_days,
                int(job.age_unknown),
                source_detail,
                dedupe_key,
            ),
        )

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
                source_count, normalized_count, rejected_missing_core_fields_count,
                after_stage_1a_count, after_stage_1b_count, after_stage_1c_count,
                passed_filter_count, persisted_count,
                notified_count, duplicate_count, error_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                outcome.source_count,
                outcome.normalized_count,
                outcome.rejected_missing_core_fields_count,
                outcome.after_stage_1a_count,
                outcome.after_stage_1b_count,
                outcome.after_stage_1c_count,
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
                    run_log_id, source_name, fetched_count, normalized_count,
                    rejected_missing_core_fields_count, rejected_age_count,
                    after_stage_1a_count,
                    rejected_internship_count, rejected_us_scope_count, rejected_title_blacklist_count,
                    rejected_data_role_count, after_stage_1b_count, rejected_policy_gate_count,
                    after_stage_1c_count,
                    rejected_eligibility_count, rejected_relevance_count,
                    persisted_count, notified_count, duplicate_count, error_count,
                    dead_token_count, feed_error_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_log_id,
                    source_name,
                    stats.fetched_count,
                    stats.normalized_count,
                    stats.rejected_missing_core_fields_count,
                    stats.rejected_age_count,
                    stats.after_stage_1a_count,
                    stats.rejected_internship_count,
                    stats.rejected_us_scope_count,
                    stats.rejected_title_blacklist_count,
                    stats.rejected_data_role_count,
                    stats.after_stage_1b_count,
                    stats.rejected_policy_gate_count,
                    stats.after_stage_1c_count,
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
                   relevance_score, eligibility_status, eligibility_confidence, compensation_type,
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
                   relevance_score, compensation_type, manual_fit_label, manual_fit_reason_codes, manual_labeled_at
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

    def get_latest_run_report(self) -> dict[str, object] | None:
        run_row = self._conn.execute(
            """
            SELECT id, run_at, source_count, normalized_count, rejected_missing_core_fields_count,
                   after_stage_1a_count, after_stage_1b_count, after_stage_1c_count,
                   passed_filter_count, persisted_count, notified_count, duplicate_count, error_count
            FROM run_logs
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        if run_row is None:
            return None

        source_rows = self._conn.execute(
            """
            SELECT source_name, fetched_count, normalized_count, rejected_missing_core_fields_count,
                   rejected_age_count, after_stage_1a_count, rejected_internship_count,
                   rejected_us_scope_count, rejected_title_blacklist_count, rejected_data_role_count,
                   after_stage_1b_count, rejected_policy_gate_count, after_stage_1c_count,
                   rejected_eligibility_count, rejected_relevance_count, persisted_count,
                   notified_count, duplicate_count, error_count, dead_token_count, feed_error_count
            FROM source_run_logs
            WHERE run_log_id = ?
            ORDER BY source_name
            """,
            (int(run_row["id"]),),
        ).fetchall()

        return {
            "run_id": int(run_row["id"]),
            "run_at": str(run_row["run_at"]),
            "source_count": int(run_row["source_count"] or 0),
            "normalized_count": int(run_row["normalized_count"] or 0),
            "rejected_missing_core_fields_count": int(run_row["rejected_missing_core_fields_count"] or 0),
            "after_stage_1a_count": int(run_row["after_stage_1a_count"] or 0),
            "after_stage_1b_count": int(run_row["after_stage_1b_count"] or 0),
            "after_stage_1c_count": int(run_row["after_stage_1c_count"] or 0),
            "passed_filter_count": int(run_row["passed_filter_count"] or 0),
            "persisted_count": int(run_row["persisted_count"] or 0),
            "notified_count": int(run_row["notified_count"] or 0),
            "duplicate_count": int(run_row["duplicate_count"] or 0),
            "error_count": int(run_row["error_count"] or 0),
            "source_stats": [
                {
                    "source_name": str(row["source_name"]),
                    "fetched_count": int(row["fetched_count"] or 0),
                    "normalized_count": int(row["normalized_count"] or 0),
                    "rejected_missing_core_fields_count": int(row["rejected_missing_core_fields_count"] or 0),
                    "rejected_age_count": int(row["rejected_age_count"] or 0),
                    "after_stage_1a_count": int(row["after_stage_1a_count"] or 0),
                    "rejected_internship_count": int(row["rejected_internship_count"] or 0),
                    "rejected_us_scope_count": int(row["rejected_us_scope_count"] or 0),
                    "rejected_title_blacklist_count": int(row["rejected_title_blacklist_count"] or 0),
                    "rejected_data_role_count": int(row["rejected_data_role_count"] or 0),
                    "after_stage_1b_count": int(row["after_stage_1b_count"] or 0),
                    "rejected_policy_gate_count": int(row["rejected_policy_gate_count"] or 0),
                    "after_stage_1c_count": int(row["after_stage_1c_count"] or 0),
                    "rejected_eligibility_count": int(row["rejected_eligibility_count"] or 0),
                    "rejected_relevance_count": int(row["rejected_relevance_count"] or 0),
                    "persisted_count": int(row["persisted_count"] or 0),
                    "notified_count": int(row["notified_count"] or 0),
                    "duplicate_count": int(row["duplicate_count"] or 0),
                    "error_count": int(row["error_count"] or 0),
                    "dead_token_count": int(row["dead_token_count"] or 0),
                    "feed_error_count": int(row["feed_error_count"] or 0),
                }
                for row in source_rows
            ],
        }

    def list_stage2_jobs(self, limit: int = 20, label: str | None = None, source: str | None = None) -> list[sqlite3.Row]:
        safe_limit = max(limit, 1)
        clauses = ["job_text_version IS NOT NULL", "TRIM(job_text_version) <> ''"]
        params: list[object] = []
        if label:
            clauses.append("profile_match_label = ?")
            params.append(label)
        if source:
            clauses.append("source = ?")
            params.append(source)
        where_sql = " AND ".join(clauses)
        query = f"""
            SELECT id, source, company, title, location, posted_at, compensation_type,
                   profile_match_score, profile_match_label, profile_match_reason_codes,
                   profile_version, scorer_version, job_text_version
            FROM jobs
            WHERE {where_sql}
            ORDER BY ingested_at DESC, id DESC
            LIMIT ?
        """
        params.append(safe_limit)
        return self._conn.execute(query, tuple(params)).fetchall()

    def get_stage2_job(self, job_id: int) -> sqlite3.Row | None:
        return self._conn.execute(
            """
            SELECT id, source, company, title, location, posted_at, url, compensation_type,
                   relevance_score, eligibility_status, eligibility_confidence,
                   profile_match_score, profile_match_label, profile_match_reason_codes,
                   profile_version, scorer_version, job_text_version, job_text_snapshot,
                   manual_fit_label, manual_fit_reason_codes
            FROM jobs
            WHERE id = ?
            LIMIT 1
            """,
            (job_id,),
        ).fetchone()

    def list_stage2_labeled_jobs(self, limit: int = 200) -> list[sqlite3.Row]:
        safe_limit = max(limit, 1)
        return self._conn.execute(
            """
            SELECT id, source, company, title, location, posted_at, url, compensation_type,
                   eligibility_status, eligibility_confidence, relevance_score,
                   profile_match_score, profile_match_label, profile_match_reason_codes,
                   profile_version, scorer_version, job_text_version, job_text_snapshot,
                   manual_fit_label, manual_fit_reason_codes
            FROM jobs
            WHERE manual_fit_label IS NOT NULL
              AND TRIM(manual_fit_label) <> ''
              AND job_text_version IS NOT NULL
              AND TRIM(job_text_version) <> ''
            ORDER BY manual_labeled_at DESC, id DESC
            LIMIT ?
            """,
            (safe_limit,),
        ).fetchall()


def ensure_parent_dir(db_path: str) -> None:
    path = Path(db_path)
    if path.parent and str(path.parent) != ".":
        path.parent.mkdir(parents=True, exist_ok=True)
