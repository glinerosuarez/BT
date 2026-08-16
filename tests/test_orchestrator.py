from __future__ import annotations

import socket
import sys
import tempfile
import types
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from job_hunter.apply.types import ApplicationRunRecord
from job_hunter.config import load_settings
from job_hunter.models import JobRecord
from job_hunter.orchestrator.agents import ApplicationWriterAgent, build_chat_model
from job_hunter.orchestrator.graph import WorkflowDependencies, WorkflowGraph
from job_hunter.orchestrator.service import OrchestratorService
from job_hunter.orchestrator.source_discovery import classify_source_url, validate_public_https_url
from job_hunter.orchestrator.store import OrchestratorStore
from job_hunter.orchestrator.telegram import _parse_command
from job_hunter.orchestrator.telemetry import PhoenixTelemetry
from job_hunter.orchestrator.types import ApplierAssessment, ApplyDecision, WriterOutput
from job_hunter.storage import JobStore
from job_hunter.tailoring.provider import build_tailoring_user_prompt, tailoring_system_prompt
from job_hunter.tailoring.types import (
    TailoringArtifactRecord,
    TailoringJobContext,
    TailoringProfile,
)


class _StructuredModel:
    def __init__(self, response) -> None:
        self.response = response
        self.schema = None
        self.messages = None

    def with_structured_output(self, schema):
        self.schema = schema
        return self

    def invoke(self, messages):
        self.messages = messages
        return self.response


class _OrchestratorAgent:
    def decide(self, *, job, profile_names):
        return ApplyDecision(
            action="apply",
            profile_name=profile_names[0],
            rationale="Eligible and aligned.",
            label_considered=job["profile_match_label"],
        )


class _WriterService:
    def generate_for_job(self, *, job_id, profile_name, force=False):
        return TailoringArtifactRecord(
            artifact_id=31,
            job_id=job_id,
            profile_name=profile_name,
            output_dir="/tmp/artifact",
            created=True,
            forced=force,
        )


class _ApplicationStore:
    def get_application_run(self, application_run_id):
        return None


class _ApplicationService:
    def __init__(self) -> None:
        self.store = _ApplicationStore()
        self.calls = 0

    def submit_job(self, *, job_id, profile_name, force=False):
        self.calls += 1
        return ApplicationRunRecord(
            application_run_id=41,
            job_id=job_id,
            profile_name=profile_name,
            adapter_name="greenhouse",
            status="submitted",
            target_url="https://example.com/apply",
            current_url="https://example.com/confirmation",
            output_dir="/tmp/application",
        )


class _BlockingApplicationStore:
    def get_application_run(self, application_run_id):
        if application_run_id == 51:
            return {
                "blocked_payload": "{}",
                "blocked_reason": "captcha",
            }
        return None


class _BlockingApplicationService:
    def __init__(self) -> None:
        self.store = _BlockingApplicationStore()
        self.resume_calls = 0

    def submit_job(self, *, job_id, profile_name, force=False):
        return ApplicationRunRecord(
            application_run_id=51,
            job_id=job_id,
            profile_name=profile_name,
            adapter_name="greenhouse",
            status="blocked",
            target_url="https://example.com/apply",
            current_url="https://example.com/apply",
            output_dir="/tmp/application",
        )

    def resume(self, *, application_run_id):
        self.resume_calls += 1
        return ApplicationRunRecord(
            application_run_id=52,
            job_id=1,
            profile_name="ml_eng_intern",
            adapter_name="greenhouse",
            status="submitted",
            target_url="https://example.com/apply",
            current_url="https://example.com/confirmation",
            output_dir="/tmp/application",
        )


class _ApplierAgent:
    def assess(self, *, application_status, blocker):
        if application_status == "submitted":
            return ApplierAssessment(action="accept", rationale="Explicit confirmation found.")
        return ApplierAssessment(
            action="intervene",
            rationale="Human action required.",
            intervention_kind=str(blocker.get("reason") or "manual_review"),
        )


class OrchestratorStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "jobs.db")
        self.jobs = JobStore(self.db_path)
        self.store = OrchestratorStore(self.db_path)

    def tearDown(self) -> None:
        self.store.close()
        self.jobs.close()
        self.tmp.cleanup()

    def test_new_jobs_only_and_profile_match_policy(self) -> None:
        old_id = self._insert_job("old-pass", label="pass")
        self.assertEqual(self.store.initialize(new_jobs_only=True), old_id)
        pass_id = self._insert_job("new-pass", label="pass", score=0.91)
        review_id = self._insert_job("new-review", label="review", score=0.75)
        self._insert_job("new-reject", label="reject", score=0.99)
        self._insert_job("policy-reject", label="pass", policy="reject")

        candidates = self.store.list_candidates(limit=20)

        self.assertEqual({int(row["id"]) for row in candidates}, {pass_id, review_id})

    def test_attempt_limit_counts_unique_jobs_per_local_day(self) -> None:
        self.store.initialize(new_jobs_only=False)
        run_id = self.store.create_run(trigger_name="test", attempt_limit=2, policy_snapshot={})
        first = self.store.create_workflow(
            job=self._job_row(self._insert_job("first", label="pass")),
            run_id=run_id,
        )
        second = self.store.create_workflow(
            job=self._job_row(self._insert_job("second", label="review")),
            run_id=run_id,
        )
        self.store.mark_attempt_started(int(first["id"]))
        self.store.mark_attempt_started(int(first["id"]))
        self.assertEqual(self.store.attempts_today("America/Bogota"), 1)
        self.store.mark_attempt_started(int(second["id"]))
        self.assertEqual(self.store.attempts_today("America/Bogota"), 2)

    def test_source_probe_lifecycle_and_rollback(self) -> None:
        source_id = self.store.upsert_source(
            source_type="greenhouse",
            source_value="example",
            provenance="tavily",
        )
        self.assertEqual(self.store.update_source_probe(source_id, success=True), "probing")
        self.assertEqual(self.store.update_source_probe(source_id, success=True), "active")
        self.assertEqual(self.store.update_source_probe(source_id, success=False, error="timeout"), "active")
        self.assertEqual(self.store.update_source_probe(source_id, success=False, error="timeout"), "quarantined")
        self.assertEqual(self.store.rollback_source(source_id), "active")

    def test_daemon_lease_is_exclusive_across_connections(self) -> None:
        other = OrchestratorStore(self.db_path)
        try:
            owner = self.store.acquire_lease(owner="first", ttl_seconds=60)
            self.assertEqual(owner, "first")
            self.assertIsNone(other.acquire_lease(owner="second", ttl_seconds=60))
            self.store.release_lease("first")
            self.assertEqual(other.acquire_lease(owner="second", ttl_seconds=60), "second")
        finally:
            other.close()

    def _insert_job(self, key: str, *, label: str, score: float = 0.8, policy: str = "pass") -> int:
        now = datetime.now(timezone.utc).isoformat()
        inserted = self.jobs.insert_job(
            JobRecord(
                source="greenhouse",
                external_id=key,
                url=f"https://example.com/{key}",
                title="Machine Learning Intern",
                company="Example",
                location="United States",
                is_internship=True,
                posted_at=now,
                description="Build data and machine learning systems.",
                ingested_at=now,
                relevance_score=8.0,
                eligibility_confidence=0.9,
                eligibility_status="eligible",
                policy_gate_status=policy,
                profile_match_score=score,
                profile_match_label=label,
                source_quality_status="detail_complete",
            ),
            dedupe_key=key,
        )
        self.assertTrue(inserted)
        row = self.jobs._conn.execute("SELECT id FROM jobs WHERE dedupe_key = ?", (key,)).fetchone()
        return int(row["id"])

    def _job_row(self, job_id: int) -> dict[str, object]:
        row = self.store._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return {key: row[key] for key in row.keys()}


