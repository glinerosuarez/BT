from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from job_hunter.models import JobRecord, PipelineOutcome

SUMMARY_BETA_MARKER = "summary beta"
SOURCE_QUALITY_QUARANTINE_STATUSES = {"card_only", "detail_polluted", "detail_mismatch"}
HANDSHAKE_PAGE_CHROME_MARKERS = (
    "skip to content",
    "career center",
    "get the app",
    "ai showcase",
)
HANDSHAKE_QUALITY_STATUS_SCORES = {
    "detail_complete": 5,
    "detail_partial": 4,
    "card_only": 3,
    "detail_mismatch": 2,
    "detail_polluted": 1,
}


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
                semantic_match_score REAL NOT NULL DEFAULT 0.0,
                semantic_match_label TEXT,
                semantic_match_reason_codes TEXT,
                semantic_base_score REAL NOT NULL DEFAULT 0.0,
                semantic_research_heaviness_score REAL NOT NULL DEFAULT 0.0,
                semantic_adjustment_reason_codes TEXT,
                semantic_profile_id TEXT,
                semantic_model_name TEXT,
                semantic_scorer_version TEXT,
                semantic_text_hash TEXT,
                age_days REAL,
                age_unknown INTEGER NOT NULL DEFAULT 1,
                source_detail TEXT,
                source_metadata TEXT,
                source_quality_status TEXT,
                source_quality_reason_codes TEXT,
                source_quality_prev_status TEXT,
                source_quality_recovered_at TEXT,
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
                rejected_source_quality_count INTEGER NOT NULL DEFAULT 0,
                recovered_source_quality_count INTEGER NOT NULL DEFAULT 0,
                persisted_count INTEGER NOT NULL,
                notified_count INTEGER NOT NULL,
                duplicate_count INTEGER NOT NULL,
                error_count INTEGER NOT NULL,
                dead_token_count INTEGER NOT NULL DEFAULT 0,
                feed_error_count INTEGER NOT NULL DEFAULT 0,
                security_verification_blocked_count INTEGER NOT NULL DEFAULT 0,
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

            CREATE TABLE IF NOT EXISTS tailoring_artifacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                profile_name TEXT NOT NULL,
                provider_name TEXT NOT NULL,
                model_name TEXT NOT NULL,
                prompt_version TEXT NOT NULL,
                resume_source_hash TEXT NOT NULL,
                cover_letter_source_hash TEXT NOT NULL,
                preferences_source_hash TEXT NOT NULL,
                job_context_hash TEXT NOT NULL,
                resume_markdown TEXT NOT NULL,
                cover_letter_markdown TEXT NOT NULL,
                highlight_requirements TEXT NOT NULL,
                evidence_map TEXT NOT NULL,
                output_dir TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (
                    job_id,
                    profile_name,
                    prompt_version,
                    resume_source_hash,
                    cover_letter_source_hash,
                    preferences_source_hash,
                    job_context_hash
                ),
                FOREIGN KEY(job_id) REFERENCES jobs(id)
            );

            CREATE TABLE IF NOT EXISTS application_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                profile_name TEXT NOT NULL,
                tailoring_artifact_id INTEGER,
                adapter_name TEXT NOT NULL,
                source TEXT NOT NULL,
                target_url TEXT NOT NULL,
                current_url TEXT,
                status TEXT NOT NULL,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                blocked_reason TEXT,
                blocked_payload TEXT,
                confirmation_payload TEXT,
                output_dir TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                submitted_at TEXT,
                FOREIGN KEY(job_id) REFERENCES jobs(id),
                FOREIGN KEY(tailoring_artifact_id) REFERENCES tailoring_artifacts(id)
            );

            CREATE TABLE IF NOT EXISTS application_steps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                application_run_id INTEGER NOT NULL,
                step_key TEXT NOT NULL,
                step_label TEXT NOT NULL,
                status TEXT NOT NULL,
                field_name TEXT,
                field_type TEXT,
                question_text TEXT,
                answer_source TEXT,
                answer_value TEXT,
                screenshot_path TEXT,
                payload_json TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(application_run_id) REFERENCES application_runs(id)
            );
            """
        )
        self._ensure_column("jobs", "age_days", "REAL")
        self._ensure_column("jobs", "age_unknown", "INTEGER NOT NULL DEFAULT 1")
        self._ensure_column("jobs", "source_detail", "TEXT")
        self._ensure_column("jobs", "source_metadata", "TEXT")
        self._ensure_column("jobs", "source_quality_status", "TEXT")
        self._ensure_column("jobs", "source_quality_reason_codes", "TEXT")
        self._ensure_column("jobs", "source_quality_prev_status", "TEXT")
        self._ensure_column("jobs", "source_quality_recovered_at", "TEXT")
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
        self._ensure_column("jobs", "semantic_match_score", "REAL NOT NULL DEFAULT 0.0")
        self._ensure_column("jobs", "semantic_match_label", "TEXT")
        self._ensure_column("jobs", "semantic_match_reason_codes", "TEXT")
        self._ensure_column("jobs", "semantic_base_score", "REAL NOT NULL DEFAULT 0.0")
        self._ensure_column("jobs", "semantic_research_heaviness_score", "REAL NOT NULL DEFAULT 0.0")
        self._ensure_column("jobs", "semantic_adjustment_reason_codes", "TEXT")
        self._ensure_column("jobs", "semantic_profile_id", "TEXT")
        self._ensure_column("jobs", "semantic_model_name", "TEXT")
        self._ensure_column("jobs", "semantic_scorer_version", "TEXT")
        self._ensure_column("jobs", "semantic_text_hash", "TEXT")
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
        self._ensure_column("source_run_logs", "security_verification_blocked_count", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("source_run_logs", "rejected_source_quality_count", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("source_run_logs", "recovered_source_quality_count", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("source_item_health", "consecutive_successes", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("source_item_health", "total_failures", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("source_item_health", "total_successes", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("source_item_health", "last_error", "TEXT")
        self._ensure_column("source_item_health", "last_checked_at", "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP")
        self._ensure_column("application_runs", "tailoring_artifact_id", "INTEGER")
        self._ensure_column("application_runs", "current_url", "TEXT")
        self._ensure_column("application_runs", "attempt_count", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("application_runs", "blocked_reason", "TEXT")
        self._ensure_column("application_runs", "blocked_payload", "TEXT")
        self._ensure_column("application_runs", "confirmation_payload", "TEXT")
        self._ensure_column("application_runs", "submitted_at", "TEXT")
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

    def resolve_existing_dedupe_key(self, *, source: str, dedupe_key: str, url: str) -> str:
        row = self._conn.execute(
            "SELECT dedupe_key FROM seen_events WHERE dedupe_key = ? LIMIT 1",
            (dedupe_key,),
        ).fetchone()
        if row is not None:
            return str(row["dedupe_key"])
        if source == "handshake" and url.strip():
            normalized_url = _normalize_handshake_storage_url(url)
            rows = self._conn.execute(
                """
                SELECT dedupe_key, url
                FROM jobs
                WHERE source = 'handshake'
                ORDER BY id DESC
                """
            ).fetchall()
            for candidate in rows:
                candidate_url = str(candidate["url"] or "").strip()
                if _normalize_handshake_storage_url(candidate_url) == normalized_url:
                    return str(candidate["dedupe_key"])
        return ""

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
                    semantic_match_score, semantic_match_label, semantic_match_reason_codes,
                    semantic_base_score, semantic_research_heaviness_score, semantic_adjustment_reason_codes,
                    semantic_profile_id, semantic_model_name, semantic_scorer_version,
                    semantic_text_hash, age_days, age_unknown, source_detail,
                    source_metadata, source_quality_status, source_quality_reason_codes,
                    source_quality_prev_status, source_quality_recovered_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    payload["semantic_match_score"],
                    payload["semantic_match_label"],
                    json.dumps(payload["semantic_match_reason_codes"]),
                    payload["semantic_base_score"],
                    payload["semantic_research_heaviness_score"],
                    json.dumps(payload["semantic_adjustment_reason_codes"]),
                    payload["semantic_profile_id"],
                    payload["semantic_model_name"],
                    payload["semantic_scorer_version"],
                    payload["semantic_text_hash"],
                    payload["age_days"],
                    int(payload["age_unknown"]),
                    payload["source_detail"],
                    json.dumps(payload["source_metadata"]),
                    payload["source_quality_status"],
                    json.dumps(payload["source_quality_reason_codes"]),
                    payload["source_quality_prev_status"],
                    payload["source_quality_recovered_at"],
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

    def update_existing_job(self, job: JobRecord, dedupe_key: str) -> dict[str, object]:
        row = self._conn.execute(
            """
            SELECT description, url, source_detail, source_metadata, source_quality_status, source_quality_reason_codes,
                   source_quality_prev_status, source_quality_recovered_at, job_text_snapshot
            FROM jobs
            WHERE dedupe_key = ?
            LIMIT 1
            """,
            (dedupe_key,),
        ).fetchone()
        if row is None:
            return {"source_quality_recovered": False}

        existing_description = str(row["description"] or "")
        new_description = job.description or ""
        description = existing_description
        existing_has_summary_beta = SUMMARY_BETA_MARKER in existing_description.lower()
        new_has_summary_beta = SUMMARY_BETA_MARKER in new_description.lower()
        existing_has_page_chrome = _has_handshake_page_chrome(existing_description)
        new_has_page_chrome = _has_handshake_page_chrome(new_description)
        if existing_has_summary_beta and not new_has_summary_beta and new_description.strip():
            description = new_description
        elif job.source == "handshake" and existing_has_page_chrome and not new_has_page_chrome and new_description.strip():
            description = new_description
        elif len(new_description.strip()) > len(existing_description.strip()):
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
        source_metadata = str(row["source_metadata"] or "")
        if job.source_metadata:
            source_metadata = json.dumps(job.source_metadata)
        existing_source_quality_status = str(row["source_quality_status"] or "")
        source_quality_status = existing_source_quality_status
        if job.source_quality_status:
            source_quality_status = job.source_quality_status
        source_quality_reason_codes = str(row["source_quality_reason_codes"] or "")
        if job.source_quality_reason_codes:
            source_quality_reason_codes = json.dumps(job.source_quality_reason_codes)
        source_quality_prev_status = str(row["source_quality_prev_status"] or "")
        source_quality_recovered_at = str(row["source_quality_recovered_at"] or "")
        source_quality_recovered = _source_quality_recovered(
            previous_status=existing_source_quality_status,
            current_status=source_quality_status,
        )
        if source_quality_recovered:
            source_quality_prev_status = existing_source_quality_status
            source_quality_recovered_at = job.ingested_at
        existing_job_text_snapshot = str(row["job_text_snapshot"] or "")
        job_text_snapshot = existing_job_text_snapshot
        description_replaced = description != existing_description
        snapshot_has_summary_beta = SUMMARY_BETA_MARKER in existing_job_text_snapshot.lower()
        if job.job_text_snapshot:
            job_text_snapshot = job.job_text_snapshot
        elif description_replaced or snapshot_has_summary_beta:
            from job_hunter.stage2 import build_job_text_v1

            refreshed_job = JobRecord(
                source=job.source,
                external_id=job.external_id,
                url=url,
                title=job.title,
                company=job.company,
                location=job.location,
                is_internship=job.is_internship,
                posted_at=job.posted_at,
                description=description,
                compensation_type=job.compensation_type,
                work_auth_signals=job.work_auth_signals,
                sponsorship_signals=job.sponsorship_signals,
                skills=job.skills,
                ingested_at=job.ingested_at,
            )
            job_text_snapshot = build_job_text_v1(refreshed_job)

        self._conn.execute(
            """
            UPDATE jobs
            SET external_id = ?,
                url = ?,
                title = ?,
                company = ?,
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
                semantic_match_score = ?,
                semantic_match_label = ?,
                semantic_match_reason_codes = ?,
                semantic_base_score = ?,
                semantic_research_heaviness_score = ?,
                semantic_adjustment_reason_codes = ?,
                semantic_profile_id = ?,
                semantic_model_name = ?,
                semantic_scorer_version = ?,
                semantic_text_hash = ?,
                age_days = ?,
                age_unknown = ?,
                source_detail = ?,
                source_metadata = ?,
                source_quality_status = ?,
                source_quality_reason_codes = ?,
                source_quality_prev_status = ?,
                source_quality_recovered_at = ?
            WHERE dedupe_key = ?
            """,
            (
                job.external_id,
                url,
                job.title,
                job.company,
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
                job.semantic_match_score,
                job.semantic_match_label,
                json.dumps(job.semantic_match_reason_codes),
                job.semantic_base_score,
                job.semantic_research_heaviness_score,
                json.dumps(job.semantic_adjustment_reason_codes),
                job.semantic_profile_id,
                job.semantic_model_name,
                job.semantic_scorer_version,
                job.semantic_text_hash,
                job.age_days,
                int(job.age_unknown),
                source_detail,
                source_metadata,
                source_quality_status,
                source_quality_reason_codes,
                source_quality_prev_status,
                source_quality_recovered_at,
                dedupe_key,
            ),
        )
        self._conn.commit()
        return {
            "source_quality_recovered": source_quality_recovered,
            "previous_source_quality_status": existing_source_quality_status,
            "current_source_quality_status": source_quality_status,
        }

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
                    rejected_eligibility_count, rejected_relevance_count, rejected_source_quality_count,
                    recovered_source_quality_count,
                    persisted_count, notified_count, duplicate_count, error_count,
                    dead_token_count, feed_error_count, security_verification_blocked_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    stats.rejected_source_quality_count,
                    stats.recovered_source_quality_count,
                    stats.persisted_count,
                    stats.notified_count,
                    stats.duplicate_count,
                    stats.error_count,
                    stats.dead_token_count,
                    stats.feed_error_count,
                    stats.security_verification_blocked_count,
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

    def cleanup_handshake_duplicate_rows(self) -> dict[str, int]:
        rows = self._conn.execute(
            """
            SELECT id, dedupe_key, url, title, company, description, source_quality_status,
                   source_quality_prev_status, source_quality_recovered_at, manual_fit_label,
                   manual_fit_reason_codes, manual_labeled_at, notified, notified_at,
                   profile_match_score, profile_match_label, profile_match_reason_codes,
                   profile_version, scorer_version, job_text_version, job_text_snapshot,
                   semantic_match_score, semantic_match_label, semantic_match_reason_codes,
                   semantic_base_score, semantic_research_heaviness_score, semantic_adjustment_reason_codes,
                   semantic_profile_id, semantic_model_name, semantic_scorer_version,
                   semantic_text_hash
            FROM jobs
            WHERE source = 'handshake' AND url IS NOT NULL AND TRIM(url) <> ''
            ORDER BY url, id
            """
        ).fetchall()
        by_url: dict[str, list[sqlite3.Row]] = {}
        for row in rows:
            normalized_url = _normalize_handshake_storage_url(str(row["url"] or "").strip())
            by_url.setdefault(normalized_url, []).append(row)

        deleted_count = 0
        for url_rows in by_url.values():
            if len(url_rows) <= 1:
                continue
            canonical = max(url_rows, key=_handshake_row_rank)
            duplicates = [row for row in url_rows if int(row["id"]) != int(canonical["id"])]
            if not duplicates:
                continue
            self._merge_handshake_duplicate_metadata(canonical, duplicates)
            self._merge_handshake_duplicate_seen_events(canonical, duplicates)
            self._conn.executemany("DELETE FROM jobs WHERE id = ?", [(int(row["id"]),) for row in duplicates])
            self._conn.executemany(
                "DELETE FROM seen_events WHERE dedupe_key = ?",
                [(str(row["dedupe_key"]),) for row in duplicates],
            )
            deleted_count += len(duplicates)
        self._conn.commit()
        return {"deleted_count": deleted_count}

    def _merge_handshake_duplicate_metadata(self, canonical: sqlite3.Row, duplicates: list[sqlite3.Row]) -> None:
        canonical_manual_fit_label = str(canonical["manual_fit_label"] or "")
        canonical_manual_fit_reason_codes = str(canonical["manual_fit_reason_codes"] or "")
        canonical_manual_labeled_at = str(canonical["manual_labeled_at"] or "")
        canonical_notified = int(canonical["notified"] or 0)
        canonical_notified_at = str(canonical["notified_at"] or "")
        canonical_prev_status = str(canonical["source_quality_prev_status"] or "")
        canonical_recovered_at = str(canonical["source_quality_recovered_at"] or "")
        canonical_profile_match_score = float(canonical["profile_match_score"] or 0.0)
        canonical_profile_match_label = str(canonical["profile_match_label"] or "")
        canonical_profile_match_reason_codes = str(canonical["profile_match_reason_codes"] or "")
        canonical_profile_version = str(canonical["profile_version"] or "")
        canonical_scorer_version = str(canonical["scorer_version"] or "")
        canonical_job_text_version = str(canonical["job_text_version"] or "")
        canonical_snapshot = str(canonical["job_text_snapshot"] or "")
        canonical_semantic_match_score = float(canonical["semantic_match_score"] or 0.0)
        canonical_semantic_match_label = str(canonical["semantic_match_label"] or "")
        canonical_semantic_match_reason_codes = str(canonical["semantic_match_reason_codes"] or "")
        canonical_semantic_base_score = float(canonical["semantic_base_score"] or 0.0)
        canonical_semantic_research_heaviness_score = float(canonical["semantic_research_heaviness_score"] or 0.0)
        canonical_semantic_adjustment_reason_codes = str(canonical["semantic_adjustment_reason_codes"] or "")
        canonical_semantic_profile_id = str(canonical["semantic_profile_id"] or "")
        canonical_semantic_model_name = str(canonical["semantic_model_name"] or "")
        canonical_semantic_scorer_version = str(canonical["semantic_scorer_version"] or "")
        canonical_semantic_text_hash = str(canonical["semantic_text_hash"] or "")

        for row in duplicates:
            if not canonical_manual_fit_label and str(row["manual_fit_label"] or "").strip():
                canonical_manual_fit_label = str(row["manual_fit_label"] or "")
                canonical_manual_fit_reason_codes = str(row["manual_fit_reason_codes"] or "")
                canonical_manual_labeled_at = str(row["manual_labeled_at"] or "")
            if not canonical_notified and int(row["notified"] or 0):
                canonical_notified = 1
                canonical_notified_at = str(row["notified_at"] or "")
            if not canonical_prev_status and str(row["source_quality_prev_status"] or "").strip():
                canonical_prev_status = str(row["source_quality_prev_status"] or "")
            if not canonical_recovered_at and str(row["source_quality_recovered_at"] or "").strip():
                canonical_recovered_at = str(row["source_quality_recovered_at"] or "")
            if not canonical_profile_match_label and str(row["profile_match_label"] or "").strip():
                canonical_profile_match_score = float(row["profile_match_score"] or 0.0)
                canonical_profile_match_label = str(row["profile_match_label"] or "")
                canonical_profile_match_reason_codes = str(row["profile_match_reason_codes"] or "")
                canonical_profile_version = str(row["profile_version"] or "")
                canonical_scorer_version = str(row["scorer_version"] or "")
                if not canonical_job_text_version and str(row["job_text_version"] or "").strip():
                    canonical_job_text_version = str(row["job_text_version"] or "")
            if not canonical_snapshot and str(row["job_text_snapshot"] or "").strip():
                canonical_snapshot = str(row["job_text_snapshot"] or "")
            if not canonical_semantic_match_label and str(row["semantic_match_label"] or "").strip():
                canonical_semantic_match_score = float(row["semantic_match_score"] or 0.0)
                canonical_semantic_match_label = str(row["semantic_match_label"] or "")
                canonical_semantic_match_reason_codes = str(row["semantic_match_reason_codes"] or "")
                canonical_semantic_base_score = float(row["semantic_base_score"] or 0.0)
                canonical_semantic_research_heaviness_score = float(row["semantic_research_heaviness_score"] or 0.0)
                canonical_semantic_adjustment_reason_codes = str(row["semantic_adjustment_reason_codes"] or "")
                canonical_semantic_profile_id = str(row["semantic_profile_id"] or "")
                canonical_semantic_model_name = str(row["semantic_model_name"] or "")
                canonical_semantic_scorer_version = str(row["semantic_scorer_version"] or "")
                canonical_semantic_text_hash = str(row["semantic_text_hash"] or "")

        self._conn.execute(
            """
            UPDATE jobs
            SET manual_fit_label = ?,
                manual_fit_reason_codes = ?,
                manual_labeled_at = ?,
                notified = ?,
                notified_at = ?,
                source_quality_prev_status = ?,
                source_quality_recovered_at = ?,
                profile_match_score = ?,
                profile_match_label = ?,
                profile_match_reason_codes = ?,
                profile_version = ?,
                scorer_version = ?,
                job_text_version = ?,
                job_text_snapshot = ?,
                semantic_match_score = ?,
                semantic_match_label = ?,
                semantic_match_reason_codes = ?,
                semantic_base_score = ?,
                semantic_research_heaviness_score = ?,
                semantic_adjustment_reason_codes = ?,
                semantic_profile_id = ?,
                semantic_model_name = ?,
                semantic_scorer_version = ?,
                semantic_text_hash = ?
            WHERE id = ?
            """,
            (
                canonical_manual_fit_label or None,
                canonical_manual_fit_reason_codes or None,
                canonical_manual_labeled_at or None,
                canonical_notified,
                canonical_notified_at or None,
                canonical_prev_status or None,
                canonical_recovered_at or None,
                canonical_profile_match_score,
                canonical_profile_match_label or None,
                canonical_profile_match_reason_codes or None,
                canonical_profile_version or None,
                canonical_scorer_version or None,
                canonical_job_text_version or None,
                canonical_snapshot or None,
                canonical_semantic_match_score,
                canonical_semantic_match_label or None,
                canonical_semantic_match_reason_codes or None,
                canonical_semantic_base_score,
                canonical_semantic_research_heaviness_score,
                canonical_semantic_adjustment_reason_codes or None,
                canonical_semantic_profile_id or None,
                canonical_semantic_model_name or None,
                canonical_semantic_scorer_version or None,
                canonical_semantic_text_hash or None,
                int(canonical["id"]),
            ),
        )

    def _merge_handshake_duplicate_seen_events(self, canonical: sqlite3.Row, duplicates: list[sqlite3.Row]) -> None:
        dedupe_keys = [str(canonical["dedupe_key"])] + [str(row["dedupe_key"]) for row in duplicates]
        placeholders = ", ".join("?" for _ in dedupe_keys)
        event_rows = self._conn.execute(
            f"""
            SELECT dedupe_key, first_seen_at, last_seen_at, seen_count, notified, notified_at
            FROM seen_events
            WHERE dedupe_key IN ({placeholders})
            """,
            tuple(dedupe_keys),
        ).fetchall()
        if not event_rows:
            return

        first_seen_values = [str(row["first_seen_at"]) for row in event_rows if str(row["first_seen_at"] or "").strip()]
        last_seen_values = [str(row["last_seen_at"]) for row in event_rows if str(row["last_seen_at"] or "").strip()]
        notified_values = [str(row["notified_at"]) for row in event_rows if str(row["notified_at"] or "").strip()]
        seen_count = sum(int(row["seen_count"] or 0) for row in event_rows)
        notified = 1 if any(int(row["notified"] or 0) for row in event_rows) else 0

        self._conn.execute(
            """
            INSERT INTO seen_events (dedupe_key, first_seen_at, last_seen_at, seen_count, notified, notified_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(dedupe_key) DO UPDATE SET
                first_seen_at = excluded.first_seen_at,
                last_seen_at = excluded.last_seen_at,
                seen_count = excluded.seen_count,
                notified = excluded.notified,
                notified_at = excluded.notified_at
            """,
            (
                str(canonical["dedupe_key"]),
                min(first_seen_values) if first_seen_values else datetime.now(timezone.utc).isoformat(),
                max(last_seen_values) if last_seen_values else datetime.now(timezone.utc).isoformat(),
                seen_count,
                notified,
                min(notified_values) if notified_values else None,
            ),
        )

    def list_jobs_for_labeling(self, limit: int = 20, unlabeled_only: bool = True) -> list[sqlite3.Row]:
        safe_limit = max(limit, 1)
        if unlabeled_only:
            query = """
                SELECT id, source, company, title, location, posted_at, url, description,
                       source_quality_status, relevance_score, manual_fit_label, manual_fit_reason_codes, manual_labeled_at
                FROM jobs
                WHERE manual_fit_label IS NULL OR TRIM(manual_fit_label) = ''
                ORDER BY ingested_at DESC, id DESC
                LIMIT ?
            """
            rows = self._conn.execute(query, (safe_limit * 3,)).fetchall()
            return _filter_canonical_handshake_rows(rows, safe_limit)

        query = """
            SELECT id, source, company, title, location, posted_at, url, description,
                   source_quality_status, relevance_score, manual_fit_label, manual_fit_reason_codes, manual_labeled_at
            FROM jobs
            ORDER BY ingested_at DESC, id DESC
            LIMIT ?
        """
        rows = self._conn.execute(query, (safe_limit * 3,)).fetchall()
        return _filter_canonical_handshake_rows(rows, safe_limit)

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
                   source_quality_status, source_quality_reason_codes, source_quality_prev_status,
                   source_quality_recovered_at, source_metadata,
                   manual_fit_label, manual_fit_reason_codes, manual_labeled_at
            FROM jobs
            {where_sql}
            ORDER BY ingested_at DESC, id DESC
            LIMIT ?
        """
        params.append(safe_limit * 3)
        rows = self._conn.execute(query, tuple(params)).fetchall()
        return _filter_canonical_handshake_rows(rows, safe_limit)

    def get_job_for_labeling(self, job_id: int) -> sqlite3.Row | None:
        row = self._conn.execute(
            """
            SELECT id, source, company, title, location, posted_at, url, description,
                   relevance_score, compensation_type, source_quality_status, source_quality_reason_codes,
                   source_quality_prev_status, source_quality_recovered_at, source_metadata,
                   manual_fit_label, manual_fit_reason_codes, manual_labeled_at
            FROM jobs
            WHERE id = ?
            LIMIT 1
            """,
            (job_id,),
        ).fetchone()
        if row is None:
            return None
        resolved = self._resolve_canonical_handshake_row(row)
        return resolved or row

    def set_manual_fit_label(self, job_id: int, label: str, reason_codes: list[str]) -> bool:
        row = self._conn.execute(
            "SELECT id, source, url FROM jobs WHERE id = ? LIMIT 1",
            (job_id,),
        ).fetchone()
        target_id = job_id
        if row is not None:
            resolved = self._resolve_canonical_handshake_row(row)
            if resolved is not None:
                target_id = int(resolved["id"])
        cursor = self._conn.execute(
            """
            UPDATE jobs
            SET manual_fit_label = ?, manual_fit_reason_codes = ?, manual_labeled_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (label, json.dumps(reason_codes), target_id),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def _resolve_canonical_handshake_row(self, row: sqlite3.Row) -> sqlite3.Row | None:
        if str(row["source"] or "") != "handshake":
            return row
        url = str(row["url"] or "").strip()
        if not url:
            return row
        normalized_url = _normalize_handshake_storage_url(url)
        candidates = [
            candidate
            for candidate in self._conn.execute(
                """
                SELECT *
                FROM jobs
                WHERE source = 'handshake'
                ORDER BY id
                """
            ).fetchall()
            if _normalize_handshake_storage_url(str(candidate["url"] or "").strip()) == normalized_url
        ]
        if not candidates:
            return row
        return max(candidates, key=_handshake_row_rank)

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
                   rejected_eligibility_count, rejected_relevance_count, rejected_source_quality_count,
                   recovered_source_quality_count, persisted_count,
                   notified_count, duplicate_count, error_count, dead_token_count, feed_error_count,
                   security_verification_blocked_count
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
                    "rejected_source_quality_count": int(row["rejected_source_quality_count"] or 0),
                    "recovered_source_quality_count": int(row["recovered_source_quality_count"] or 0),
                    "persisted_count": int(row["persisted_count"] or 0),
                    "notified_count": int(row["notified_count"] or 0),
                    "duplicate_count": int(row["duplicate_count"] or 0),
                    "error_count": int(row["error_count"] or 0),
                    "dead_token_count": int(row["dead_token_count"] or 0),
                    "feed_error_count": int(row["feed_error_count"] or 0),
                    "security_verification_blocked_count": int(row["security_verification_blocked_count"] or 0),
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
                   source_quality_status, source_quality_reason_codes,
                   profile_match_score, profile_match_label, profile_match_reason_codes,
                   profile_version, scorer_version, job_text_version,
                   semantic_match_score, semantic_match_label, semantic_match_reason_codes,
                   semantic_base_score, semantic_research_heaviness_score, semantic_adjustment_reason_codes,
                   semantic_profile_id, semantic_model_name, semantic_scorer_version
            FROM jobs
            WHERE {where_sql}
            ORDER BY ingested_at DESC, id DESC
            LIMIT ?
        """
        params.append(safe_limit * 3)
        rows = self._conn.execute(query, tuple(params)).fetchall()
        return _filter_canonical_handshake_rows(rows, safe_limit)

    def get_stage2_job(self, job_id: int) -> sqlite3.Row | None:
        row = self._conn.execute(
            """
            SELECT id, source, company, title, location, posted_at, url, compensation_type,
                   relevance_score, eligibility_status, eligibility_confidence,
                   source_quality_status, source_quality_reason_codes, source_quality_prev_status,
                   source_quality_recovered_at, source_metadata,
                   profile_match_score, profile_match_label, profile_match_reason_codes,
                   profile_version, scorer_version, job_text_version, job_text_snapshot,
                   semantic_match_score, semantic_match_label, semantic_match_reason_codes,
                   semantic_base_score, semantic_research_heaviness_score, semantic_adjustment_reason_codes,
                   semantic_profile_id, semantic_model_name, semantic_scorer_version,
                   semantic_text_hash,
                   manual_fit_label, manual_fit_reason_codes
            FROM jobs
            WHERE id = ?
            LIMIT 1
            """,
            (job_id,),
        ).fetchone()
        if row is None:
            return None
        resolved = self._resolve_canonical_handshake_row(row)
        return resolved or row

    def list_stage2_labeled_jobs(self, limit: int = 200) -> list[sqlite3.Row]:
        safe_limit = max(limit, 1)
        rows = self._conn.execute(
            """
            SELECT id, source, company, title, location, posted_at, url, compensation_type,
                   eligibility_status, eligibility_confidence, relevance_score,
                   source_quality_status, source_quality_reason_codes, source_quality_prev_status,
                   source_quality_recovered_at, source_metadata,
                   profile_match_score, profile_match_label, profile_match_reason_codes,
                   profile_version, scorer_version, job_text_version, job_text_snapshot,
                   semantic_match_score, semantic_match_label, semantic_match_reason_codes,
                   semantic_base_score, semantic_research_heaviness_score, semantic_adjustment_reason_codes,
                   semantic_profile_id, semantic_model_name, semantic_scorer_version,
                   semantic_text_hash,
                   manual_fit_label, manual_fit_reason_codes
            FROM jobs
            WHERE manual_fit_label IS NOT NULL
              AND TRIM(manual_fit_label) <> ''
              AND job_text_version IS NOT NULL
              AND TRIM(job_text_version) <> ''
            ORDER BY manual_labeled_at DESC, id DESC
            LIMIT ?
            """,
            (safe_limit * 3,),
        ).fetchall()
        return _filter_canonical_handshake_rows(rows, safe_limit)

    def list_stage2_job_text_rows(
        self,
        limit: int = 200,
        *,
        label: str | None = None,
        source: str | None = None,
        labeled_only: bool = False,
    ) -> list[sqlite3.Row]:
        safe_limit = max(limit, 1)
        clauses = [
            "job_text_snapshot IS NOT NULL",
            "TRIM(job_text_snapshot) <> ''",
            "job_text_version IS NOT NULL",
            "TRIM(job_text_version) <> ''",
        ]
        params: list[object] = []
        if labeled_only:
            clauses.extend(["manual_fit_label IS NOT NULL", "TRIM(manual_fit_label) <> ''"])
        if label:
            clauses.append("profile_match_label = ?")
            params.append(label)
        if source:
            clauses.append("source = ?")
            params.append(source)
        where_sql = " AND ".join(clauses)
        query = f"""
            SELECT id, source, company, title, posted_at, profile_match_label, manual_fit_label,
                   job_text_version, job_text_snapshot
            FROM jobs
            WHERE {where_sql}
            ORDER BY ingested_at DESC, id DESC
            LIMIT ?
        """
        params.append(safe_limit * 3)
        rows = self._conn.execute(query, tuple(params)).fetchall()
        return _filter_canonical_handshake_rows(rows, safe_limit)

    def update_semantic_shadow(
        self,
        job_id: int,
        *,
        semantic_match_score: float,
        semantic_match_label: str,
        semantic_match_reason_codes: list[str],
        semantic_base_score: float,
        semantic_research_heaviness_score: float,
        semantic_adjustment_reason_codes: list[str],
        semantic_profile_id: str,
        semantic_model_name: str,
        semantic_scorer_version: str,
        semantic_text_hash: str,
    ) -> bool:
        cursor = self._conn.execute(
            """
            UPDATE jobs
            SET semantic_match_score = ?,
                semantic_match_label = ?,
                semantic_match_reason_codes = ?,
                semantic_base_score = ?,
                semantic_research_heaviness_score = ?,
                semantic_adjustment_reason_codes = ?,
                semantic_profile_id = ?,
                semantic_model_name = ?,
                semantic_scorer_version = ?,
                semantic_text_hash = ?
            WHERE id = ?
            """,
            (
                semantic_match_score,
                semantic_match_label,
                json.dumps(semantic_match_reason_codes),
                semantic_base_score,
                semantic_research_heaviness_score,
                json.dumps(semantic_adjustment_reason_codes),
                semantic_profile_id,
                semantic_model_name,
                semantic_scorer_version,
                semantic_text_hash,
                job_id,
            ),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def list_stage2_comparison_rows(
        self,
        limit: int = 200,
        *,
        source: str | None = None,
        labeled_only: bool = False,
    ) -> list[sqlite3.Row]:
        safe_limit = max(limit, 1)
        clauses = [
            "job_text_version IS NOT NULL",
            "TRIM(job_text_version) <> ''",
        ]
        params: list[object] = []
        if labeled_only:
            clauses.extend(["manual_fit_label IS NOT NULL", "TRIM(manual_fit_label) <> ''"])
        if source:
            clauses.append("source = ?")
            params.append(source)
        where_sql = " AND ".join(clauses)
        query = f"""
            SELECT id, source, company, title, location, posted_at, compensation_type,
                   profile_match_score, profile_match_label, profile_match_reason_codes,
                   semantic_match_score, semantic_match_label, semantic_match_reason_codes,
                   semantic_base_score, semantic_research_heaviness_score, semantic_adjustment_reason_codes,
                   semantic_profile_id, manual_fit_label, manual_fit_reason_codes
            FROM jobs
            WHERE {where_sql}
            ORDER BY ingested_at DESC, id DESC
            LIMIT ?
        """
        params.append(safe_limit * 3)
        rows = self._conn.execute(query, tuple(params)).fetchall()
        return _filter_canonical_handshake_rows(rows, safe_limit)

    def get_job_for_tailoring(self, job_id: int) -> sqlite3.Row | None:
        row = self._conn.execute(
            """
            SELECT id, source, company, title, location, posted_at, url, description,
                   relevance_score, compensation_type, source_quality_status,
                   profile_match_score, profile_match_label, profile_match_reason_codes,
                   job_text_version, job_text_snapshot
            FROM jobs
            WHERE id = ?
            LIMIT 1
            """,
            (job_id,),
        ).fetchone()
        if row is None:
            return None
        resolved = self._resolve_canonical_handshake_row(row)
        return resolved or row

    def list_tailoring_candidates(
        self,
        *,
        limit: int = 10,
        label: str | None = None,
        source: str | None = None,
    ) -> list[sqlite3.Row]:
        safe_limit = max(limit, 1)
        clauses: list[str] = []
        params: list[object] = []
        if label:
            clauses.append("profile_match_label = ?")
            params.append(label)
        else:
            clauses.append("profile_match_label IN ('pass', 'review')")
        if source:
            clauses.append("source = ?")
            params.append(source)
        where_sql = ""
        if clauses:
            where_sql = "WHERE " + " AND ".join(clauses)
        rows = self._conn.execute(
            f"""
            SELECT id, source, company, title, location, posted_at, url, description,
                   relevance_score, compensation_type, source_quality_status,
                   profile_match_score, profile_match_label, profile_match_reason_codes,
                   job_text_version, job_text_snapshot, ingested_at
            FROM jobs
            {where_sql}
            ORDER BY profile_match_score DESC, ingested_at DESC, id DESC
            LIMIT ?
            """,
            (*params, safe_limit * 3),
        ).fetchall()
        return _filter_canonical_handshake_rows(rows, safe_limit)

    def find_tailoring_artifact(
        self,
        *,
        job_id: int,
        profile_name: str,
        prompt_version: str,
        resume_source_hash: str,
        cover_letter_source_hash: str,
        preferences_source_hash: str,
        job_context_hash: str,
    ) -> sqlite3.Row | None:
        return self._conn.execute(
            """
            SELECT *
            FROM tailoring_artifacts
            WHERE job_id = ?
              AND profile_name = ?
              AND prompt_version = ?
              AND resume_source_hash = ?
              AND cover_letter_source_hash = ?
              AND preferences_source_hash = ?
              AND job_context_hash = ?
            LIMIT 1
            """,
            (
                job_id,
                profile_name,
                prompt_version,
                resume_source_hash,
                cover_letter_source_hash,
                preferences_source_hash,
                job_context_hash,
            ),
        ).fetchone()

    def upsert_tailoring_artifact(
        self,
        *,
        job_id: int,
        profile_name: str,
        provider_name: str,
        model_name: str,
        prompt_version: str,
        resume_source_hash: str,
        cover_letter_source_hash: str,
        preferences_source_hash: str,
        job_context_hash: str,
        resume_markdown: str,
        cover_letter_markdown: str,
        highlight_requirements: list[str],
        evidence_map: list[dict[str, str]],
        output_dir: str,
    ) -> tuple[int, bool]:
        existing = self.find_tailoring_artifact(
            job_id=job_id,
            profile_name=profile_name,
            prompt_version=prompt_version,
            resume_source_hash=resume_source_hash,
            cover_letter_source_hash=cover_letter_source_hash,
            preferences_source_hash=preferences_source_hash,
            job_context_hash=job_context_hash,
        )
        if existing is not None:
            self._conn.execute(
                """
                UPDATE tailoring_artifacts
                SET provider_name = ?,
                    model_name = ?,
                    resume_markdown = ?,
                    cover_letter_markdown = ?,
                    highlight_requirements = ?,
                    evidence_map = ?,
                    output_dir = ?,
                    created_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    provider_name,
                    model_name,
                    resume_markdown,
                    cover_letter_markdown,
                    json.dumps(highlight_requirements),
                    json.dumps(evidence_map),
                    output_dir,
                    int(existing["id"]),
                ),
            )
            self._conn.commit()
            return int(existing["id"]), False

        cursor = self._conn.execute(
            """
            INSERT INTO tailoring_artifacts (
                job_id, profile_name, provider_name, model_name, prompt_version,
                resume_source_hash, cover_letter_source_hash, preferences_source_hash, job_context_hash,
                resume_markdown, cover_letter_markdown, highlight_requirements, evidence_map,
                output_dir
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                profile_name,
                provider_name,
                model_name,
                prompt_version,
                resume_source_hash,
                cover_letter_source_hash,
                preferences_source_hash,
                job_context_hash,
                resume_markdown,
                cover_letter_markdown,
                json.dumps(highlight_requirements),
                json.dumps(evidence_map),
                output_dir,
            ),
        )
        self._conn.commit()
        return int(cursor.lastrowid), True

    def list_tailoring_artifacts(self, *, limit: int = 20, profile_name: str | None = None) -> list[sqlite3.Row]:
        safe_limit = max(limit, 1)
        clauses: list[str] = []
        params: list[object] = []
        if profile_name:
            clauses.append("ta.profile_name = ?")
            params.append(profile_name)
        where_sql = ""
        if clauses:
            where_sql = "WHERE " + " AND ".join(clauses)
        return self._conn.execute(
            f"""
            SELECT ta.id, ta.job_id, ta.profile_name, ta.provider_name, ta.model_name, ta.prompt_version,
                   ta.output_dir, ta.created_at, j.company, j.title, j.source, j.profile_match_label
            FROM tailoring_artifacts ta
            JOIN jobs j ON j.id = ta.job_id
            {where_sql}
            ORDER BY ta.created_at DESC, ta.id DESC
            LIMIT ?
            """,
            (*params, safe_limit),
        ).fetchall()

    def get_tailoring_artifact(self, artifact_id: int) -> sqlite3.Row | None:
        return self._conn.execute(
            """
            SELECT ta.id, ta.job_id, ta.profile_name, ta.provider_name, ta.model_name, ta.prompt_version,
                   ta.resume_source_hash, ta.cover_letter_source_hash, ta.preferences_source_hash,
                   ta.job_context_hash, ta.resume_markdown, ta.cover_letter_markdown,
                   ta.highlight_requirements, ta.evidence_map, ta.output_dir, ta.created_at,
                   j.company, j.title, j.source, j.url
            FROM tailoring_artifacts ta
            JOIN jobs j ON j.id = ta.job_id
            WHERE ta.id = ?
            LIMIT 1
            """,
            (artifact_id,),
        ).fetchone()

    def get_job_for_application(self, job_id: int) -> sqlite3.Row | None:
        row = self._conn.execute(
            """
            SELECT id, source, company, title, location, posted_at, url, description,
                   profile_match_score, profile_match_label, profile_match_reason_codes,
                   job_text_version, job_text_snapshot
            FROM jobs
            WHERE id = ?
            LIMIT 1
            """,
            (job_id,),
        ).fetchone()
        if row is None:
            return None
        resolved = self._resolve_canonical_handshake_row(row)
        return resolved or row

    def find_latest_tailoring_artifact(self, *, job_id: int, profile_name: str) -> sqlite3.Row | None:
        return self._conn.execute(
            """
            SELECT ta.id, ta.job_id, ta.profile_name, ta.provider_name, ta.model_name, ta.prompt_version,
                   ta.resume_source_hash, ta.cover_letter_source_hash, ta.preferences_source_hash,
                   ta.job_context_hash, ta.resume_markdown, ta.cover_letter_markdown,
                   ta.highlight_requirements, ta.evidence_map, ta.output_dir, ta.created_at,
                   j.company, j.title, j.source, j.url
            FROM tailoring_artifacts ta
            JOIN jobs j ON j.id = ta.job_id
            WHERE ta.job_id = ? AND ta.profile_name = ?
            ORDER BY ta.created_at DESC, ta.id DESC
            LIMIT 1
            """,
            (job_id, profile_name),
        ).fetchone()

    def find_application_run(
        self,
        *,
        job_id: int,
        profile_name: str,
        adapter_name: str,
        status: str | None = None,
    ) -> sqlite3.Row | None:
        clauses = ["job_id = ?", "profile_name = ?", "adapter_name = ?"]
        params: list[object] = [job_id, profile_name, adapter_name]
        if status:
            clauses.append("status = ?")
            params.append(status)
        return self._conn.execute(
            f"""
            SELECT *
            FROM application_runs
            WHERE {" AND ".join(clauses)}
            ORDER BY id DESC
            LIMIT 1
            """,
            tuple(params),
        ).fetchone()

    def create_application_run(
        self,
        *,
        job_id: int,
        profile_name: str,
        tailoring_artifact_id: int | None,
        adapter_name: str,
        source: str,
        target_url: str,
        current_url: str,
        status: str,
        output_dir: str,
        blocked_reason: str | None = None,
        blocked_payload: dict[str, object] | None = None,
        confirmation_payload: dict[str, object] | None = None,
        submitted_at: str | None = None,
    ) -> int:
        cursor = self._conn.execute(
            """
            INSERT INTO application_runs (
                job_id, profile_name, tailoring_artifact_id, adapter_name, source, target_url,
                current_url, status, attempt_count, blocked_reason, blocked_payload,
                confirmation_payload, output_dir, submitted_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                job_id,
                profile_name,
                tailoring_artifact_id,
                adapter_name,
                source,
                target_url,
                current_url,
                status,
                blocked_reason,
                json.dumps(blocked_payload) if blocked_payload is not None else None,
                json.dumps(confirmation_payload) if confirmation_payload is not None else None,
                output_dir,
                submitted_at,
            ),
        )
        self._conn.commit()
        return int(cursor.lastrowid)

    def update_application_run(
        self,
        application_run_id: int,
        *,
        tailoring_artifact_id: int | None = None,
        target_url: str | None = None,
        current_url: str | None = None,
        status: str | None = None,
        blocked_reason: str | None = None,
        blocked_payload: dict[str, object] | None = None,
        confirmation_payload: dict[str, object] | None = None,
        increment_attempt_count: bool = False,
        submitted_at: str | None = None,
        output_dir: str | None = None,
    ) -> None:
        assignments = ["updated_at = CURRENT_TIMESTAMP"]
        params: list[object] = []
        if tailoring_artifact_id is not None:
            assignments.append("tailoring_artifact_id = ?")
            params.append(tailoring_artifact_id)
        if target_url is not None:
            assignments.append("target_url = ?")
            params.append(target_url)
        if current_url is not None:
            assignments.append("current_url = ?")
            params.append(current_url)
        if status is not None:
            assignments.append("status = ?")
            params.append(status)
        if blocked_reason is not None or status == "blocked":
            assignments.append("blocked_reason = ?")
            params.append(blocked_reason)
        if blocked_payload is not None or status == "blocked":
            assignments.append("blocked_payload = ?")
            params.append(json.dumps(blocked_payload) if blocked_payload is not None else None)
        if confirmation_payload is not None or status == "submitted":
            assignments.append("confirmation_payload = ?")
            params.append(json.dumps(confirmation_payload) if confirmation_payload is not None else None)
        if increment_attempt_count:
            assignments.append("attempt_count = attempt_count + 1")
        if submitted_at is not None:
            assignments.append("submitted_at = ?")
            params.append(submitted_at)
        if output_dir is not None:
            assignments.append("output_dir = ?")
            params.append(output_dir)
        params.append(application_run_id)
        self._conn.execute(
            f"""
            UPDATE application_runs
            SET {", ".join(assignments)}
            WHERE id = ?
            """,
            tuple(params),
        )
        self._conn.commit()

    def get_application_run(self, application_run_id: int) -> sqlite3.Row | None:
        return self._conn.execute(
            """
            SELECT ar.*, j.company, j.title, j.url AS job_url
            FROM application_runs ar
            JOIN jobs j ON j.id = ar.job_id
            WHERE ar.id = ?
            LIMIT 1
            """,
            (application_run_id,),
        ).fetchone()

    def list_application_runs(self, *, status: str | None = None, limit: int = 20) -> list[sqlite3.Row]:
        clauses: list[str] = []
        params: list[object] = []
        if status:
            clauses.append("ar.status = ?")
            params.append(status)
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return self._conn.execute(
            f"""
            SELECT ar.*, j.company, j.title
            FROM application_runs ar
            JOIN jobs j ON j.id = ar.job_id
            {where_sql}
            ORDER BY ar.updated_at DESC, ar.id DESC
            LIMIT ?
            """,
            (*params, max(limit, 1)),
        ).fetchall()

    def insert_application_step(
        self,
        *,
        application_run_id: int,
        step_key: str,
        step_label: str,
        status: str,
        field_name: str | None = None,
        field_type: str | None = None,
        question_text: str | None = None,
        answer_source: str | None = None,
        answer_value: str | None = None,
        screenshot_path: str | None = None,
        payload_json: dict[str, object] | None = None,
    ) -> int:
        cursor = self._conn.execute(
            """
            INSERT INTO application_steps (
                application_run_id, step_key, step_label, status, field_name, field_type,
                question_text, answer_source, answer_value, screenshot_path, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                application_run_id,
                step_key,
                step_label,
                status,
                field_name,
                field_type,
                question_text,
                answer_source,
                answer_value,
                screenshot_path,
                json.dumps(payload_json) if payload_json is not None else None,
            ),
        )
        self._conn.commit()
        return int(cursor.lastrowid)

    def list_application_steps(self, application_run_id: int) -> list[sqlite3.Row]:
        return self._conn.execute(
            """
            SELECT *
            FROM application_steps
            WHERE application_run_id = ?
            ORDER BY id ASC
            """,
            (application_run_id,),
        ).fetchall()

    def list_recent_handshake_quarantined_urls(self, days: int = 7, limit: int = 50) -> list[str]:
        safe_days = max(days, 1)
        safe_limit = max(limit, 1)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=safe_days)).isoformat()
        rows = self._conn.execute(
            """
            SELECT DISTINCT url
            FROM jobs
            WHERE source = 'handshake'
              AND source_quality_status IN ('card_only', 'detail_polluted', 'detail_mismatch')
              AND ingested_at >= ?
              AND url IS NOT NULL
              AND TRIM(url) <> ''
            ORDER BY ingested_at DESC, id DESC
            LIMIT ?
            """,
            (cutoff, safe_limit),
        ).fetchall()
        return [str(row["url"]) for row in rows if str(row["url"] or "").strip()]

    def list_recent_handshake_suspect_urls(self, days: int = 30, limit: int = 50) -> list[str]:
        safe_days = max(days, 1)
        safe_limit = max(limit, 1)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=safe_days)).isoformat()
        rows = self._conn.execute(
            """
            SELECT DISTINCT url
            FROM jobs
            WHERE source = 'handshake'
              AND ingested_at >= ?
              AND url IS NOT NULL
              AND TRIM(url) <> ''
              AND (
                    source_quality_status IN ('card_only', 'detail_polluted', 'detail_mismatch')
                    OR LOWER(COALESCE(description, '')) LIKE '%summary beta%'
                  )
            ORDER BY ingested_at DESC, id DESC
            LIMIT ?
            """,
            (cutoff, safe_limit),
        ).fetchall()
        return [str(row["url"]) for row in rows if str(row["url"] or "").strip()]


