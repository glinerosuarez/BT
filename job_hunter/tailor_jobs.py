from __future__ import annotations

import argparse
import json

from job_hunter.config import load_settings
from job_hunter.storage import JobStore, ensure_parent_dir
from job_hunter.tailoring import AnthropicTailoringProvider, TailoringService


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate tailored resume and cover-letter artifacts for stored jobs")
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate_parser = subparsers.add_parser("generate", help="Generate tailoring artifacts for a single job")
    generate_parser.add_argument("--job-id", type=int, required=True)
    generate_parser.add_argument("--profile", default="default")
    generate_parser.add_argument("--force", action="store_true")

    batch_parser = subparsers.add_parser("batch", help="Generate tailoring artifacts for a batch of jobs")
    batch_parser.add_argument("--profile", default="default")
    batch_parser.add_argument("--limit", type=int, default=None)
    batch_parser.add_argument("--source")
    batch_parser.add_argument("--label", choices=("pass", "review", "reject"))
    batch_parser.add_argument("--force", action="store_true")

    list_parser = subparsers.add_parser("list", help="List stored tailoring artifacts")
    list_parser.add_argument("--limit", type=int, default=20)
    list_parser.add_argument("--profile")

    show_parser = subparsers.add_parser("show", help="Show a stored tailoring artifact")
    show_parser.add_argument("--artifact-id", type=int, required=True)
    show_parser.add_argument("--format", choices=("text", "json"), default="text")

    args = parser.parse_args()

    settings = load_settings(load_dotenv=True)
    ensure_parent_dir(settings.db_path)
    store = JobStore(settings.db_path)
    try:
        if args.command == "list":
            return _cmd_list(store, limit=args.limit, profile=args.profile)
        if args.command == "show":
            return _cmd_show(store, artifact_id=args.artifact_id, output_format=args.format)

        provider = _build_provider(settings)
        service = TailoringService(settings=settings, store=store, provider=provider)
        if args.command == "generate":
            return _cmd_generate(service, job_id=args.job_id, profile_name=args.profile, force=args.force)
        return _cmd_batch(
            service,
            store,
            profile_name=args.profile,
            limit=args.limit or settings.tailoring_batch_default_limit,
            source=args.source,
            label=args.label,
            force=args.force,
        )
    except RuntimeError as exc:
        print(str(exc))
        return 1
    finally:
        store.close()


def _build_provider(settings):
    if settings.tailoring_provider != "anthropic":
        raise RuntimeError(f"Unsupported tailoring provider: {settings.tailoring_provider}")
    return AnthropicTailoringProvider(model_name=settings.tailoring_anthropic_model or "")


def _cmd_generate(service: TailoringService, *, job_id: int, profile_name: str, force: bool) -> int:
    artifact = service.generate_for_job(job_id=job_id, profile_name=profile_name, force=force)
    print(
        json.dumps(
            {
                "artifact_id": artifact.artifact_id,
                "job_id": artifact.job_id,
                "profile_name": artifact.profile_name,
                "output_dir": artifact.output_dir,
                "created": artifact.created,
                "forced": artifact.forced,
            },
            sort_keys=True,
        )
    )
    return 0


def _cmd_batch(
    service: TailoringService,
    store: JobStore,
    *,
    profile_name: str,
    limit: int,
    source: str | None,
    label: str | None,
    force: bool,
) -> int:
    rows = store.list_tailoring_candidates(limit=limit, source=source, label=label)
    if not rows:
        print(json.dumps({"processed_count": 0, "success_count": 0, "failure_count": 0}, sort_keys=True))
        return 0
    successes: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []
    for row in rows:
        job_id = int(row["id"])
        try:
            artifact = service.generate_for_job(job_id=job_id, profile_name=profile_name, force=force)
            successes.append(
                {
                    "job_id": job_id,
                    "artifact_id": artifact.artifact_id,
                    "created": artifact.created,
                    "output_dir": artifact.output_dir,
                }
            )
        except RuntimeError as exc:
            failures.append({"job_id": job_id, "error": str(exc)})
    print(
        json.dumps(
            {
                "processed_count": len(rows),
                "success_count": len(successes),
                "failure_count": len(failures),
                "successes": successes,
                "failures": failures,
            },
            sort_keys=True,
        )
    )
    return 1 if failures else 0


def _cmd_list(store: JobStore, *, limit: int, profile: str | None) -> int:
    rows = store.list_tailoring_artifacts(limit=limit, profile_name=profile)
    if not rows:
        print("No tailoring artifacts found.")
        return 0
    for row in rows:
        print(
            f"[{row['id']}] job={row['job_id']} profile={row['profile_name']} "
            f"company={row['company']} title={row['title']}\n"
            f"  source={row['source']} stage2={row['profile_match_label'] or '-'} provider={row['provider_name']} model={row['model_name']}\n"
            f"  created_at={row['created_at']} output_dir={row['output_dir']}\n"
        )
    return 0


def _cmd_show(store: JobStore, *, artifact_id: int, output_format: str) -> int:
    row = store.get_tailoring_artifact(artifact_id)
    if row is None:
        print(f"Tailoring artifact id {artifact_id} not found.")
        return 1
    payload = {
        "artifact_id": int(row["id"]),
        "job_id": int(row["job_id"]),
        "profile_name": str(row["profile_name"]),
        "provider_name": str(row["provider_name"]),
        "model_name": str(row["model_name"]),
        "prompt_version": str(row["prompt_version"]),
        "company": str(row["company"]),
        "title": str(row["title"]),
        "source": str(row["source"]),
        "url": str(row["url"]),
        "output_dir": str(row["output_dir"]),
        "created_at": str(row["created_at"]),
        "highlight_requirements": _decode_json_array(row["highlight_requirements"]),
        "evidence_map": _decode_json_array_of_objects(row["evidence_map"]),
        "resume_markdown": str(row["resume_markdown"]),
        "cover_letter_markdown": str(row["cover_letter_markdown"]),
    }
    if output_format == "json":
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    print(f"artifact_id={payload['artifact_id']}")
    print(f"job_id={payload['job_id']}")
    print(f"profile_name={payload['profile_name']}")
    print(f"provider={payload['provider_name']}")
    print(f"model={payload['model_name']}")
    print(f"company={payload['company']}")
    print(f"title={payload['title']}")
    print(f"source={payload['source']}")
    print(f"url={payload['url']}")
    print(f"output_dir={payload['output_dir']}")
    print(f"created_at={payload['created_at']}")
    print("highlight_requirements=" + (", ".join(payload["highlight_requirements"]) or "-"))
    print("evidence_map=" + json.dumps(payload["evidence_map"], sort_keys=True))
    print("resume_markdown:")
    print(payload["resume_markdown"])
    print("cover_letter_markdown:")
    print(payload["cover_letter_markdown"])
    return 0


def _decode_json_array(value: object) -> list[str]:
    if value is None:
        return []
    raw = str(value).strip()
    if not raw:
        return []
    parsed = json.loads(raw)
    return [str(item) for item in parsed] if isinstance(parsed, list) else [str(parsed)]


def _decode_json_array_of_objects(value: object) -> list[dict[str, str]]:
    if value is None:
        return []
    raw = str(value).strip()
    if not raw:
        return []
    parsed = json.loads(raw)
    if not isinstance(parsed, list):
        return []
    rows: list[dict[str, str]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        rows.append({str(key): str(val) for key, val in item.items()})
    return rows


if __name__ == "__main__":
    raise SystemExit(main())
