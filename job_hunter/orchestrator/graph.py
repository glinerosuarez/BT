from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Callable

from job_hunter.apply.service import ApplicationService
from job_hunter.orchestrator.store import OrchestratorStore
from job_hunter.orchestrator.telemetry import PhoenixTelemetry
from job_hunter.orchestrator.types import WorkflowState
from job_hunter.tailoring.service import TailoringService


@dataclass
class WorkflowDependencies:
    store: OrchestratorStore
    orchestrator_agent: object
    writer_service: TailoringService
    applier_agent: object
    application_service: ApplicationService
    telemetry: PhoenixTelemetry
    profile_names: list[str]
    attempt_limit: int
    timezone_name: str
    notify: Callable[[str], object] | None = None
    manual_wait: Callable[[int], object] | None = None


class WorkflowGraph:
    def __init__(self, dependencies: WorkflowDependencies, *, checkpointer) -> None:
        self.dependencies = dependencies
        self.graph = self._build(checkpointer)

    def invoke(self, state: WorkflowState | object, *, thread_id: str):
        return self.graph.invoke(
            state,
            {"configurable": {"thread_id": thread_id}, "recursion_limit": 40},
        )

    def _build(self, checkpointer):
        try:
            from langgraph.graph import END, START, StateGraph
        except ModuleNotFoundError as exc:
            raise RuntimeError("LangGraph is not installed. Run `pip install -e .`.") from exc

        builder = StateGraph(WorkflowState)
        builder.add_node("orchestrator", self._orchestrator_node)
        builder.add_node("record_skip", self._record_skip_node)
        builder.add_node("writer", self._writer_node)
        builder.add_node("applier", self._applier_node)
        builder.add_node("assess_application", self._assess_application_node)
        builder.add_node("prepare_intervention", self._prepare_intervention_node)
        builder.add_node("human_intervention", self._human_intervention_node)
        builder.add_node("resume_application", self._resume_application_node)
        builder.add_node("human_skip", self._human_skip_node)
        builder.add_edge(START, "orchestrator")
        builder.add_conditional_edges(
            "orchestrator",
            self._route_decision,
            {"write": "writer", "skip": "record_skip", "end": END},
        )
        builder.add_conditional_edges("writer", self._route_writer, {"apply": "applier", "end": END})
        builder.add_conditional_edges(
            "applier",
            self._route_applier,
            {"assess": "assess_application", "end": END},
        )
        builder.add_conditional_edges(
            "assess_application",
            self._route_assessment,
            {"intervene": "prepare_intervention", "end": END},
        )
        builder.add_edge("prepare_intervention", "human_intervention")
        builder.add_conditional_edges(
            "human_intervention",
            self._route_human_resolution,
            {"resume": "resume_application", "skip": "human_skip", "end": END},
        )
        builder.add_edge("resume_application", "assess_application")
        builder.add_edge("record_skip", END)
        builder.add_edge("human_skip", END)
        return builder.compile(checkpointer=checkpointer, name="job-hunter-workflow")

    def _orchestrator_node(self, state: WorkflowState) -> WorkflowState:
        deps = self.dependencies
        workflow_id = int(state["workflow_id"])
        run_id = int(state["run_id"])
        deps.store.update_workflow(workflow_id, status="deciding")
        started = time.monotonic()
        with deps.telemetry.span(
            "agent.orchestrator",
            {
                "agent.role": "orchestrator",
                "workflow.id": workflow_id,
                "job.id": int(state["job_id"]),
                "profile_match.label": str(state["job"].get("profile_match_label") or ""),
            },
        ):
            decision = deps.orchestrator_agent.decide(
                job=state["job"],
                profile_names=deps.profile_names,
            )
        payload = decision.model_dump()
        deps.store.record_event(
            workflow_id=workflow_id,
            run_id=run_id,
            event_type="apply_decision",
            agent_role="orchestrator",
            status=decision.action,
            payload={
                "profile_match_label": decision.label_considered,
                "profile_name": decision.profile_name or "",
                "rationale": decision.rationale,
                "alternatives": decision.alternatives,
            },
            latency_ms=(time.monotonic() - started) * 1000,
        )
        deps.store.update_workflow(
            workflow_id,
            selected_profile=decision.profile_name,
            decision_rationale=decision.rationale,
            status="writing" if decision.action == "apply" else "skipped",
        )
        return {
            "decision": payload,
            "selected_profile": decision.profile_name or "",
            "status": "writing" if decision.action == "apply" else "skipped",
        }

    def _record_skip_node(self, state: WorkflowState) -> WorkflowState:
        self.dependencies.store.update_workflow(int(state["workflow_id"]), status="skipped")
        return {"status": "skipped"}

    def _writer_node(self, state: WorkflowState) -> WorkflowState:
        deps = self.dependencies
        workflow_id = int(state["workflow_id"])
        profile = str(state.get("selected_profile") or "")
        started = time.monotonic()
        try:
            with deps.telemetry.span(
                "agent.writer",
                {
                    "agent.role": "writer",
                    "workflow.id": workflow_id,
                    "job.id": int(state["job_id"]),
                    "profile.id": profile,
                },
            ):
                artifact = deps.writer_service.generate_for_job(
                    job_id=int(state["job_id"]),
                    profile_name=profile,
                    force=False,
                )
        except Exception as exc:
            deps.store.update_workflow(workflow_id, status="failed")
            deps.store.record_event(
                workflow_id=workflow_id,
                run_id=int(state["run_id"]),
                event_type="writing",
                agent_role="writer",
                status="failed",
                payload={"error_type": type(exc).__name__},
                latency_ms=(time.monotonic() - started) * 1000,
            )
            return {"status": "failed", "error": str(exc)}
        deps.store.update_workflow(
            workflow_id,
            status="applying",
            tailoring_artifact_id=artifact.artifact_id,
        )
        deps.store.record_event(
            workflow_id=workflow_id,
            run_id=int(state["run_id"]),
            event_type="writing",
            agent_role="writer",
            status="completed",
            payload={"artifact_id": artifact.artifact_id, "profile_name": profile},
            latency_ms=(time.monotonic() - started) * 1000,
        )
        return {"artifact_id": artifact.artifact_id, "status": "applying"}

    def _applier_node(self, state: WorkflowState) -> WorkflowState:
        deps = self.dependencies
        workflow_id = int(state["workflow_id"])
        if deps.store.attempts_today(deps.timezone_name) >= deps.attempt_limit:
            deps.store.update_workflow(workflow_id, status="queued")
            return {"status": "queued", "error": "daily_attempt_limit_reached"}
        deps.store.mark_attempt_started(workflow_id)
        started = time.monotonic()
        try:
            with deps.telemetry.span(
                "agent.applier.submit",
                {
                    "agent.role": "applier",
                    "workflow.id": workflow_id,
                    "job.id": int(state["job_id"]),
                    "profile.id": str(state["selected_profile"]),
                },
            ):
                run = deps.application_service.submit_job(
                    job_id=int(state["job_id"]),
                    profile_name=str(state["selected_profile"]),
                    force=False,
                )
        except Exception as exc:
            deps.store.update_workflow(workflow_id, status="failed")
            deps.store.record_event(
                workflow_id=workflow_id,
                run_id=int(state["run_id"]),
                event_type="application_attempt",
                agent_role="applier",
                status="failed_before_result",
                payload={"error_type": type(exc).__name__},
                latency_ms=(time.monotonic() - started) * 1000,
            )
            return {"status": "failed", "error": str(exc), "application_status": "failed"}
        blocker = self._load_blocker(run.application_run_id)
        deps.store.update_workflow(
            workflow_id,
            application_run_id=run.application_run_id,
            status=run.status,
            blocker_reason=str(blocker.get("reason") or "") or None,
        )
        deps.store.record_event(
            workflow_id=workflow_id,
            run_id=int(state["run_id"]),
            event_type="application_attempt",
            agent_role="applier",
            status=run.status,
            payload={
                "application_run_id": run.application_run_id,
                "adapter_name": run.adapter_name,
                "blocker_reason": str(blocker.get("reason") or ""),
            },
            latency_ms=(time.monotonic() - started) * 1000,
        )
        return {
            "application_run_id": run.application_run_id,
            "application_status": run.status,
            "status": run.status,
            "blocker": blocker,
        }

    def _assess_application_node(self, state: WorkflowState) -> WorkflowState:
        status = str(state.get("application_status") or state.get("status") or "")
        if status == "skipped":
            self.dependencies.store.update_workflow(int(state["workflow_id"]), status="skipped")
            return {"status": "skipped"}
        if status == "failed" and not state.get("application_run_id"):
            return {"status": "failed"}
        assessment = self.dependencies.applier_agent.assess(
            application_status=status,
            blocker=state.get("blocker", {}),
        )
        workflow_id = int(state["workflow_id"])
        if assessment.action == "accept" and status == "submitted":
            self.dependencies.store.update_workflow(workflow_id, status="submitted")
            return {"status": "submitted"}
        if assessment.action == "terminal":
            self.dependencies.store.update_workflow(workflow_id, status="failed")
            return {"status": "failed", "error": assessment.rationale}
        self.dependencies.store.update_workflow(workflow_id, status="blocked")
        blocker = dict(state.get("blocker", {}))
        blocker.setdefault("assessment", assessment.rationale)
        blocker.setdefault("kind", assessment.intervention_kind or blocker.get("reason") or "manual_review")
        return {"status": "blocked", "blocker": blocker}

    def _prepare_intervention_node(self, state: WorkflowState) -> WorkflowState:
        blocker = state.get("blocker", {})
        kind = str(blocker.get("kind") or blocker.get("reason") or "manual_review")
        prompt = (
            f"Application {state.get('application_run_id', '')} is blocked: {kind}. "
            "Reply with /open <id>, /retry <id>, or /skip <id>."
        )
        intervention_id = self.dependencies.store.create_intervention(
            workflow_id=int(state["workflow_id"]),
            application_run_id=int(state.get("application_run_id") or 0) or None,
            kind=kind,
            prompt=prompt,
        )
        if self.dependencies.notify is not None:
            self.dependencies.notify(f"[Intervention {intervention_id}] {prompt}")
        return {"intervention_id": intervention_id}

    def _human_intervention_node(self, state: WorkflowState) -> WorkflowState:
        try:
            from langgraph.types import interrupt
        except ModuleNotFoundError as exc:
            raise RuntimeError("LangGraph is not installed. Run `pip install -e .`.") from exc
        intervention_id = int(state["intervention_id"])
        resolution = interrupt(
            {
                "intervention_id": intervention_id,
                "workflow_id": int(state["workflow_id"]),
                "application_run_id": int(state.get("application_run_id") or 0),
                "blocker": state.get("blocker", {}),
                "allowed_actions": ["open", "retry", "skip"],
            }
        )
        normalized = resolution if isinstance(resolution, dict) else {"action": str(resolution)}
        self.dependencies.store.resolve_intervention(intervention_id, normalized)
        return {"intervention_resolution": normalized}

    def _resume_application_node(self, state: WorkflowState) -> WorkflowState:
        action = str(state.get("intervention_resolution", {}).get("action") or "retry")
        application_id = int(state.get("application_run_id") or 0)
        if action == "open" and self.dependencies.manual_wait is not None:
            try:
                run = self.dependencies.application_service.resume_with_manual_gate(
                    application_run_id=application_id,
                    notify=self.dependencies.notify,
                    wait_for_user=lambda: self.dependencies.manual_wait(int(state["intervention_id"])),
                )
            except RuntimeError as exc:
                if str(exc) != "manual_gate_skipped":
                    raise
                self.dependencies.store.update_workflow(int(state["workflow_id"]), status="skipped")
                return {"application_status": "skipped", "status": "skipped"}
        else:
            run = self.dependencies.application_service.resume(application_run_id=application_id)
        blocker = self._load_blocker(run.application_run_id)
        self.dependencies.store.update_workflow(
            int(state["workflow_id"]),
            application_run_id=run.application_run_id,
            status=run.status,
            blocker_reason=str(blocker.get("reason") or "") or None,
        )
        return {
            "application_run_id": run.application_run_id,
            "application_status": run.status,
            "status": run.status,
            "blocker": blocker,
        }

    def _human_skip_node(self, state: WorkflowState) -> WorkflowState:
        self.dependencies.store.update_workflow(int(state["workflow_id"]), status="skipped")
        return {"status": "skipped"}

    def _route_decision(self, state: WorkflowState) -> str:
        if state.get("error"):
            return "end"
        return "write" if state.get("decision", {}).get("action") == "apply" else "skip"

    def _route_writer(self, state: WorkflowState) -> str:
        return "end" if state.get("error") else "apply"

    def _route_applier(self, state: WorkflowState) -> str:
        if state.get("status") == "queued" or (
            state.get("error") and not state.get("application_run_id")
        ):
            return "end"
        return "assess"

    def _route_assessment(self, state: WorkflowState) -> str:
        return "intervene" if state.get("status") == "blocked" else "end"

    def _route_human_resolution(self, state: WorkflowState) -> str:
        action = str(state.get("intervention_resolution", {}).get("action") or "")
        if action in {"open", "retry", "continue"}:
            return "resume"
        if action == "skip":
            return "skip"
        return "end"

    def _load_blocker(self, application_run_id: int) -> dict[str, object]:
        row = self.dependencies.application_service.store.get_application_run(application_run_id)
        if row is None:
            return {}
        raw = str(row["blocked_payload"] or "").strip()
        payload = json.loads(raw) if raw else {}
        if not isinstance(payload, dict):
            payload = {}
        reason = str(row["blocked_reason"] or "").strip()
        if reason:
            payload.setdefault("reason", reason)
        return payload
