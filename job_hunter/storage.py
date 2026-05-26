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
            """
        )
        self._conn.commit()

    def is_seen(self, dedupe_key: str) -> bool:
        row = self._conn.execute(
            "SELECT dedupe_key FROM seen_events WHERE dedupe_key = ? LIMIT 1",
            (dedupe_key,),
        ).fetchone()
        return row is not None

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
                    relevance_hits
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        self._conn.execute(
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
        self._conn.commit()


def ensure_parent_dir(db_path: str) -> None:
    path = Path(db_path)
    if path.parent and str(path.parent) != ".":
        path.parent.mkdir(parents=True, exist_ok=True)