def _source_quality_recovered(previous_status: str, current_status: str) -> bool:
    previous = previous_status.strip().lower()
    current = current_status.strip().lower()
    if previous not in SOURCE_QUALITY_QUARANTINE_STATUSES:
        return False
    return current == "detail_complete"


def _has_handshake_page_chrome(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in HANDSHAKE_PAGE_CHROME_MARKERS)


def _looks_like_polluted_handshake_title(value: str) -> bool:
    title = value.strip()
    if not title:
        return False
    lowered = title.lower()
    if SUMMARY_BETA_MARKER in lowered:
        return True
    if " is seeking " in lowered or " is looking for " in lowered:
        return True
    if len(title) >= 140:
        return True
    if title.count(".") >= 2:
        return True
    return False


def _handshake_row_rank(row: sqlite3.Row) -> tuple[int, int, int, int, int, int]:
    status = str(_row_value(row, "source_quality_status") or "").strip().lower()
    title = str(_row_value(row, "title") or "")
    description = str(_row_value(row, "description") or "")
    has_polluted_title = _looks_like_polluted_handshake_title(title)
    has_page_chrome = _has_handshake_page_chrome(description)
    has_summary_beta = SUMMARY_BETA_MARKER in description.lower()
    return (
        HANDSHAKE_QUALITY_STATUS_SCORES.get(status, 0),
        0 if has_polluted_title else 1,
        0 if has_page_chrome else 1,
        0 if has_summary_beta else 1,
        len(description.strip()),
        -int(row["id"]),
    )


