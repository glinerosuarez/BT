from __future__ import annotations

import argparse
import json

from job_hunter.config import load_settings
from job_hunter.logging_utils import configure_logging
from job_hunter.orchestrator.service import OrchestratorService


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    configure_logging(verbose=getattr(args, "verbose", False))
    settings = load_settings(load_dotenv=True)
    service = OrchestratorService(settings=settings)
    try:
        if args.command == "init":
            _print(service.initialize(), args.format)
            return 0
        if args.command == "once":
            _print(service.run_cycle(trigger_name="once", attempt_limit=args.attempt_limit), args.format)
            return 0
        if args.command == "run":
            service.initialize()
            service.run_forever(attempt_limit=args.attempt_limit)
            return 0
        if args.command == "status":
            _print(service.status(), args.format)
            return 0
        if args.command == "report":
            _print(service.report(days=args.days), args.format)
            return 0
        if args.command == "sources":
            return _sources(service, args)
        if args.command == "interventions":
            return _interventions(service, args)
        parser.error(f"Unknown command: {args.command}")
        return 2
    except (RuntimeError, ValueError) as exc:
        print(str(exc))
        return 1
    finally:
        service.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the durable multi-agent job-hunting orchestrator")
    parser.add_argument("--verbose", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="Initialize baseline and source registry")
    _format_argument(init)

    once = subparsers.add_parser("once", help="Run one sourcing and application cycle")
    once.add_argument("--attempt-limit", type=int)
    _format_argument(once)

    run = subparsers.add_parser("run", help="Run the local daemon")
    run.add_argument("--attempt-limit", type=int)

    status = subparsers.add_parser("status")
    _format_argument(status)

    report = subparsers.add_parser("report")
    report.add_argument("--days", type=int, default=7)
    _format_argument(report)

    sources = subparsers.add_parser("sources")
    source_sub = sources.add_subparsers(dest="source_command", required=True)
    source_list = source_sub.add_parser("list")
    source_list.add_argument("--status")
    _format_argument(source_list)
    source_history = source_sub.add_parser("history")
    source_history.add_argument("--source-id", type=int, required=True)
    _format_argument(source_history)
    source_rollback = source_sub.add_parser("rollback")
    source_rollback.add_argument("--source-id", type=int, required=True)
    _format_argument(source_rollback)

    interventions = subparsers.add_parser("interventions")
    intervention_sub = interventions.add_subparsers(dest="intervention_command", required=True)
    intervention_list = intervention_sub.add_parser("list")
    intervention_list.add_argument("--status", default="pending")
    _format_argument(intervention_list)
    intervention_resolve = intervention_sub.add_parser("resolve")
    intervention_resolve.add_argument("--intervention-id", type=int, required=True)
    intervention_resolve.add_argument("--action", choices=("open", "retry", "continue", "skip"), required=True)
    _format_argument(intervention_resolve)
    return parser


def _sources(service: OrchestratorService, args) -> int:
    if args.source_command == "list":
        payload = service.store.list_sources(status=args.status)
    elif args.source_command == "history":
        payload = service.store.source_history(args.source_id)
    else:
        payload = {"source_id": args.source_id, "status": service.store.rollback_source(args.source_id)}
    _print(payload, args.format)
    return 0


def _interventions(service: OrchestratorService, args) -> int:
    if args.intervention_command == "list":
        payload = service.store.list_interventions(status=args.status)
    else:
        payload = service.resume_intervention(args.intervention_id, action=args.action)
    _print(payload, args.format)
    return 0


def _format_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--format", choices=("text", "json"), default="text")


def _print(payload: object, output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
        return
    if isinstance(payload, list):
        for item in payload:
            print(json.dumps(item, sort_keys=True, default=str))
        return
    if isinstance(payload, dict):
        for key, value in payload.items():
            rendered = json.dumps(value, sort_keys=True, default=str) if isinstance(value, (dict, list)) else value
            print(f"{key}={rendered}")
        return
    print(payload)
