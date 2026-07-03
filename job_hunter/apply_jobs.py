from __future__ import annotations

import argparse
import json

from job_hunter.apply.service import ApplicationService, build_application_service
from job_hunter.apply.browser import BrowserManager
from job_hunter.apply.adapters.greenhouse import GreenhouseAdapter
from job_hunter.apply.adapters.linkedin import LinkedInEasyApplyAdapter
from job_hunter.config import load_settings
from job_hunter.storage import JobStore, ensure_parent_dir
from job_hunter.tailoring.service import TailoringService


def main() -> int:
    parser = argparse.ArgumentParser(description="Run automated application attempts against stored jobs")
    subparsers = parser.add_subparsers(dest="command", required=True)

    submit_parser = subparsers.add_parser("submit")
    submit_parser.add_argument("--job-id", type=int, required=True)
    submit_parser.add_argument("--profile", default="default")
    submit_parser.add_argument("--force", action="store_true")

    batch_parser = subparsers.add_parser("batch")
    batch_parser.add_argument("--profile", default="default")
    batch_parser.add_argument("--limit", type=int, default=None)
    batch_parser.add_argument("--source")
    batch_parser.add_argument("--force", action="store_true")

    list_parser = subparsers.add_parser("list")
    list_parser.add_argument("--status", choices=("queued", "applying", "submitted", "blocked", "failed", "skipped"))
    list_parser.add_argument("--limit", type=int, default=20)

    show_parser = subparsers.add_parser("show")
    show_parser.add_argument("--application-id", type=int, required=True)
    show_parser.add_argument("--format", choices=("text", "json"), default="text")

    resume_parser = subparsers.add_parser("resume")
    resume_parser.add_argument("--application-id", type=int, required=True)

    args = parser.parse_args()

    settings = load_settings(load_dotenv=True)
    ensure_parent_dir(settings.db_path)
    store = JobStore(settings.db_path)
    try:
        if args.command == "submit":
            service = build_application_service(settings=settings, store=store)
            run = service.submit_job(job_id=args.job_id, profile_name=args.profile, force=args.force)
            print(json.dumps({"application_id": run.application_run_id, "status": run.status, "output_dir": run.output_dir}, sort_keys=True))
            return 0
        if args.command == "batch":
            service = build_application_service(settings=settings, store=store)
            runs = service.submit_batch(
                profile_name=args.profile,
                limit=args.limit or settings.apply_batch_default_limit,
                source=args.source,
                force=args.force,
            )
            print(
                json.dumps(
                    {
                        "processed_count": len(runs),
                        "runs": [
                            {"application_id": run.application_run_id, "job_id": run.job_id, "status": run.status}
                            for run in runs
                        ],
                    },
                    sort_keys=True,
                )
            )
            return 0
        if args.command == "list":
            service = _build_read_only_service(settings=settings, store=store)
            rows = service.list_runs(status=args.status, limit=args.limit)
            if not rows:
                print("No application runs found.")
                return 0
            for row in rows:
                blocker = f" blocker={row['blocked_reason']}" if row["blocked_reason"] else ""
                print(
                    f"[{row['application_id']}] job={row['job_id']} {row['company']} / {row['title']} "
                    f"status={row['status']} adapter={row['adapter_name']} profile={row['profile_name']}{blocker}"
                )
            return 0
        if args.command == "show":
            service = _build_read_only_service(settings=settings, store=store)
            payload = service.show_run(args.application_id)
            if args.format == "json":
                print(json.dumps(payload, indent=2, sort_keys=True))
            else:
                print(_render_text(payload))
            return 0
        service = build_application_service(settings=settings, store=store)
        run = service.resume(application_run_id=args.application_id)
        print(json.dumps({"application_id": run.application_run_id, "status": run.status, "output_dir": run.output_dir}, sort_keys=True))
        return 0
    except RuntimeError as exc:
        print(str(exc))
        return 1
    finally:
        store.close()


def _render_text(payload: dict[str, object]) -> str:
    lines = [
        f"application_id={payload['application_id']}",
        f"job_id={payload['job_id']}",
        f"company={payload['company']}",
        f"title={payload['title']}",
        f"profile_name={payload['profile_name']}",
        f"adapter_name={payload['adapter_name']}",
        f"status={payload['status']}",
        f"target_url={payload['target_url']}",
        f"current_url={payload['current_url']}",
        f"blocked_reason={payload['blocked_reason'] or '-'}",
        f"confirmation_payload={json.dumps(payload['confirmation_payload'], sort_keys=True)}",
        "steps:",
    ]
    for step in payload["steps"]:
        lines.append(
            f"  {step['step_key']} {step['status']} field={step['field_name'] or '-'} "
            f"question={step['question_text'] or '-'} answer_source={step['answer_source'] or '-'}"
        )
    return "\n".join(lines)


def _build_read_only_service(*, settings, store) -> ApplicationService:
    return ApplicationService(
        settings=settings,
        store=store,
        tailoring_service=TailoringService(settings=settings, store=store, provider=None),
        browser_manager=BrowserManager(settings),
        linkedin_adapter=LinkedInEasyApplyAdapter(),
        greenhouse_adapter=GreenhouseAdapter(),
    )


if __name__ == "__main__":
    raise SystemExit(main())
