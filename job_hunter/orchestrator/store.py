from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


TERMINAL_WORKFLOW_STATUSES = {"skipped", "submitted", "failed"}


class OrchestratorStore:
    """Application-owned durable state; LangGraph checkpoints are execution-only."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def close(self) -> None:
        self._conn.close()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS orchestrator_state (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS orchestrator_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trigger_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempt_limit INTEGER NOT NULL,
                    policy_snapshot TEXT NOT NULL,
                    phoenix_trace_id TEXT,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    finished_at TEXT
                );

                CREATE TABLE IF NOT EXISTS workflow_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id INTEGER NOT NULL UNIQUE,
                    thread_id TEXT NOT NULL UNIQUE,
                    run_id INTEGER,
                    profile_match_label TEXT,
                    selected_profile TEXT,
                    decision_rationale TEXT,
                    status TEXT NOT NULL DEFAULT 'queued',
                    tailoring_artifact_id INTEGER,
                    application_run_id INTEGER,
                    blocker_reason TEXT,
                    attempt_started_at TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(job_id) REFERENCES jobs(id),
                    FOREIGN KEY(run_id) REFERENCES orchestrator_runs(id)
                );

                CREATE TABLE IF NOT EXISTS workflow_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workflow_id INTEGER,
                    run_id INTEGER,
                    event_type TEXT NOT NULL,
                    agent_role TEXT,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    latency_ms REAL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(workflow_id) REFERENCES workflow_items(id),
                    FOREIGN KEY(run_id) REFERENCES orchestrator_runs(id)
                );

                CREATE TABLE IF NOT EXISTS interventions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workflow_id INTEGER NOT NULL,
                    application_run_id INTEGER,
                    kind TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    telegram_message_id TEXT,
                    resolution_json TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    resolved_at TEXT,
                    FOREIGN KEY(workflow_id) REFERENCES workflow_items(id)
                );

                CREATE TABLE IF NOT EXISTS source_registry (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_type TEXT NOT NULL,
                    source_value TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'candidate',
                    provenance TEXT NOT NULL,
                    discovery_query TEXT,
                    consecutive_successes INTEGER NOT NULL DEFAULT 0,
                    consecutive_failures INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    last_probed_at TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(source_type, source_value)
                );

                CREATE TABLE IF NOT EXISTS source_registry_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_registry_id INTEGER NOT NULL,
                    previous_status TEXT,
                    new_status TEXT NOT NULL,
                    rationale TEXT NOT NULL,
                    agent_role TEXT NOT NULL DEFAULT 'sourcing',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(source_registry_id) REFERENCES source_registry(id)
                );

                CREATE TABLE IF NOT EXISTS source_discovery_usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider TEXT NOT NULL,
                    query_text TEXT NOT NULL,
                    credits_used INTEGER NOT NULL DEFAULT 1,
                    result_count INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_workflow_status ON workflow_items(status, updated_at);
                CREATE INDEX IF NOT EXISTS idx_workflow_events_run ON workflow_events(run_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_interventions_status ON interventions(status, created_at);
                CREATE INDEX IF NOT EXISTS idx_source_registry_status ON source_registry(status, source_type);
                """
            )
            self._conn.commit()

    def initialize(self, *, new_jobs_only: bool = True) -> int:
        existing = self.get_state("baseline_job_id")
        if existing is not None:
            return int(existing)
        row = self._conn.execute("SELECT COALESCE(MAX(id), 0) AS max_id FROM jobs").fetchone()
        baseline = int(row["max_id"] or 0) if new_jobs_only else 0
        self.set_state("baseline_job_id", baseline)
        self.set_state("initialized_at", _utc_now())
        return baseline

    def get_state(self, key: str) -> object | None:
        row = self._conn.execute(
            "SELECT value_json FROM orchestrator_state WHERE key = ?",
            (key,),
        ).fetchone()
        return json.loads(str(row["value_json"])) if row is not None else None

    def set_state(self, key: str, value: object) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO orchestrator_state (key, value_json, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (key, json.dumps(value, sort_keys=True)),
            )
            self._conn.commit()

    def acquire_lease(self, *, owner: str | None = None, ttl_seconds: int = 180) -> str | None:
        owner = owner or uuid.uuid4().hex
        now = datetime.now(timezone.utc)
        lease = {
            "owner": owner,
            "expires_at": (now + timedelta(seconds=max(ttl_seconds, 1))).isoformat(),
        }
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                row = self._conn.execute(
                    "SELECT value_json FROM orchestrator_state WHERE key = 'daemon_lease'"
                ).fetchone()
                raw = json.loads(str(row["value_json"])) if row is not None else None
                if isinstance(raw, dict):
                    expires = _parse_datetime(str(raw.get("expires_at") or ""))
                    if expires is not None and expires > now and raw.get("owner") != owner:
                        self._conn.rollback()
                        return None
                self._conn.execute(
                    """
                    INSERT INTO orchestrator_state (key, value_json, updated_at)
                    VALUES ('daemon_lease', ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(key) DO UPDATE SET
                        value_json = excluded.value_json,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (json.dumps(lease, sort_keys=True),),
                )
                self._conn.commit()
                return owner
            except Exception:
                self._conn.rollback()
                raise

    def release_lease(self, owner: str) -> None:
        raw = self.get_state("daemon_lease")
        if isinstance(raw, dict) and raw.get("owner") == owner:
            self.set_state("daemon_lease", {})

    def create_run(self, *, trigger_name: str, attempt_limit: int, policy_snapshot: dict[str, object]) -> int:
        cursor = self._conn.execute(
            """
            INSERT INTO orchestrator_runs (trigger_name, status, attempt_limit, policy_snapshot)
            VALUES (?, 'running', ?, ?)
            """,
            (trigger_name, max(attempt_limit, 0), json.dumps(policy_snapshot, sort_keys=True)),
        )
        self._conn.commit()
        return int(cursor.lastrowid)

    def finish_run(self, run_id: int, *, status: str, error: str | None = None) -> None:
        self._conn.execute(
            """
            UPDATE orchestrator_runs
            SET status = ?, error = ?, finished_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (status, error, run_id),
        )
        self._conn.commit()

    def list_candidates(self, *, limit: int = 25) -> list[dict[str, object]]:
        baseline = int(self.get_state("baseline_job_id") or 0)
        rows = self._conn.execute(
            """
            SELECT j.id, j.source, j.company, j.title, j.location, j.posted_at, j.url,
                   j.description, j.profile_match_label, j.profile_match_score,
                   j.profile_match_reason_codes, j.eligibility_status, j.eligibility_confidence,
                   j.policy_gate_status, j.policy_gate_reason_codes, j.source_quality_status,
                   j.relevance_score, j.compensation_type
            FROM jobs j
            LEFT JOIN workflow_items wi ON wi.job_id = j.id
            WHERE j.id > ?
              AND j.profile_match_label IN ('pass', 'review')
              AND COALESCE(j.policy_gate_status, '') NOT IN ('reject', 'rejected', 'fail')
              AND COALESCE(j.eligibility_status, '') NOT IN ('ineligible', 'reject')
              AND COALESCE(j.source_quality_status, '') NOT IN ('card_only', 'detail_polluted', 'detail_mismatch')
              AND wi.id IS NULL
              AND NOT EXISTS (
                  SELECT 1 FROM application_runs ar
                  WHERE ar.job_id = j.id AND ar.status = 'submitted'
              )
            ORDER BY j.profile_match_score DESC, j.relevance_score DESC, j.id ASC
            LIMIT ?
            """,
            (baseline, max(limit, 1)),
        ).fetchall()
        return [{key: row[key] for key in row.keys()} for row in rows]

    def create_workflow(self, *, job: dict[str, object], run_id: int) -> dict[str, object]:
        job_id = int(job["id"])
        existing = self.get_workflow_for_job(job_id)
        if existing is not None:
            return existing
        thread_id = f"job:{job_id}:{uuid.uuid4().hex[:12]}"
        cursor = self._conn.execute(
            """
            INSERT INTO workflow_items (job_id, thread_id, run_id, profile_match_label, status)
            VALUES (?, ?, ?, ?, 'queued')
            """,
            (job_id, thread_id, run_id, str(job.get("profile_match_label") or "")),
        )
        self._conn.commit()
        return self.get_workflow(int(cursor.lastrowid)) or {}

    def get_workflow(self, workflow_id: int) -> dict[str, object] | None:
        row = self._conn.execute("SELECT * FROM workflow_items WHERE id = ?", (workflow_id,)).fetchone()
        return _row_dict(row)

    def get_workflow_for_job(self, job_id: int) -> dict[str, object] | None:
        row = self._conn.execute("SELECT * FROM workflow_items WHERE job_id = ?", (job_id,)).fetchone()
        return _row_dict(row)

    def list_queued_workflows(self, *, limit: int = 25) -> list[dict[str, object]]:
        rows = self._conn.execute(
            """
            SELECT wi.id AS workflow_id, wi.thread_id, wi.run_id, j.*
            FROM workflow_items wi
            JOIN jobs j ON j.id = wi.job_id
            WHERE wi.status = 'queued'
            ORDER BY wi.created_at ASC, wi.id ASC
            LIMIT ?
            """,
            (max(limit, 1),),
        ).fetchall()
        return [_row_dict(row) or {} for row in rows]

    def update_workflow(self, workflow_id: int, **values: object) -> None:
        allowed = {
            "run_id", "selected_profile", "decision_rationale", "status",
            "tailoring_artifact_id", "application_run_id", "blocker_reason", "attempt_started_at",
        }
        changes = [(key, value) for key, value in values.items() if key in allowed]
        if not changes:
            return
        assignments = ", ".join(f"{key} = ?" for key, _ in changes)
        params = [value for _, value in changes]
        params.append(workflow_id)
        self._conn.execute(
            f"UPDATE workflow_items SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            tuple(params),
        )
        self._conn.commit()

    def record_event(
        self,
        *,
        workflow_id: int | None,
        run_id: int | None,
        event_type: str,
        agent_role: str | None,
        status: str,
        payload: dict[str, object] | None = None,
        latency_ms: float | None = None,
    ) -> int:
        cursor = self._conn.execute(
            """
            INSERT INTO workflow_events (
                workflow_id, run_id, event_type, agent_role, status, payload_json, latency_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                workflow_id,
                run_id,
                event_type,
                agent_role,
                status,
                json.dumps(_redact_payload(payload or {}), sort_keys=True),
                latency_ms,
            ),
        )
        self._conn.commit()
        return int(cursor.lastrowid)

    def attempts_today(self, timezone_name: str) -> int:
        zone = ZoneInfo(timezone_name)
        local_now = datetime.now(zone)
        local_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        utc_start = local_start.astimezone(timezone.utc).isoformat()
        utc_end = (local_start + timedelta(days=1)).astimezone(timezone.utc).isoformat()
        row = self._conn.execute(
            """
            SELECT COUNT(DISTINCT job_id) AS count
            FROM workflow_items
            WHERE attempt_started_at >= ? AND attempt_started_at < ?
            """,
            (utc_start, utc_end),
        ).fetchone()
        return int(row["count"] or 0)

    def mark_attempt_started(self, workflow_id: int) -> None:
        self._conn.execute(
            """
            UPDATE workflow_items
            SET attempt_started_at = COALESCE(attempt_started_at, ?), updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (_utc_now(), workflow_id),
        )
        self._conn.commit()

    def create_intervention(
        self,
        *,
        workflow_id: int,
        application_run_id: int | None,
        kind: str,
        prompt: str,
    ) -> int:
        existing = self._conn.execute(
            """
            SELECT id FROM interventions
            WHERE workflow_id = ? AND status = 'pending'
            ORDER BY id DESC LIMIT 1
            """,
            (workflow_id,),
        ).fetchone()
        if existing is not None:
            return int(existing["id"])
        cursor = self._conn.execute(
            """
            INSERT INTO interventions (workflow_id, application_run_id, kind, prompt)
            VALUES (?, ?, ?, ?)
            """,
            (workflow_id, application_run_id, kind, prompt),
        )
        self._conn.commit()
        return int(cursor.lastrowid)

    def resolve_intervention(self, intervention_id: int, resolution: dict[str, object]) -> bool:
        cursor = self._conn.execute(
            """
            UPDATE interventions
            SET status = 'resolved', resolution_json = ?, resolved_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'pending'
            """,
            (json.dumps(resolution, sort_keys=True), intervention_id),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def get_intervention(self, intervention_id: int) -> dict[str, object] | None:
        row = self._conn.execute(
            """
            SELECT i.*, wi.job_id, wi.thread_id, wi.selected_profile
            FROM interventions i
            JOIN workflow_items wi ON wi.id = i.workflow_id
            WHERE i.id = ?
            """,
            (intervention_id,),
        ).fetchone()
        return _row_dict(row)

    def list_interventions(self, *, status: str = "pending", limit: int = 50) -> list[dict[str, object]]:
        rows = self._conn.execute(
            """
            SELECT i.*, wi.job_id, wi.thread_id, wi.selected_profile
            FROM interventions i
            JOIN workflow_items wi ON wi.id = i.workflow_id
            WHERE i.status = ?
            ORDER BY i.id ASC LIMIT ?
            """,
            (status, max(limit, 1)),
        ).fetchall()
        return [_row_dict(row) or {} for row in rows]

    def upsert_source(
        self,
        *,
        source_type: str,
        source_value: str,
        provenance: str,
        discovery_query: str = "",
        status: str = "candidate",
        rationale: str = "source discovered",
    ) -> int:
        existing = self._conn.execute(
            "SELECT id FROM source_registry WHERE source_type = ? AND source_value = ?",
            (source_type, source_value),
        ).fetchone()
        if existing is not None:
            return int(existing["id"])
        cursor = self._conn.execute(
            """
            INSERT INTO source_registry (
                source_type, source_value, status, provenance, discovery_query
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (source_type, source_value, status, provenance, discovery_query),
        )
        source_id = int(cursor.lastrowid)
        self._conn.execute(
            """
            INSERT INTO source_registry_events (
                source_registry_id, previous_status, new_status, rationale
            ) VALUES (?, NULL, ?, ?)
            """,
            (source_id, status, rationale),
        )
        self._conn.commit()
        return source_id

    def update_source_probe(self, source_id: int, *, success: bool, error: str = "") -> str:
        row = self._conn.execute("SELECT * FROM source_registry WHERE id = ?", (source_id,)).fetchone()
        if row is None:
            raise RuntimeError(f"Source registry id {source_id} not found")
        successes = int(row["consecutive_successes"] or 0)
        failures = int(row["consecutive_failures"] or 0)
        previous = str(row["status"])
        if success:
            successes += 1
            failures = 0
        else:
            failures += 1
            successes = 0
        new_status = previous
        if success and successes >= 2 and previous in {"candidate", "probing", "quarantined"}:
            new_status = "active"
        elif not success and failures >= 2 and previous == "active":
            new_status = "quarantined"
        elif previous == "candidate":
            new_status = "probing"
        self._conn.execute(
            """
            UPDATE source_registry
            SET status = ?, consecutive_successes = ?, consecutive_failures = ?,
                last_error = ?, last_probed_at = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (new_status, successes, failures, error or None, _utc_now(), source_id),
        )
        if new_status != previous:
            self._conn.execute(
                """
                INSERT INTO source_registry_events (
                    source_registry_id, previous_status, new_status, rationale
                ) VALUES (?, ?, ?, ?)
                """,
                (source_id, previous, new_status, "probe succeeded" if success else (error or "probe failed")),
            )
        self._conn.commit()
        return new_status

    def set_source_status(self, source_id: int, *, status: str, rationale: str, agent_role: str = "sourcing") -> None:
        row = self._conn.execute("SELECT status FROM source_registry WHERE id = ?", (source_id,)).fetchone()
        if row is None:
            raise RuntimeError(f"Source registry id {source_id} not found")
        previous = str(row["status"])
        self._conn.execute(
            "UPDATE source_registry SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, source_id),
        )
        self._conn.execute(
            """
            INSERT INTO source_registry_events (
                source_registry_id, previous_status, new_status, rationale, agent_role
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (source_id, previous, status, rationale, agent_role),
        )
        self._conn.commit()

    def list_sources(self, *, status: str | None = None, limit: int = 500) -> list[dict[str, object]]:
        if status:
            rows = self._conn.execute(
                "SELECT * FROM source_registry WHERE status = ? ORDER BY source_type, source_value LIMIT ?",
                (status, max(limit, 1)),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM source_registry ORDER BY source_type, source_value LIMIT ?",
                (max(limit, 1),),
            ).fetchall()
        return [_row_dict(row) or {} for row in rows]

    def list_sources_for_probe(self, *, limit: int) -> list[dict[str, object]]:
        rows = self._conn.execute(
            """
            SELECT * FROM source_registry
            WHERE status IN ('candidate', 'probing', 'quarantined', 'active')
            ORDER BY
                CASE status
                    WHEN 'candidate' THEN 0
                    WHEN 'probing' THEN 1
                    WHEN 'quarantined' THEN 2
                    ELSE 3
                END,
                COALESCE(last_probed_at, '') ASC,
                id ASC
            LIMIT ?
            """,
            (max(limit, 1),),
        ).fetchall()
        return [_row_dict(row) or {} for row in rows]

    def source_history(self, source_id: int, *, limit: int = 100) -> list[dict[str, object]]:
        rows = self._conn.execute(
            """
            SELECT * FROM source_registry_events
            WHERE source_registry_id = ? ORDER BY id DESC LIMIT ?
            """,
            (source_id, max(limit, 1)),
        ).fetchall()
        return [_row_dict(row) or {} for row in rows]

    def rollback_source(self, source_id: int) -> str:
        rows = self.source_history(source_id, limit=1)
        if not rows:
            raise RuntimeError(f"No source history found for id {source_id}")
        previous = str(rows[0].get("previous_status") or "").strip()
        if not previous:
            raise RuntimeError(f"Source id {source_id} has no previous state to restore")
        self.set_source_status(
            source_id,
            status=previous,
            rationale=f"rolled back event {rows[0]['id']}",
            agent_role="operator",
        )
        return previous

    def record_discovery_usage(
        self,
        *,
        query: str,
        credits_used: int,
        result_count: int,
        status: str,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO source_discovery_usage (
                provider, query_text, credits_used, result_count, status
            ) VALUES ('tavily', ?, ?, ?, ?)
            """,
            (query, max(credits_used, 0), max(result_count, 0), status),
        )
        self._conn.commit()

    def discovery_credits_today(self, timezone_name: str) -> int:
        zone = ZoneInfo(timezone_name)
        local_start = datetime.now(zone).replace(hour=0, minute=0, second=0, microsecond=0)
        # SQLite's CURRENT_TIMESTAMP uses ``YYYY-MM-DD HH:MM:SS``. Match that
        # representation so lexical comparisons remain chronological.
        start = _sqlite_timestamp(local_start.astimezone(timezone.utc))
        end = _sqlite_timestamp((local_start + timedelta(days=1)).astimezone(timezone.utc))
        row = self._conn.execute(
            """
            SELECT COALESCE(SUM(credits_used), 0) AS credits
            FROM source_discovery_usage
            WHERE created_at >= ? AND created_at < ?
            """,
            (start, end),
        ).fetchone()
        return int(row["credits"] or 0)

    def report(self, *, days: int = 7) -> dict[str, object]:
        cutoff = _sqlite_timestamp(datetime.now(timezone.utc) - timedelta(days=max(days, 1)))
        workflow_rows = self._conn.execute(
            """
            SELECT status, COUNT(*) AS count FROM workflow_items
            WHERE created_at >= ? GROUP BY status
            """,
            (cutoff,),
        ).fetchall()
        blocker_rows = self._conn.execute(
            """
            SELECT COALESCE(blocker_reason, 'none') AS blocker, COUNT(*) AS count
            FROM workflow_items WHERE created_at >= ? GROUP BY COALESCE(blocker_reason, 'none')
            """,
            (cutoff,),
        ).fetchall()
        return {
            "days": max(days, 1),
            "workflows": {str(row["status"]): int(row["count"]) for row in workflow_rows},
            "blockers": {str(row["blocker"]): int(row["count"]) for row in blocker_rows},
            "pending_interventions": len(self.list_interventions(status="pending")),
            "sources": {
                status: len(self.list_sources(status=status))
                for status in ("candidate", "probing", "active", "quarantined", "disabled")
            },
        }


def _row_dict(row: sqlite3.Row | None) -> dict[str, object] | None:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sqlite_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _parse_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _redact_payload(payload: dict[str, object]) -> dict[str, object]:
    sensitive = {
        "password", "token", "api_key", "email", "phone", "address", "answer_value",
        "resume_markdown", "cover_letter_markdown", "description", "prompt", "messages",
    }
    redacted: dict[str, object] = {}
    for key, value in payload.items():
        if key.lower() in sensitive or any(part in key.lower() for part in ("secret", "credential")):
            redacted[key] = "[REDACTED]"
        elif isinstance(value, dict):
            redacted[key] = _redact_payload(value)
        elif isinstance(value, list):
            redacted[key] = [
                _redact_payload(item) if isinstance(item, dict) else item for item in value
            ]
        else:
            redacted[key] = value
    return redacted
