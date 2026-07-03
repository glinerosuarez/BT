from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from job_hunter.apply.adapters.greenhouse import GreenhouseAdapter
from job_hunter.apply.adapters.linkedin import LinkedInEasyApplyAdapter
from job_hunter.apply.browser import BrowserManager
from job_hunter.apply.profile_loader import load_application_inputs
from job_hunter.apply.resolver import AnswerResolver
from job_hunter.apply.types import ApplicationRunRecord, SubmitResult
from job_hunter.config import Settings
from job_hunter.notify import TelegramNotifier
from job_hunter.storage import JobStore
from job_hunter.tailoring import AnthropicTailoringProvider, TailoringService


class ApplicationService:
    def __init__(
        self,
        *,
        settings: Settings,
        store: JobStore,
        tailoring_service: TailoringService,
        browser_manager: BrowserManager | None = None,
        linkedin_adapter: LinkedInEasyApplyAdapter | None = None,
        greenhouse_adapter: GreenhouseAdapter | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.tailoring_service = tailoring_service
        self.browser_manager = browser_manager or BrowserManager(settings)
        self.linkedin_adapter = linkedin_adapter or LinkedInEasyApplyAdapter()
        self.greenhouse_adapter = greenhouse_adapter or GreenhouseAdapter()

    def submit_job(self, *, job_id: int, profile_name: str, force: bool = False) -> ApplicationRunRecord:
        job = self.store.get_job_for_application(job_id)
        if job is None:
            raise RuntimeError(f"Job id {job_id} not found.")
        if str(job["profile_match_label"] or "") != "pass" and not force:
            raise RuntimeError(f"Job id {job_id} is not eligible for auto-apply because profile_match_label is not 'pass'.")

        profile, answers = load_application_inputs(self.settings.tailoring_profile_root, profile_name)
        resolver = AnswerResolver(profile=profile, answers=answers)
        tailoring_artifact = self._ensure_tailoring_artifact(job_id=job_id, profile_name=profile_name, force=force)
        context = self._build_adapter_context(profile, tailoring_artifact["output_dir"])

        session = self.browser_manager.open(adapter_name="linkedin" if str(job["source"]) == "linkedin" else "greenhouse")
        try:
            page = session.new_page()
            initial_target_url = str(job["url"] or "").strip()
            page.goto(initial_target_url, wait_until="domcontentloaded")
            adapter_name, adapter, target_url = self._resolve_adapter(job, page, initial_target_url)

            duplicate = self.store.find_application_run(
                job_id=job_id,
                profile_name=profile_name,
                adapter_name=adapter_name,
                status="submitted",
            )
            if duplicate is not None and not force:
                run_id = self.store.create_application_run(
                    job_id=job_id,
                    profile_name=profile_name,
                    tailoring_artifact_id=int(tailoring_artifact["id"]),
                    adapter_name=adapter_name,
                    source=str(job["source"]),
                    target_url=target_url,
                    current_url=target_url,
                    status="skipped",
                    output_dir=str(self._output_dir(profile_name, "pending")),
                    blocked_reason="duplicate_submitted_run",
                )
                return self._run_record(run_id)

            run_id = self.store.create_application_run(
                job_id=job_id,
                profile_name=profile_name,
                tailoring_artifact_id=int(tailoring_artifact["id"]),
                adapter_name=adapter_name,
                source=str(job["source"]),
                target_url=target_url,
                current_url=getattr(page, "url", target_url),
                status="applying",
                output_dir=str(self._output_dir(profile_name, "pending")),
            )
            output_dir = self._output_dir(profile_name, str(run_id))
            output_dir.mkdir(parents=True, exist_ok=True)
            self.store.update_application_run(
                run_id,
                tailoring_artifact_id=int(tailoring_artifact["id"]),
                target_url=target_url,
                current_url=getattr(page, "url", target_url),
                increment_attempt_count=True,
                output_dir=str(output_dir),
            )
            result = adapter.submit(page=page, resolver=resolver, context=context)
            self._persist_result(run_id=run_id, result=result, output_dir=output_dir)
            self._maybe_notify(job=job, run_id=run_id, result=result)
            return self._run_record(run_id)
        finally:
            session.close()

    def resume(self, *, application_run_id: int) -> ApplicationRunRecord:
        row = self.store.get_application_run(application_run_id)
        if row is None:
            raise RuntimeError(f"Application id {application_run_id} not found.")
        if str(row["status"]) not in {"blocked", "failed"}:
            return self._run_record(application_run_id)
        return self.submit_job(job_id=int(row["job_id"]), profile_name=str(row["profile_name"]), force=True)

    def submit_batch(
        self,
        *,
        profile_name: str,
        limit: int,
        source: str | None = None,
        force: bool = False,
    ) -> list[ApplicationRunRecord]:
        rows = self.store.list_tailoring_candidates(limit=limit, source=source, label="pass")
        records: list[ApplicationRunRecord] = []
        for row in rows[: max(limit, 1)]:
            try:
                records.append(self.submit_job(job_id=int(row["id"]), profile_name=profile_name, force=force))
            except RuntimeError:
                continue
        return records

    def list_runs(self, *, status: str | None, limit: int) -> list[dict[str, object]]:
        rows = self.store.list_application_runs(status=status, limit=limit)
        return [
            {
                "application_id": int(row["id"]),
                "job_id": int(row["job_id"]),
                "company": str(row["company"]),
                "title": str(row["title"]),
                "profile_name": str(row["profile_name"]),
                "adapter_name": str(row["adapter_name"]),
                "status": str(row["status"]),
                "blocked_reason": str(row["blocked_reason"] or ""),
                "submitted_at": str(row["submitted_at"] or ""),
                "updated_at": str(row["updated_at"]),
            }
            for row in rows
        ]

    def show_run(self, application_run_id: int) -> dict[str, object]:
        row = self.store.get_application_run(application_run_id)
        if row is None:
            raise RuntimeError(f"Application id {application_run_id} not found.")
        steps = self.store.list_application_steps(application_run_id)
        return {
            "application_id": int(row["id"]),
            "job_id": int(row["job_id"]),
            "company": str(row["company"]),
            "title": str(row["title"]),
            "profile_name": str(row["profile_name"]),
            "adapter_name": str(row["adapter_name"]),
            "source": str(row["source"]),
            "target_url": str(row["target_url"]),
            "current_url": str(row["current_url"] or ""),
            "status": str(row["status"]),
            "attempt_count": int(row["attempt_count"]),
            "blocked_reason": str(row["blocked_reason"] or ""),
            "blocked_payload": self._decode_json_object(row["blocked_payload"]),
            "confirmation_payload": self._decode_json_object(row["confirmation_payload"]),
            "output_dir": str(row["output_dir"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "submitted_at": str(row["submitted_at"] or ""),
            "steps": [
                {
                    "step_key": str(step["step_key"]),
                    "step_label": str(step["step_label"]),
                    "status": str(step["status"]),
                    "field_name": str(step["field_name"] or ""),
                    "field_type": str(step["field_type"] or ""),
                    "question_text": str(step["question_text"] or ""),
                    "answer_source": str(step["answer_source"] or ""),
                    "answer_value": str(step["answer_value"] or ""),
                    "screenshot_path": str(step["screenshot_path"] or ""),
                    "payload": self._decode_json_object(step["payload_json"]),
                }
                for step in steps
            ],
        }

    def _ensure_tailoring_artifact(self, *, job_id: int, profile_name: str, force: bool) -> dict[str, object]:
        profile = self.tailoring_service.load_profile(profile_name)
        job_context = self.tailoring_service.build_job_context(job_id)
        artifact = self.store.find_tailoring_artifact(
            job_id=job_id,
            profile_name=profile_name,
            prompt_version="tailoring_v1",
            resume_source_hash=profile.resume_source_hash,
            cover_letter_source_hash=profile.cover_letter_source_hash,
            preferences_source_hash=profile.preferences_source_hash,
            job_context_hash=job_context.job_context_hash,
        )
        if artifact is None:
            generated = self.tailoring_service.generate_for_job(job_id=job_id, profile_name=profile_name, force=False)
            artifact = self.store.get_tailoring_artifact(generated.artifact_id)
        elif force and not Path(str(artifact["output_dir"])).exists():
            generated = self.tailoring_service.generate_for_job(job_id=job_id, profile_name=profile_name, force=False)
            artifact = self.store.get_tailoring_artifact(generated.artifact_id)
        if artifact is None:
            raise RuntimeError("No tailoring artifact available for application.")
        return {key: artifact[key] for key in artifact.keys()}

    def _build_adapter_context(self, profile, artifact_output_dir: str):
        from job_hunter.apply.adapters.base import AdapterContext

        output_dir = Path(artifact_output_dir)
        resume_pdf_path = output_dir / "resume.pdf"
        cover_letter_pdf_path = output_dir / "cover_letter.pdf"
        if not resume_pdf_path.exists():
            fallback = profile.uploads.get("resume", "")
            if fallback:
                resume_pdf_path = Path(fallback)
        if not cover_letter_pdf_path.exists():
            fallback = profile.uploads.get("cover_letter", "")
            if fallback:
                cover_letter_pdf_path = Path(fallback)
        if not resume_pdf_path.exists() or not cover_letter_pdf_path.exists():
            raise RuntimeError("Tailored resume/cover letter PDFs are required for apply flows.")
        return AdapterContext(
            resume_pdf_path=str(resume_pdf_path),
            cover_letter_pdf_path=str(cover_letter_pdf_path),
            output_dir=output_dir,
        )

    def _resolve_adapter(self, job, page, target_url: str):
        if str(job["source"]) == "linkedin":
            if self.linkedin_adapter.is_easy_apply_available(page):
                return "linkedin", self.linkedin_adapter, target_url
            external_url = self.linkedin_adapter.extract_external_apply_url(page)
            if external_url:
                page.goto(external_url, wait_until="domcontentloaded")
                target_url = external_url
            else:
                raise RuntimeError("LinkedIn job did not expose Easy Apply or a supported external apply link.")
        if self.greenhouse_adapter.is_greenhouse_target(target_url, page=page):
            return "greenhouse", self.greenhouse_adapter, target_url
        raise RuntimeError(f"Unsupported apply target for job source={job['source']} url={target_url}")

    def _persist_result(self, *, run_id: int, result: SubmitResult, output_dir: Path) -> None:
        self.store.update_application_run(
            run_id,
            current_url=result.current_url,
            status=result.status,
            blocked_reason=result.blocker.reason if result.blocker else None,
            blocked_payload=result.blocker.to_dict() if result.blocker else None,
            confirmation_payload=result.confirmation_payload if result.confirmation_payload else None,
            submitted_at=datetime.now(timezone.utc).isoformat() if result.status == "submitted" else None,
        )
        for step in result.steps:
            self.store.insert_application_step(
                application_run_id=run_id,
                step_key=step.step_key,
                step_label=step.step_label,
                status=step.status,
                field_name=step.field_name or None,
                field_type=step.field_type or None,
                question_text=step.question_text or None,
                answer_source=step.answer_source or None,
                answer_value=step.answer_value or None,
                screenshot_path=step.screenshot_path or None,
                payload_json=step.payload,
            )
        run_payload = self.show_run(run_id)
        (output_dir / "run.json").write_text(json.dumps(run_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if result.blocker is not None:
            (output_dir / "blocker.json").write_text(
                json.dumps(result.blocker.to_dict(), indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        if result.confirmation_payload:
            (output_dir / "confirmation.json").write_text(
                json.dumps(result.confirmation_payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

    def _run_record(self, run_id: int) -> ApplicationRunRecord:
        row = self.store.get_application_run(run_id)
        if row is None:
            raise RuntimeError(f"Application id {run_id} not found.")
        return ApplicationRunRecord(
            application_run_id=int(row["id"]),
            job_id=int(row["job_id"]),
            profile_name=str(row["profile_name"]),
            adapter_name=str(row["adapter_name"]),
            status=str(row["status"]),
            target_url=str(row["target_url"]),
            current_url=str(row["current_url"] or ""),
            output_dir=str(row["output_dir"]),
        )

    def _output_dir(self, profile_name: str, run_id: str) -> Path:
        return Path(self.settings.apply_output_root).expanduser() / profile_name / run_id

    def _decode_json_object(self, value: object) -> dict[str, object]:
        raw = str(value or "").strip()
        if not raw:
            return {}
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}

    def _maybe_notify(self, *, job, run_id: int, result: SubmitResult) -> None:
        if not self.settings.telegram_bot_token or not self.settings.telegram_chat_id:
            return
        notifier = TelegramNotifier(self.settings.telegram_bot_token, self.settings.telegram_chat_id)
        text = (
            f"[Application {result.status}] {job['title']}\n"
            f"Company: {job['company']}\n"
            f"Application ID: {run_id}\n"
            f"Adapter: {result.adapter_name}\n"
            f"URL: {result.current_url or job['url']}"
        )
        if result.blocker is not None:
            text += f"\nBlocker: {result.blocker.reason}"
        notifier.send_text(text)


def build_application_service(*, settings: Settings, store: JobStore) -> ApplicationService:
    if settings.tailoring_provider != "anthropic":
        raise RuntimeError(f"Unsupported tailoring provider: {settings.tailoring_provider}")
    provider = AnthropicTailoringProvider(model_name=settings.tailoring_anthropic_model or "")
    tailoring_service = TailoringService(settings=settings, store=store, provider=provider)
    return ApplicationService(settings=settings, store=store, tailoring_service=tailoring_service)