class OrchestratorIntegrationTests(unittest.TestCase):
    def test_langgraph_happy_path_submits_and_marks_attempt(self) -> None:
        from langgraph.checkpoint.memory import InMemorySaver

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "jobs.db")
            jobs = JobStore(db_path)
            store = OrchestratorStore(db_path)
            now = datetime.now(timezone.utc).isoformat()
            jobs.insert_job(
                JobRecord(
                    source="greenhouse",
                    external_id="graph-job",
                    url="https://example.com/graph-job",
                    title="Data Intern",
                    company="Example",
                    location="US",
                    is_internship=True,
                    posted_at=now,
                    description="Data pipelines",
                    ingested_at=now,
                    relevance_score=7,
                    eligibility_confidence=0.9,
                    eligibility_status="eligible",
                    policy_gate_status="pass",
                    profile_match_score=0.85,
                    profile_match_label="review",
                    source_quality_status="detail_complete",
                ),
                dedupe_key="graph-job",
            )
            job_row = jobs._conn.execute("SELECT * FROM jobs WHERE dedupe_key = 'graph-job'").fetchone()
            job = {key: job_row[key] for key in job_row.keys()}
            run_id = store.create_run(trigger_name="test", attempt_limit=1, policy_snapshot={})
            workflow = store.create_workflow(job=job, run_id=run_id)
            application = _ApplicationService()
            graph = WorkflowGraph(
                WorkflowDependencies(
                    store=store,
                    orchestrator_agent=_OrchestratorAgent(),
                    writer_service=_WriterService(),
                    applier_agent=_ApplierAgent(),
                    application_service=application,
                    telemetry=PhoenixTelemetry(enabled=False),
                    profile_names=["ml_eng_intern"],
                    attempt_limit=1,
                    timezone_name="America/Bogota",
                ),
                checkpointer=InMemorySaver(),
            )

            result = graph.invoke(
                {
                    "workflow_id": int(workflow["id"]),
                    "run_id": run_id,
                    "thread_id": str(workflow["thread_id"]),
                    "job_id": int(job["id"]),
                    "job": job,
                    "status": "queued",
                },
                thread_id=str(workflow["thread_id"]),
            )

            self.assertEqual(result["status"], "submitted")
            self.assertEqual(application.calls, 1)
            self.assertEqual(store.attempts_today("America/Bogota"), 1)
            self.assertEqual(store.get_workflow(int(workflow["id"]))["status"], "submitted")
            store.close()
            jobs.close()

    def test_langgraph_interrupt_can_resume_from_checkpoint(self) -> None:
        from langgraph.checkpoint.memory import InMemorySaver
        from langgraph.types import Command

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "jobs.db")
            jobs = JobStore(db_path)
            store = OrchestratorStore(db_path)
            now = datetime.now(timezone.utc).isoformat()
            jobs.insert_job(
                JobRecord(
                    source="greenhouse",
                    external_id="blocked-job",
                    url="https://example.com/blocked-job",
                    title="ML Intern",
                    company="Example",
                    location="US",
                    is_internship=True,
                    posted_at=now,
                    description="ML systems",
                    ingested_at=now,
                    relevance_score=8,
                    eligibility_confidence=0.9,
                    eligibility_status="eligible",
                    policy_gate_status="pass",
                    profile_match_score=0.9,
                    profile_match_label="pass",
                    source_quality_status="detail_complete",
                ),
                dedupe_key="blocked-job",
            )
            row = jobs._conn.execute("SELECT * FROM jobs WHERE dedupe_key = 'blocked-job'").fetchone()
            job = {key: row[key] for key in row.keys()}
            run_id = store.create_run(trigger_name="test", attempt_limit=1, policy_snapshot={})
            workflow = store.create_workflow(job=job, run_id=run_id)
            application = _BlockingApplicationService()
            graph = WorkflowGraph(
                WorkflowDependencies(
                    store=store,
                    orchestrator_agent=_OrchestratorAgent(),
                    writer_service=_WriterService(),
                    applier_agent=_ApplierAgent(),
                    application_service=application,
                    telemetry=PhoenixTelemetry(enabled=False),
                    profile_names=["ml_eng_intern"],
                    attempt_limit=1,
                    timezone_name="America/Bogota",
                ),
                checkpointer=InMemorySaver(),
            )
            thread_id = str(workflow["thread_id"])
            paused = graph.invoke(
                {
                    "workflow_id": int(workflow["id"]),
                    "run_id": run_id,
                    "thread_id": thread_id,
                    "job_id": int(job["id"]),
                    "job": job,
                    "status": "queued",
                },
                thread_id=thread_id,
            )
            intervention = store.list_interventions(status="pending")[0]

            resumed = graph.invoke(Command(resume={"action": "retry"}), thread_id=thread_id)

            self.assertIn("__interrupt__", paused)
            self.assertEqual(resumed["status"], "submitted")
            self.assertEqual(application.resume_calls, 1)
            self.assertEqual(store.get_intervention(int(intervention["id"]))["status"], "resolved")
            store.close()
            jobs.close()

    def test_initialize_reports_only_complete_profiles_as_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict("os.environ", {}, clear=True):
            root = Path(tmpdir) / "profiles"
            complete = root / "complete"
            incomplete = root / "incomplete"
            complete.mkdir(parents=True)
            incomplete.mkdir(parents=True)
            for filename in (
                "resume.md",
                "cover_letter.md",
                "application_profile.json",
                "application_answers.json",
            ):
                (complete / filename).write_text("{}", encoding="utf-8")
            (incomplete / "resume.md").write_text("resume", encoding="utf-8")
            settings = replace(
                load_settings(),
                db_path=str(Path(tmpdir) / "jobs.db"),
                tailoring_profile_root=str(root),
                orchestrator_profiles=["complete", "incomplete"],
                phoenix_enabled=False,
                greenhouse_boards=[],
                lever_companies=[],
                rss_feeds=[],
                github_repo_readmes=[],
                ashby_boards=[],
            )
            service = OrchestratorService(settings=settings)
            outcome = service.initialize()
            self.assertEqual(outcome["ready_profiles"], ["complete"])
            self.assertIn("cover_letter.md", outcome["profile_readiness"]["incomplete"])
            service.close()