def _filter_canonical_handshake_rows(rows: list[sqlite3.Row], limit: int) -> list[sqlite3.Row]:
    handshake_best_by_url: dict[str, sqlite3.Row] = {}

    for row in rows:
        source = str(_row_value(row, "source") or "")
        url = str(_row_value(row, "url") or "").strip()
        if source != "handshake" or not url:
            continue
        normalized_url = _normalize_handshake_storage_url(url)
        if normalized_url not in handshake_best_by_url:
            handshake_best_by_url[normalized_url] = row
            continue
        if _handshake_row_rank(row) > _handshake_row_rank(handshake_best_by_url[normalized_url]):
            handshake_best_by_url[normalized_url] = row

    emitted_handshake_urls: set[str] = set()
    filtered: list[sqlite3.Row] = []
    for row in rows:
        source = str(_row_value(row, "source") or "")
        url = str(_row_value(row, "url") or "").strip()
        if source == "handshake" and url:
            normalized_url = _normalize_handshake_storage_url(url)
            if normalized_url in emitted_handshake_urls:
                continue
            filtered.append(handshake_best_by_url[normalized_url])
            emitted_handshake_urls.add(normalized_url)
        else:
            filtered.append(row)
        if len(filtered) >= limit:
            break
    return filtered


def _normalize_handshake_storage_url(url: str) -> str:
    value = url.strip()
    if not value:
        return value
    parsed = urlparse(value)
    if "/jobs/" in parsed.path:
        return urlunparse(parsed._replace(query="", fragment=""))
    if "/job-search/" in parsed.path:
        parts = parsed.path.rstrip("/").split("/")
        if parts and parts[-1].isdigit():
            return urlunparse(parsed._replace(path=f"/jobs/{parts[-1]}", query="", fragment=""))
        query_pairs = [(key, val) for key, val in parse_qsl(parsed.query, keep_blank_values=True) if key != "searchId"]
        normalized_query = urlencode(query_pairs, doseq=True)
        return urlunparse(parsed._replace(query=normalized_query, fragment=""))
    return value


def _row_value(row: sqlite3.Row, key: str) -> object | None:
    if key not in row.keys():
        return None
    return row[key]


def ensure_parent_dir(db_path: str) -> None:
    path = Path(db_path)
    if path.parent and str(path.parent) != ".":
        path.parent.mkdir(parents=True, exist_ok=True)
