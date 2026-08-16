from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from dataclasses import asdict, replace
from pathlib import Path

from job_hunter.apply.email_codes import GmailVerificationCodeClient
from job_hunter.apply.service import ApplicationService
from job_hunter.config import Settings
from job_hunter.notify import TelegramNotifier
from job_hunter.orchestrator.agents import build_default_agents
from job_hunter.orchestrator.graph import WorkflowDependencies, WorkflowGraph
from job_hunter.orchestrator.source_discovery import (
    TavilySourceDiscovery,
    probe_source,
    seed_source_registry,
)
from job_hunter.orchestrator.store import OrchestratorStore
from job_hunter.orchestrator.telegram import TelegramController
from job_hunter.orchestrator.telemetry import PhoenixTelemetry, configure_phoenix
from job_hunter.pipeline import run_pipeline
from job_hunter.storage import JobStore, ensure_parent_dir
from job_hunter.tailoring.service import TailoringService

LOG = logging.getLogger(__name__)


class OrchestratorService:
    def __init__(
        self,
        *,
        settings: Settings,
        store: OrchestratorStore | None = None,
        job_store: JobStore | None = None,
        agents: tuple[object, object, object, object] | None = None,
        telemetry: PhoenixTelemetry | None = None,
        checkpointer=None,
        pipeline_runner=run_pipeline,
    ) -> None:
        self.settings = settings
        ensure_parent_dir(settings.db_path)
        self.store = store or OrchestratorStore(settings.db_path)
        self.job_store = job_store or JobStore(settings.db_path)
        self._provided_agents = agents
        self.telemetry = telemetry or configure_phoenix(settings)
        self.pipeline_runner = pipeline_runner
        self._checkpoint_connection: sqlite3.Connection | None = None
        self._checkpointer = checkpointer
        self._workflow_graph: WorkflowGraph | None = None
        self._sourcing_agent = None
        self._runtime_profiles: list[str] = []
        self.telegram = self._build_telegram()

    def initialize(self) -> dict[str, object]:
        baseline = self.store.initialize(new_jobs_only=self.settings.orchestrator_new_jobs_only)
        seeded = seed_source_registry(self.settings, self.store)
        profile_readiness = self._profile_readiness()
        return {
            "baseline_job_id": baseline,
            "new_jobs_only": self.settings.orchestrator_new_jobs_only,
            "seeded_sources": seeded,
            "profiles": self.settings.orchestrator_profiles,
            "ready_profiles": [name for name, missing in profile_readiness.items() if not missing],
            "profile_readiness": profile_readiness,
        }

    def run_cycle(self, *, trigger_name: str = "once", attempt_limit: int | None = None) -> dict[str, object]:
        self.initialize()
        limit = self.settings.orchestrator_daily_attempt_limit if attempt_limit is None else max(attempt_limit, 0)
        profile_readiness = self._profile_readiness()
        ready_profiles = [name for name, missing in profile_readiness.items() if not missing]
        if not ready_profiles:
            details = "; ".join(
                f"{name}: {', '.join(missing)}" for name, missing in profile_readiness.items()
            )
            raise RuntimeError(f"No end-to-end-ready orchestrator profile is configured ({details})")
        lease_owner = self.store.acquire_lease(owner=uuid.uuid4().hex, ttl_seconds=max(self.settings.poll_interval_minutes * 120, 180))
        if lease_owner is None:
            raise RuntimeError("Another orchestrator daemon currently holds the lease")
        policy = {
            "allowed_profile_match_labels": ["pass", "review"],
            "profiles": ready_profiles,
            "daily_attempt_limit": limit,
            "timezone": self.settings.orchestrator_timezone,
            "new_jobs_only": self.settings.orchestrator_new_jobs_only,
        }
        run_id = self.store.create_run(
            trigger_name=trigger_name,
            attempt_limit=limit,
            policy_snapshot=policy,
        )
        processed: list[dict[str, object]] = []
        try:
            self._ensure_runtime(attempt_limit=limit)
            source_outcome = self._run_sourcing(run_id=run_id)
            remaining = max(
                limit - self.store.attempts_today(self.settings.orchestrator_timezone),
                0,
            )
            queued = self.store.list_queued_workflows(limit=max(remaining, 1)) if remaining else []
            seen_job_ids = {int(row["id"]) for row in queued}
            candidate_limit = max(remaining * 3, 10) if remaining else 1
            candidates = self.store.list_candidates(limit=candidate_limit) if remaining else []
            work: list[tuple[dict[str, object], dict[str, object]]] = []
            for row in queued:
                workflow = self.store.get_workflow(int(row["workflow_id"])) or {}
                self.store.update_workflow(int(row["workflow_id"]), run_id=run_id)
                work.append((row, workflow))
            for job in candidates:
                if int(job["id"]) in seen_job_ids:
                    continue
                workflow = self.store.create_workflow(job=job, run_id=run_id)
                work.append((job, workflow))
            for job, workflow in work:
                if self.store.attempts_today(self.settings.orchestrator_timezone) >= limit:
                    break
                workflow_id = int(workflow["id"])
                thread_id = str(workflow["thread_id"])
                initial_state = {
                    "workflow_id": workflow_id,
                    "run_id": run_id,
                    "thread_id": thread_id,
                    "job_id": int(job["id"]),
                    "job": job,
                    "profile_names": ready_profiles,
                    "attempt_limit": limit,
                    "status": "queued",
                    "events": [],
                }
                with self.telemetry.span(
                    "workflow.job",
                    {
                        "workflow.id": workflow_id,
                        "orchestrator.run_id": run_id,
                        "job.id": int(job["id"]),
                        "job.source": str(job.get("source") or ""),
                        "profile_match.label": str(job.get("profile_match_label") or ""),
                    },
                ):
                    result = self._workflow_graph.invoke(initial_state, thread_id=thread_id)
                processed.append(
                    {
                        "workflow_id": workflow_id,
                        "job_id": int(job["id"]),
                        "status": str(result.get("status") or "interrupted"),
                        "interrupted": bool(result.get("__interrupt__")),
                    }
                )
            self.store.finish_run(run_id, status="completed")
            return {
                "run_id": run_id,
                "source_outcome": source_outcome,
                "processed": processed,
                "attempt_limit": limit,
                "attempts_today": self.store.attempts_today(self.settings.orchestrator_timezone),
            }
        except Exception as exc:
            self.store.finish_run(run_id, status="failed", error=f"{type(exc).__name__}: {exc}")
            raise
        finally:
            self.store.release_lease(lease_owner)

    def resume_intervention(self, intervention_id: int, *, action: str) -> dict[str, object]:
        if action not in {"open", "retry", "continue", "skip"}:
            raise RuntimeError(f"Unsupported intervention action: {action}")
        row = self.store.get_intervention(intervention_id)
        if row is None:
            raise RuntimeError(f"Intervention id {intervention_id} not found")
        if str(row["status"]) != "pending":
            return {"intervention_id": intervention_id, "status": str(row["status"]), "idempotent": True}
        self._ensure_runtime(attempt_limit=self.settings.orchestrator_daily_attempt_limit)
        try:
            from langgraph.types import Command
        except ModuleNotFoundError as exc:
            raise RuntimeError("LangGraph is not installed. Run `pip install -e .`.") from exc
        result = self._workflow_graph.invoke(
            Command(resume={"action": action}),
            thread_id=str(row["thread_id"]),
        )
        return {
            "intervention_id": intervention_id,
            "workflow_id": int(row["workflow_id"]),
            "status": str(result.get("status") or "interrupted"),
            "interrupted": bool(result.get("__interrupt__")),
        }

    def process_telegram(self, *, long_poll_seconds: int = 0) -> list[dict[str, object]]:
        if self.telegram is None:
            return []
        outcomes: list[dict[str, object]] = []
        for command in self.telegram.poll(long_poll_seconds=long_poll_seconds):
            if command.action == "status":
                payload = self.status()
                self.telegram.send_text(json.dumps(payload, sort_keys=True))
                outcomes.append({"action": "status"})
                continue
            try:
                outcome = self.resume_intervention(int(command.target_id or 0), action=command.action)
                self.telegram.send_text(json.dumps(outcome, sort_keys=True))
                outcomes.append(outcome)
            except RuntimeError as exc:
                self.telegram.send_text(f"Command failed: {exc}")
                outcomes.append({"action": command.action, "error": str(exc)})
        return outcomes

    def run_forever(self, *, attempt_limit: int | None = None) -> None:
        interval_seconds = max(self.settings.poll_interval_minutes, 1) * 60
        next_cycle_at = 0.0
        while True:
            now = time.monotonic()
            if now >= next_cycle_at:
                try:
                    self.run_cycle(trigger_name="daemon", attempt_limit=attempt_limit)
                except Exception:
                    LOG.exception("orchestrator_cycle_failed")
                next_cycle_at = time.monotonic() + interval_seconds
            try:
                self.process_telegram(long_poll_seconds=10)
            except Exception:
                LOG.exception("telegram_poll_failed")
                time.sleep(5)

    def status(self) -> dict[str, object]:
        return {
            "initialized": self.store.get_state("initialized_at") is not None,
            "baseline_job_id": self.store.get_state("baseline_job_id"),
            "attempt_limit": self.settings.orchestrator_daily_attempt_limit,
            "attempts_today": self.store.attempts_today(self.settings.orchestrator_timezone),
            "pending_interventions": self.store.list_interventions(status="pending"),
            "phoenix_enabled": self.telemetry.enabled,
        }

    def report(self, *, days: int) -> dict[str, object]:
        return self.store.report(days=days)

    def close(self) -> None:
        self.telemetry.shutdown()
        if self._checkpoint_connection is not None:
            self._checkpoint_connection.close()
        self.job_store.close()
        self.store.close()

    def _ensure_runtime(self, *, attempt_limit: int) -> None:
        if self._workflow_graph is not None:
            self._workflow_graph.dependencies.attempt_limit = attempt_limit
            return
        self._runtime_profiles = [
            name for name, missing in self._profile_readiness().items() if not missing
        ]
        if not self._runtime_profiles:
            raise RuntimeError("No end-to-end-ready orchestrator profile is configured")
        agents = self._provided_agents or build_default_agents(self.settings)
        orchestrator_agent, sourcing_agent, writer_agent, applier_agent = agents
        self._sourcing_agent = sourcing_agent
        writer_service = TailoringService(
            settings=self.settings,
            store=self.job_store,
            provider=writer_agent,
        )
        email_client = (
            GmailVerificationCodeClient(self.settings)
            if self.settings.apply_gmail_verification_enabled
            else None
        )
        application_service = ApplicationService(
            settings=self.settings,
            store=self.job_store,
            tailoring_service=writer_service,
            email_code_client=email_client,
            auto_apply_labels=frozenset({"pass", "review"}),
        )
        checkpointer = self._checkpointer or self._build_checkpointer()
        self._workflow_graph = WorkflowGraph(
            WorkflowDependencies(
                store=self.store,
                orchestrator_agent=orchestrator_agent,
                writer_service=writer_service,
                applier_agent=applier_agent,
                application_service=application_service,
                telemetry=self.telemetry,
                profile_names=self._runtime_profiles,
                attempt_limit=attempt_limit,
                timezone_name=self.settings.orchestrator_timezone,
                notify=self.telegram.send_text if self.telegram is not None else None,
                manual_wait=self.telegram.wait_for_gate if self.telegram is not None else None,
            ),
            checkpointer=checkpointer,
        )

    def _build_checkpointer(self):
        try:
            from langgraph.checkpoint.sqlite import SqliteSaver
        except ModuleNotFoundError as exc:
            raise RuntimeError("langgraph-checkpoint-sqlite is not installed. Run `pip install -e .`.") from exc
        path = Path(self.settings.orchestrator_checkpoint_db_path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._checkpoint_connection = sqlite3.connect(path, check_same_thread=False)
        saver = SqliteSaver(self._checkpoint_connection)
        saver.setup()
        return saver

    def _build_telegram(self) -> TelegramController | None:
        if not self.settings.telegram_bot_token or not self.settings.telegram_chat_id:
            return None
        return TelegramController(
            bot_token=self.settings.telegram_bot_token,
            chat_id=self.settings.telegram_chat_id,
            store=self.store,
            timeout_seconds=self.settings.request_timeout_seconds,
        )

    def _run_sourcing(self, *, run_id: int) -> dict[str, object]:
        if self._sourcing_agent is None:
            raise RuntimeError("Sourcing agent is not initialized")
        summary = self.store.report(days=7)
        with self.telemetry.span("agent.sourcing", {"agent.role": "sourcing", "orchestrator.run_id": run_id}):
            plan = self._sourcing_agent.plan(operational_summary=summary)
        self.store.record_event(
            workflow_id=None,
            run_id=run_id,
            event_type="sourcing_plan",
            agent_role="sourcing",
            status="completed",
            payload={"queries": plan.search_queries, "source_types": plan.source_types, "rationale": plan.rationale},
        )
        discovery = TavilySourceDiscovery(settings=self.settings, store=self.store)
        found = discovery.discover(plan.search_queries)
        selected_types = set(plan.source_types)
        accepted = [item for item in found if not selected_types or item.source_type in selected_types]
        for item in accepted:
            self.store.upsert_source(
                source_type=item.source_type,
                source_value=item.source_value,
                provenance="tavily",
                discovery_query=item.query,
                rationale=f"discovered from {item.result_url}",
            )
        probed = 0
        for source in self.store.list_sources_for_probe(
            limit=self.settings.source_probe_limit_per_run
        ):
            success, error = probe_source(
                str(source["source_type"]),
                str(source["source_value"]),
                timeout_seconds=self.settings.request_timeout_seconds,
            )
            self.store.update_source_probe(int(source["id"]), success=success, error=error)
            probed += 1
        effective_settings = self._settings_from_registry()
        notifier = None
        if effective_settings.telegram_bot_token and effective_settings.telegram_chat_id:
            notifier = TelegramNotifier(
                effective_settings.telegram_bot_token,
                effective_settings.telegram_chat_id,
                effective_settings.request_timeout_seconds,
            )
        outcome = self.pipeline_runner(effective_settings, self.job_store, notifier)
        return {
            "plan": plan.model_dump(),
            "discovered_count": len(accepted),
            "probed_count": probed,
            "pipeline": asdict(outcome),
        }

    def _settings_from_registry(self) -> Settings:
        active = self.store.list_sources(status="active", limit=5000)
        grouped: dict[str, list[str]] = {}
        for row in active:
            grouped.setdefault(str(row["source_type"]), []).append(str(row["source_value"]))
        return replace(
            self.settings,
            greenhouse_boards=grouped.get("greenhouse", []),
            lever_companies=grouped.get("lever", []),
            rss_feeds=grouped.get("rss", []),
            github_repo_readmes=grouped.get("github_repo", []),
            ashby_boards=grouped.get("ashby", []),
        )

    def _profile_readiness(self) -> dict[str, list[str]]:
        root = Path(self.settings.tailoring_profile_root).expanduser()
        required = (
            "resume.md",
            "cover_letter.md",
            "application_profile.json",
            "application_answers.json",
        )
        readiness: dict[str, list[str]] = {}
        for name in self.settings.orchestrator_profiles:
            profile_dir = root / name
            readiness[name] = [filename for filename in required if not (profile_dir / filename).is_file()]
        return readiness