class OrchestratorAgentAndInputTests(unittest.TestCase):
    def test_nvidia_nim_builds_openai_compatible_structured_model(self) -> None:
        created: list[dict[str, object]] = []
        structured_calls: list[tuple[object, dict[str, object]]] = []

        class FakeChatOpenAI:
            def __init__(self, **kwargs) -> None:
                created.append(kwargs)

            def with_structured_output(self, schema, **kwargs):
                structured_calls.append((schema, kwargs))
                return "structured-model"

        module = types.ModuleType("langchain_openai")
        module.ChatOpenAI = FakeChatOpenAI
        with patch.dict(sys.modules, {"langchain_openai": module}), patch.dict(
            "os.environ", {}, clear=True
        ):
            settings = replace(
                load_settings(),
                nvidia_api_key="nvapi-test",
                nvidia_nim_base_url="https://integrate.api.nvidia.com/v1/",
            )
            model = build_chat_model(
                provider="nvidia_nim",
                model_name="meta/llama-3.1-70b-instruct",
                settings=settings,
            )

        self.assertEqual(created[0]["base_url"], "https://integrate.api.nvidia.com/v1")
        self.assertEqual(created[0]["api_key"], "nvapi-test")
        self.assertEqual(created[0]["model"], "meta/llama-3.1-70b-instruct")
        self.assertEqual(model.with_structured_output(ApplyDecision), "structured-model")
        self.assertEqual(structured_calls, [(ApplyDecision, {"method": "json_schema"})])

    def test_nvidia_nim_requires_api_key(self) -> None:
        module = types.ModuleType("langchain_openai")
        module.ChatOpenAI = object
        with patch.dict(sys.modules, {"langchain_openai": module}), patch.dict(
            "os.environ", {}, clear=True
        ):
            settings = replace(load_settings(), nvidia_api_key=None)
            with self.assertRaisesRegex(RuntimeError, "NVIDIA_API_KEY"):
                build_chat_model(
                    provider="nim",
                    model_name="meta/llama-3.1-8b-instruct",
                    settings=settings,
                )

    def test_application_writer_uses_exact_existing_tailoring_prompts(self) -> None:
        output = WriterOutput(
            resume_markdown="# Resume",
            cover_letter_markdown="# Letter",
            highlight_requirements=["Python"],
            evidence_map=[{"job_requirement": "Python", "profile_evidence": "Built Python tools"}],
        )
        model = _StructuredModel(output)
        agent = ApplicationWriterAgent(model, provider_name="anthropic", model_name="test-model")
        profile = TailoringProfile(
            profile_name="test",
            profile_dir="/tmp/test",
            resume_markdown="# Source resume",
            cover_letter_markdown="# Source letter",
            preferences_markdown="Concise",
            shared_preferences_markdown="Concise",
            profile_preferences_markdown="",
            resume_source_hash="r",
            cover_letter_source_hash="c",
            preferences_source_hash="p",
        )
        context = TailoringJobContext(
            job_id=1,
            source="greenhouse",
            title="ML Intern",
            company="Example",
            location="US",
            posted_at="2026-07-30",
            url="https://example.com/job",
            description="Build ML systems",
            company_context="Example builds tools",
            job_text_version="v1",
            job_text_snapshot="snapshot",
            profile_match_label="pass",
            profile_match_score=0.9,
            job_context_hash="j",
        )

        agent.generate(profile=profile, job_context=context)

        self.assertEqual(model.messages[0], ("system", tailoring_system_prompt()))
        self.assertEqual(
            model.messages[1],
            ("human", build_tailoring_user_prompt(profile=profile, job_context=context)),
        )

    def test_source_classification_and_ssrf_guard(self) -> None:
        source = classify_source_url("https://boards.greenhouse.io/example/jobs/123", query="internships")
        self.assertEqual((source.source_type, source.source_value), ("greenhouse", "example"))

        def local_resolver(host, port, *, type):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]

        valid, reason = validate_public_https_url("https://example.com/feed.xml", resolver=local_resolver)
        self.assertFalse(valid)
        self.assertEqual(reason, "non_public_address")

    def test_telegram_command_parser(self) -> None:
        command = _parse_command("/retry 42", update_id=7)
        self.assertEqual((command.action, command.target_id), ("retry", 42))
        self.assertIsNone(_parse_command("/retry", update_id=8))
        self.assertEqual(_parse_command("/status", update_id=9).action, "status")


if __name__ == "__main__":
    unittest.main()
