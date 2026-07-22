from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from job_hunter.apply.adapters.greenhouse import GreenhouseAdapter
from job_hunter.apply.adapters.handshake import HandshakeAdapter
from job_hunter.apply.adapters.handshake_fellow import HandshakeFellowAdapter
from job_hunter.apply.adapters.icims import ICIMSAdapter
from job_hunter.apply.adapters.linkedin import LinkedInEasyApplyAdapter
from job_hunter.apply.adapters.workday import WorkdayAdapter
from job_hunter.apply.browser import BrowserManager
from job_hunter.apply.email_codes import GmailVerificationCodeClient
from job_hunter.apply.profile_loader import load_application_inputs
from job_hunter.apply.resolver import AnswerResolver
from job_hunter.apply.types import ApplicationRunRecord, Blocker, StepSnapshot, SubmitResult
from job_hunter.config import Settings
from job_hunter.notify import TelegramNotifier
from job_hunter.storage import JobStore
from job_hunter.tailoring import AnthropicTailoringProvider, TailoringService


class UnsupportedApplyTargetError(RuntimeError):
    def __init__(
        self,
        *,
        source: str,
        target_url: str,
        current_url: str,
        blocker_reason: str = "unsupported_portal",
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(f"Unsupported apply target for job source={source} url={target_url}")
        self.source = source
        self.target_url = target_url
        self.current_url = current_url
        self.blocker_reason = blocker_reason
        self.details = details or {}


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
        handshake_adapter: HandshakeAdapter | None = None,
        handshake_fellow_adapter: HandshakeFellowAdapter | None = None,
        icims_adapter: ICIMSAdapter | None = None,
        workday_adapter: WorkdayAdapter | None = None,
        email_code_client: GmailVerificationCodeClient | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.tailoring_service = tailoring_service
        self.browser_manager = browser_manager or BrowserManager(settings)
        self.linkedin_adapter = linkedin_adapter or LinkedInEasyApplyAdapter()
        self.greenhouse_adapter = greenhouse_adapter or GreenhouseAdapter()
        self.handshake_adapter = handshake_adapter or HandshakeAdapter()
        self.handshake_fellow_adapter = handshake_fellow_adapter or HandshakeFellowAdapter()
        self.icims_adapter = icims_adapter or ICIMSAdapter()
        self.workday_adapter = workday_adapter or WorkdayAdapter()
        self.email_code_client = email_code_client

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

        session = self.browser_manager.open(adapter_name=self._session_adapter_name_for_source(str(job["source"] or "")))
        try:
            page = session.new_page()
            initial_target_url = self._initial_target_url(job)
            page.goto(initial_target_url, wait_until="domcontentloaded")
            try:
                adapter_name, adapter, target_url = self._resolve_adapter(job, page, initial_target_url)
            except UnsupportedApplyTargetError as exc:
                run_id = self.store.create_application_run(
                    job_id=job_id,
                    profile_name=profile_name,
                    tailoring_artifact_id=int(tailoring_artifact["id"]),
                    adapter_name="unsupported",
                    source=str(job["source"]),
                    target_url=exc.target_url,
                    current_url=exc.current_url,
                    status="applying",
                    output_dir=str(self._output_dir(profile_name, "pending")),
                )
                output_dir = self._output_dir(profile_name, str(run_id))
                output_dir.mkdir(parents=True, exist_ok=True)
                self.store.update_application_run(
                    run_id,
                    tailoring_artifact_id=int(tailoring_artifact["id"]),
                    target_url=exc.target_url,
                    current_url=exc.current_url,
                    increment_attempt_count=True,
                    output_dir=str(output_dir),
                )
                result = SubmitResult(
                    status="blocked",
                    current_url=exc.current_url,
                    blocker=Blocker(
                        reason=exc.blocker_reason,
                        question_text="Application target",
                        field_name="target_url",
                        field_type="url",
                        details={
                            "source": exc.source,
                            "target_url": exc.target_url,
                            **exc.details,
                        },
                    ),
                    adapter_name="unsupported",
                    target_url=exc.target_url,
                )
                self._persist_result(run_id=run_id, result=result, output_dir=output_dir, page=page)
                return self._run_record(run_id)

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
            submit_started_at = datetime.now(timezone.utc)
            result = adapter.submit(page=page, resolver=resolver, context=context)
            result = self._maybe_complete_email_verification(
                adapter_name=adapter_name,
                adapter=adapter,
                page=page,
                result=result,
                context=context,
                resolver=resolver,
                recipient_email=profile.identity.email,
                submit_started_at=submit_started_at,
            )
            self._persist_result(run_id=run_id, result=result, output_dir=output_dir, page=page)
            self._maybe_notify(job=job, run_id=run_id, result=result)
            return self._run_record(run_id)
        finally:
            session.close()

    def handoff_job(
        self,
        *,
        job_id: int,
        profile_name: str,
        force: bool = False,
        notify: callable | None = None,
        wait_for_user: callable | None = None,
    ) -> ApplicationRunRecord:
        job = self.store.get_job_for_application(job_id)
        if job is None:
            raise RuntimeError(f"Job id {job_id} not found.")
        if str(job["profile_match_label"] or "") != "pass" and not force:
            raise RuntimeError(f"Job id {job_id} is not eligible for auto-apply because profile_match_label is not 'pass'.")

        profile, answers = load_application_inputs(self.settings.tailoring_profile_root, profile_name)
        resolver = AnswerResolver(profile=profile, answers=answers)
        tailoring_artifact = self._ensure_tailoring_artifact(job_id=job_id, profile_name=profile_name, force=force)
        context = self._build_adapter_context(profile, tailoring_artifact["output_dir"])

        session = self.browser_manager.open(
            adapter_name=self._session_adapter_name_for_source(str(job["source"] or "")),
            headless=False,
        )
        try:
            page = session.new_page()
            initial_target_url = self._initial_target_url(job)
            page.goto(initial_target_url, wait_until="domcontentloaded")
            try:
                adapter_name, adapter, target_url = self._resolve_adapter(job, page, initial_target_url)
            except UnsupportedApplyTargetError as exc:
                run_id = self.store.create_application_run(
                    job_id=job_id,
                    profile_name=profile_name,
                    tailoring_artifact_id=int(tailoring_artifact["id"]),
                    adapter_name="unsupported",
                    source=str(job["source"]),
                    target_url=exc.target_url,
                    current_url=exc.current_url,
                    status="applying",
                    output_dir=str(self._output_dir(profile_name, "pending")),
                )
                output_dir = self._output_dir(profile_name, str(run_id))
                output_dir.mkdir(parents=True, exist_ok=True)
                self.store.update_application_run(
                    run_id,
                    tailoring_artifact_id=int(tailoring_artifact["id"]),
                    target_url=exc.target_url,
                    current_url=exc.current_url,
                    increment_attempt_count=True,
                    output_dir=str(output_dir),
                )
                result = SubmitResult(
                    status="blocked",
                    current_url=exc.current_url,
                    blocker=Blocker(
                        reason=exc.blocker_reason,
                        question_text="Application target",
                        field_name="target_url",
                        field_type="url",
                        details={
                            "source": exc.source,
                            "target_url": exc.target_url,
                            **exc.details,
                        },
                    ),
                    adapter_name="unsupported",
                    target_url=exc.target_url,
                )
                self._persist_result(run_id=run_id, result=result, output_dir=output_dir, page=page)
                return self._run_record(run_id)

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
            if notify is not None:
                notify(self._handoff_message(job=job, adapter_name=adapter_name, run_id=run_id, target_url=target_url))
            if wait_for_user is not None:
                wait_for_user()
            submit_started_at = datetime.now(timezone.utc)
            result = adapter.submit(page=page, resolver=resolver, context=context)
            result = self._maybe_complete_email_verification(
                adapter_name=adapter_name,
                adapter=adapter,
                page=page,
                result=result,
                context=context,
                resolver=resolver,
                recipient_email=profile.identity.email,
                submit_started_at=submit_started_at,
            )
            self._persist_result(run_id=run_id, result=result, output_dir=output_dir, page=page)
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

    def resume_with_manual_gate(
        self,
        *,
        application_run_id: int,
        notify: callable | None = None,
        wait_for_user: callable | None = None,
    ) -> ApplicationRunRecord:
        row = self.store.get_application_run(application_run_id)
        if row is None:
            raise RuntimeError(f"Application id {application_run_id} not found.")
        job_id = int(row["job_id"])
        profile_name = str(row["profile_name"])
        adapter_name = str(row["adapter_name"] or "").strip()
        if not adapter_name:
            raise RuntimeError(f"Application id {application_run_id} does not have a stored adapter name.")
        job = self.store.get_job_for_application(job_id)
        if job is None:
            raise RuntimeError(f"Job id {job_id} not found.")

        profile, answers = load_application_inputs(self.settings.tailoring_profile_root, profile_name)
        resolver = AnswerResolver(profile=profile, answers=answers)
        tailoring_artifact = None
        stored_artifact_id = int(row["tailoring_artifact_id"] or 0)
        if stored_artifact_id:
            stored_artifact = self.store.get_tailoring_artifact(stored_artifact_id)
            if stored_artifact is not None and Path(str(stored_artifact["output_dir"])).exists():
                tailoring_artifact = {key: stored_artifact[key] for key in stored_artifact.keys()}
        if tailoring_artifact is None:
            tailoring_artifact = self._ensure_tailoring_artifact(
                job_id=job_id,
                profile_name=profile_name,
                force=False,
            )
        context = self._build_adapter_context(profile, tailoring_artifact["output_dir"])
        adapter = self._adapter_for_name(adapter_name)
        manual_url = str(row["current_url"] or row["target_url"] or job["url"] or "").strip()
        if not manual_url:
            raise RuntimeError(f"Application id {application_run_id} does not have a stored target URL.")

        session = self.browser_manager.open(adapter_name=adapter_name, headless=False)
        try:
            page = session.new_page()
            page.goto(manual_url, wait_until="domcontentloaded")
            if notify is not None:
                notify(self._manual_checkpoint_message(row))
            if wait_for_user is not None:
                wait_for_user()

            run_id = self.store.create_application_run(
                job_id=job_id,
                profile_name=profile_name,
                tailoring_artifact_id=int(tailoring_artifact["id"]),
                adapter_name=adapter_name,
                source=str(job["source"]),
                target_url=manual_url,
                current_url=getattr(page, "url", manual_url),
                status="applying",
                output_dir=str(self._output_dir(profile_name, "pending")),
            )
            output_dir = self._output_dir(profile_name, str(run_id))
            output_dir.mkdir(parents=True, exist_ok=True)
            self.store.update_application_run(
                run_id,
                tailoring_artifact_id=int(tailoring_artifact["id"]),
                target_url=manual_url,
                current_url=getattr(page, "url", manual_url),
                increment_attempt_count=True,
                output_dir=str(output_dir),
            )
            submit_started_at = datetime.now(timezone.utc)
            result = adapter.submit(page=page, resolver=resolver, context=context)
            result = self._maybe_complete_email_verification(
                adapter_name=adapter_name,
                adapter=adapter,
                page=page,
                result=result,
                context=context,
                resolver=resolver,
                recipient_email=profile.identity.email,
                submit_started_at=submit_started_at,
            )
            self._persist_result(run_id=run_id, result=result, output_dir=output_dir, page=page)
            self._maybe_notify(job=job, run_id=run_id, result=result)
            return self._run_record(run_id)
        finally:
            session.close()

    def _manual_checkpoint_message(self, row) -> str:
        blocked_reason = str(row["blocked_reason"] or "").strip()
        blocked_payload = self._decode_json_object(row["blocked_payload"])
        details = blocked_payload.get("details") if isinstance(blocked_payload, dict) else {}
        if blocked_reason == "manual_checkpoint_required" and isinstance(details, dict):
            checkpoint = str(details.get("checkpoint") or "").strip()
            if checkpoint == "professional_experience_dropdowns":
                return (
                    "Manual checkpoint opened in the browser. Fill the Professional Experience dropdown fields "
                    "(Country, State/Province, date dropdowns, and May We Contact as needed) on the current "
                    "candidate profile page, leave the page open there, then return here and press Enter."
                )
            if checkpoint == "job_specific_questions_gpa":
                return (
                    "Manual checkpoint opened in the browser. Select the undergraduate GPA answer on the "
                    "Job Specific Questions page, submit that page manually if needed, leave the resulting page open, "
                    "then return here and press Enter."
                )
            if checkpoint == "handshake_fellow_apply":
                return (
                    "Manual checkpoint opened in the browser. Complete the Handshake Fellow application steps, "
                    "stop on the final submission confirmation page or the last review screen, leave that page open, "
                    "then return here and press Enter."
                )
            if checkpoint == "workday_required_listbox":
                question_text = str(details.get("question_text") or "the required dropdown").strip()
                expected_answer = str(details.get("expected_answer") or "").strip()
                answer_clause = f" and select `{expected_answer}`" if expected_answer else ""
                return (
                    "Manual checkpoint opened in the browser. Open "
                    f"{question_text}{answer_clause}, leave the application on the same page after the selection, "
                    "then return here and press Enter."
                )
            if checkpoint == "workday_required_choice":
                question_text = str(details.get("question_text") or "the required choice field").strip()
                expected_answer = str(details.get("expected_answer") or "").strip()
                answer_clause = f" and select `{expected_answer}`" if expected_answer else ""
                return (
                    "Manual checkpoint opened in the browser. Open "
                    f"{question_text}{answer_clause}, leave the application on the same page after the selection, "
                    "then return here and press Enter."
                )
        return (
            "Manual gate continuation opened in the browser. "
            "Complete any captcha, login, or consent steps, navigate to the application form, "
            "then return here and press Enter."
        )

    def _handoff_message(self, *, job, adapter_name: str, run_id: int, target_url: str) -> str:
        company = str(job["company"] or "").strip()
        title = str(job["title"] or "").strip()
        portal = adapter_name.replace("_", " ")
        return (
            f"Live handoff opened in the browser for application {run_id}: {company} / {title} via {portal}. "
            f"Complete any sign-up, login, captcha, consent, or bootstrap steps, leave the browser on the "
            f"resulting application page, then return here and press Enter to resume automation. target_url={target_url}"
        )

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
            profile=profile,
            workday_account_store_path=None,
        )

    def _resolve_adapter(self, job, page, target_url: str):
        source = str(job["source"] or "")
        if source == "linkedin" and self._is_linkedin_url(target_url):
            if self.linkedin_adapter.is_easy_apply_available(page):
                return "linkedin", self.linkedin_adapter, target_url
            external_url = ""
            for _ in range(3):
                external_url = self.linkedin_adapter.extract_external_apply_url(page)
                if external_url:
                    break
                wait = getattr(page, "wait_for_timeout", None)
                if callable(wait):
                    wait(1500)
            if not external_url:
                previous_target = self.store.find_latest_application_target(job_id=int(job["id"]))
                if previous_target is not None:
                    external_url = str(previous_target["target_url"] or "").strip()
            if external_url:
                page.goto(external_url, wait_until="domcontentloaded")
                target_url = external_url
            else:
                raise RuntimeError("LinkedIn job did not expose Easy Apply or a supported external apply link.")
        elif source == "handshake":
            self._prepare_handshake_page(page)
            current_url = str(getattr(page, "url", target_url) or target_url)
            if self.handshake_adapter.is_handshake_target(current_url, page=page):
                return "handshake", self.handshake_adapter, current_url
            allow_click_discovery = not self.handshake_adapter.is_handshake_target(target_url, page=page)
            external_url = self._discover_external_apply_url(job, page, target_url, allow_click=allow_click_discovery)
            if external_url:
                page.goto(external_url, wait_until="domcontentloaded")
                target_url = external_url
        if self.greenhouse_adapter.is_greenhouse_target(target_url, page=page):
            return "greenhouse", self.greenhouse_adapter, target_url
        if self.icims_adapter.is_icims_target(target_url, page=page):
            return "icims", self.icims_adapter, target_url
        if self.workday_adapter.is_workday_target(target_url, page=page):
            return "workday", self.workday_adapter, target_url
        if self.handshake_adapter.is_handshake_target(target_url, page=page):
            current_url = str(getattr(page, "url", target_url) or target_url)
            return "handshake", self.handshake_adapter, current_url
        if self.handshake_fellow_adapter.is_handshake_fellow_target(target_url, page=page):
            current_url = str(getattr(page, "url", target_url) or target_url)
            return "handshake_fellow", self.handshake_fellow_adapter, current_url
        blocker_reason, details = self._classify_unsupported_target(
            source=source,
            target_url=target_url,
            current_url=str(getattr(page, "url", target_url) or target_url),
        )
        raise UnsupportedApplyTargetError(
            source=source,
            target_url=target_url,
            current_url=str(getattr(page, "url", target_url) or target_url),
            blocker_reason=blocker_reason,
            details=details,
        )

    def _initial_target_url(self, job) -> str:
        source_metadata = self._job_source_metadata(job)
        external_url = str(source_metadata.get("external_apply_url") or "").strip()
        if external_url:
            return external_url
        return str(job["url"] or "").strip()

    def _job_source_metadata(self, job) -> dict[str, object]:
        raw = job["source_metadata"] if "source_metadata" in job.keys() else None
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str):
            text = raw.strip()
            if not text:
                return {}
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                return {}
            return payload if isinstance(payload, dict) else {}
        return {}

    def _is_linkedin_url(self, url: str) -> bool:
        parsed = urlparse(url.strip())
        return "linkedin.com" in parsed.netloc.lower()

    def _session_adapter_name_for_source(self, source: str) -> str:
        if source == "linkedin":
            return "linkedin"
        if source == "handshake":
            return "handshake"
        return "greenhouse"

    def _discover_external_apply_url(self, job, page, target_url: str, *, allow_click: bool = True) -> str:
        source_metadata = self._job_source_metadata(job)
        external_url = str(source_metadata.get("external_apply_url") or "").strip()
        if external_url:
            return external_url
        previous_target = self.store.find_latest_application_target(job_id=int(job["id"]))
        if previous_target is not None:
            candidate = str(previous_target["target_url"] or "").strip()
            if candidate and candidate != target_url and not self._is_handshake_url(candidate):
                return candidate
        extractor = getattr(page, "extract_external_apply_url", None)
        if callable(extractor):
            candidate = str(extractor() or "").strip()
            if candidate and candidate != target_url:
                return candidate
        if allow_click:
            clicked_url = self._discover_external_apply_url_from_click(page, target_url)
            if clicked_url:
                return clicked_url
        if not hasattr(page, "evaluate"):
            return ""
        candidate = page.evaluate(
            """
            ({ currentUrl }) => {
              const currentHost = (() => {
                try { return new URL(currentUrl).host.toLowerCase(); } catch (error) { return ''; }
              })();
              const links = Array.from(document.querySelectorAll('a[href]'));
              for (const link of links) {
                const href = (link.href || '').trim();
                if (!href) continue;
                try {
                  const url = new URL(href, window.location.href);
                  if (!/^https?:$/i.test(url.protocol)) continue;
                  if (url.host.toLowerCase() === currentHost) continue;
                  return url.toString();
                } catch (error) {
                  continue;
                }
              }
              return '';
            }
            """,
            {"currentUrl": str(getattr(page, "url", target_url) or target_url)},
        )
        return str(candidate or "").strip()

    def _prepare_handshake_page(self, page) -> None:
        wait = getattr(page, "wait_for_timeout", None)
        if callable(wait):
            wait(3000)
            wait(2000)

    def _discover_external_apply_url_from_click(self, page, target_url: str) -> str:
        if not hasattr(page, "locator"):
            return ""
        try:
            context = getattr(page, "context", None)
            existing_pages = list(getattr(context, "pages", [])) if context is not None else []
            selectors = [
                ('button', 'Apply'),
                ('button', 'Apply now'),
                ('a', 'Apply'),
                ('a', 'Apply now'),
                ('[role="button"]', 'Apply'),
                ('[role="button"]', 'Apply now'),
            ]
            for selector, label in selectors:
                try:
                    candidate = page.locator(selector).filter(has_text=label).first
                    if candidate.count() == 0:
                        continue
                    candidate.click()
                    wait = getattr(page, "wait_for_timeout", None)
                    if callable(wait):
                        wait(2000)
                    current_url = str(getattr(page, "url", "") or "").strip()
                    if current_url and current_url != target_url and not self._is_handshake_url(current_url):
                        return current_url
                    if context is not None:
                        for popup in getattr(context, "pages", []):
                            if popup in existing_pages:
                                continue
                            popup_url = str(getattr(popup, "url", "") or "").strip()
                            if popup_url and not self._is_handshake_url(popup_url):
                                return popup_url
                except Exception:
                    continue
        except Exception:
            return ""
        return ""

    def _is_handshake_url(self, url: str) -> bool:
        parsed = urlparse(url.strip())
        return "joinhandshake.com" in parsed.netloc.lower()

    def _classify_unsupported_target(self, *, source: str, target_url: str, current_url: str) -> tuple[str, dict[str, object]]:
        parsed_target = urlparse(target_url.strip())
        parsed_current = urlparse(current_url.strip())
        target_host = parsed_target.netloc.lower()
        current_host = parsed_current.netloc.lower()
        details: dict[str, object] = {
            "resolved_from_source": source,
            "target_host": target_host,
            "current_host": current_host,
        }
        if source == "handshake":
            if "ai.joinhandshake.com" in current_host or "ai.joinhandshake.com" in target_host:
                if "/fellow" in parsed_current.path or "/fellow" in parsed_target.path:
                    details["portal_family"] = "handshake_fellow"
                    return "handshake_fellow_unsupported", details
            if "joinhandshake.com" in current_host or "joinhandshake.com" in target_host:
                details["portal_family"] = "handshake_native"
                return "handshake_native_unsupported", details
        return "unsupported_portal", details

    def _adapter_for_name(self, adapter_name: str):
        if adapter_name == "linkedin":
            return self.linkedin_adapter
        if adapter_name == "greenhouse":
            return self.greenhouse_adapter
        if adapter_name == "handshake":
            return self.handshake_adapter
        if adapter_name == "handshake_fellow":
            return self.handshake_fellow_adapter
        if adapter_name == "icims":
            return self.icims_adapter
        if adapter_name == "workday":
            return self.workday_adapter
        raise RuntimeError(f"Unsupported adapter name: {adapter_name}")

    def _persist_result(self, *, run_id: int, result: SubmitResult, output_dir: Path, page) -> None:
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
            screenshot_path = output_dir / "blocked.png"
            try:
                page.screenshot(path=str(screenshot_path), full_page=True)
            except Exception:
                pass
            try:
                (output_dir / "page.html").write_text(page.content(), encoding="utf-8")
            except Exception:
                pass
        if result.confirmation_payload:
            (output_dir / "confirmation.json").write_text(
                json.dumps(result.confirmation_payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

    def _maybe_complete_email_verification(
        self,
        *,
        adapter_name: str,
        adapter,
        page,
        result: SubmitResult,
        context,
        resolver,
        recipient_email: str,
        submit_started_at: datetime,
    ) -> SubmitResult:
        if adapter_name not in {"greenhouse", "workday"}:
            return result
        if result.blocker is None or result.blocker.reason != "email_verification_required":
            return result
        if self.email_code_client is None or not self.email_code_client.is_enabled():
            return result
        try:
            if adapter_name == "workday":
                code = self.email_code_client.poll_for_workday_code(
                    recipient_email=recipient_email,
                    requested_at=submit_started_at,
                )
            else:
                code = self.email_code_client.poll_for_greenhouse_code(
                    recipient_email=recipient_email,
                    requested_at=submit_started_at,
                )
        except RuntimeError as exc:
            if result.blocker is None:
                return result
            details = dict(result.blocker.details)
            details["gmail_verification_error"] = str(exc)
            result.blocker = Blocker(
                reason=result.blocker.reason,
                question_text=result.blocker.question_text,
                field_name=result.blocker.field_name,
                field_type=result.blocker.field_type,
                details=details,
            )
            result.steps.append(
                StepSnapshot(
                    step_key="greenhouse:email_verification:gmail",
                    step_label="Fetch Greenhouse verification code from Gmail",
                    status="failed",
                    field_name=result.blocker.field_name,
                    field_type=result.blocker.field_type,
                    question_text=result.blocker.question_text,
                    payload={"error": str(exc)},
                )
            )
            return result
        if not code:
            return result
        return adapter.complete_email_verification(
            page=page,
            code=code,
            steps=result.steps,
            context=context,
            resolver=resolver,
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
    email_code_client = GmailVerificationCodeClient(settings) if settings.apply_gmail_verification_enabled else None
    return ApplicationService(
        settings=settings,
        store=store,
        tailoring_service=tailoring_service,
        email_code_client=email_code_client,
    )
