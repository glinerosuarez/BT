from __future__ import annotations

import argparse
import json
from pathlib import Path
from textwrap import shorten

from job_hunter.config import load_settings
from job_hunter.storage import JobStore, ensure_parent_dir

FIT_LABELS = ("good_fit", "borderline", "bad_fit")
EXPORT_FORMATS = ("json", "jsonl", "markdown")
FIT_REASON_CODES = (
    "good_fit_ml_engineering",
    "good_fit_data_science",
    "good_fit_applied_scientist",
    "borderline_adjacent_role",
    "borderline_last_resort",
    "borderline_research_heavy",
    "borderline_degree_preference_mismatch",
    "borderline_conflicting_work_auth",
    "bad_fit_phd_only",
    "bad_fit_background_mismatch",
    "bad_fit_domain_mismatch",
    "bad_fit_research_heavy",
    "bad_fit_work_auth_mismatch",
    "bad_fit_seniority_mismatch",
    "bad_fit_non_target_function",
)


def main() -> int:
    parser = argparse.ArgumentParser(description="List and label historical jobs for Stage 2 evaluation")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List jobs available for manual labeling")
    list_parser.add_argument("--limit", type=int, default=20, help="Number of jobs to show")
    list_parser.add_argument(
        "--include-labeled",
        action="store_true",
        help="Include already labeled jobs in the listing",
    )
    list_parser.add_argument("--source", default=None, help="Optional source filter")

    stats_parser = subparsers.add_parser("stats", help="Show labeling coverage and source distribution")

    export_parser = subparsers.add_parser("export", help="Export a labeling batch for review")
    export_parser.add_argument("--output", required=True, help="Output JSONL file path")
    export_parser.add_argument("--limit", type=int, default=50, help="Number of jobs to export")
    export_parser.add_argument(
        "--include-labeled",
        action="store_true",
        help="Include already labeled jobs in the export",
    )
    export_parser.add_argument("--source", default=None, help="Optional source filter")
    export_parser.add_argument(
        "--format",
        default="json",
        choices=EXPORT_FORMATS,
        help="Export format: pretty JSON array, JSONL, or Markdown",
    )

    show_parser = subparsers.add_parser("show", help="Show one job in detail before labeling")
    show_parser.add_argument("--job-id", type=int, required=True, help="Database job id")

    label_parser = subparsers.add_parser("label", help="Apply a manual fit label to a job")
    label_parser.add_argument("--job-id", type=int, required=True, help="Database job id")
    label_parser.add_argument("--fit-label", required=True, choices=FIT_LABELS, help="Manual fit label")
    label_parser.add_argument(
        "--reason-codes",
        default="",
        help="Comma-separated reason codes, for example bad_fit_phd_only,bad_fit_domain_mismatch",
    )

    args = parser.parse_args()

    settings = load_settings()
    ensure_parent_dir(settings.db_path)
    store = JobStore(settings.db_path)
    try:
        if args.command == "list":
            return _cmd_list(store, limit=args.limit, include_labeled=args.include_labeled, source=args.source)
        if args.command == "stats":
            return _cmd_stats(store)
        if args.command == "export":
            return _cmd_export(
                store,
                output=args.output,
                limit=args.limit,
                include_labeled=args.include_labeled,
                source=args.source,
                export_format=args.format,
            )
        if args.command == "show":
            return _cmd_show(store, job_id=args.job_id)
        return _cmd_label(store, job_id=args.job_id, fit_label=args.fit_label, reason_codes=args.reason_codes)
    finally:
        store.close()


def _cmd_list(store: JobStore, limit: int, include_labeled: bool, source: str | None) -> int:
    rows = store.list_jobs_for_export(limit=limit, unlabeled_only=not include_labeled, source=source)
    if not rows:
        print("No jobs found for labeling.")
        return 0

    for row in rows:
        label = row["manual_fit_label"] or "-"
        reasons = _format_reasons(row["manual_fit_reason_codes"])
        title = shorten(str(row["title"]), width=72, placeholder="...")
        print(
            f"[{row['id']}] {title}\n"
            f"  company={row['company']} source={row['source']} score={row['relevance_score']}\n"
            f"  location={row['location']} posted_at={row['posted_at']}\n"
            f"  label={label} reasons={reasons}\n"
            f"  url={row['url']}\n"
        )
    return 0


def _cmd_stats(store: JobStore) -> int:
    stats = store.get_labeling_stats()
    print(
        json.dumps(
            stats,
            sort_keys=True,
            indent=2,
        )
    )
    return 0


def _cmd_export(
    store: JobStore,
    output: str,
    limit: int,
    include_labeled: bool,
    source: str | None,
    export_format: str,
) -> int:
    rows = store.list_jobs_for_export(limit=limit, unlabeled_only=not include_labeled, source=source)
    if not rows:
        print("No jobs found for export.")
        return 0

    path = Path(output).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    payloads = [_row_payload(row) for row in rows]
    with path.open("w", encoding="utf-8") as handle:
        if export_format == "jsonl":
            for payload in payloads:
                handle.write(json.dumps(payload, sort_keys=True) + "\n")
        elif export_format == "markdown":
            handle.write(_render_markdown(payloads))
        else:
            json.dump(payloads, handle, sort_keys=True, indent=2)
            handle.write("\n")

    print(json.dumps({"exported_count": len(rows), "format": export_format, "output": str(path)}, sort_keys=True))
    return 0


def _cmd_show(store: JobStore, job_id: int) -> int:
    row = store.get_job_for_labeling(job_id)
    if row is None:
        print(f"Job id {job_id} not found.")
        return 1

    print(f"id={row['id']}")
    print(f"source={row['source']}")
    print(f"company={row['company']}")
    print(f"title={row['title']}")
    print(f"location={row['location']}")
    print(f"posted_at={row['posted_at']}")
    print(f"relevance_score={row['relevance_score']}")
    print(f"manual_fit_label={row['manual_fit_label'] or '-'}")
    print(f"manual_fit_reason_codes={_format_reasons(row['manual_fit_reason_codes'])}")
    print(f"url={row['url']}")
    print("description=")
    print(row["description"] or "")
    return 0


def _cmd_label(store: JobStore, job_id: int, fit_label: str, reason_codes: str) -> int:
    parsed_reasons = _parse_reason_codes(reason_codes)
    if fit_label == "good_fit" and any(code.startswith("bad_fit_") for code in parsed_reasons):
        print("good_fit cannot be paired with bad_fit_* reason codes.")
        return 1

    if fit_label == "bad_fit" and not parsed_reasons:
        print("bad_fit requires at least one reason code.")
        return 1

    updated = store.set_manual_fit_label(job_id, fit_label, parsed_reasons)
    if not updated:
        print(f"Job id {job_id} not found.")
        return 1

    print(
        json.dumps(
            {
                "job_id": job_id,
                "manual_fit_label": fit_label,
                "manual_fit_reason_codes": parsed_reasons,
            },
            sort_keys=True,
        )
    )
    return 0


def _parse_reason_codes(raw: str) -> list[str]:
    if not raw.strip():
        return []
    values: list[str] = []
    seen: set[str] = set()
    for item in raw.split(","):
        code = item.strip()
        if not code:
            continue
        if code not in FIT_REASON_CODES:
            raise SystemExit(f"Unsupported reason code: {code}")
        if code in seen:
            continue
        seen.add(code)
        values.append(code)
    return values


def _format_reasons(raw: object) -> str:
    values = _decode_reasons(raw)
    if not values:
        return "-"
    return ",".join(values)


def _decode_reasons(raw: object) -> list[str]:
    if raw is None:
        return []
    text = str(raw).strip()
    if not text:
        return []
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        return [text]
    if not isinstance(decoded, list):
        return [text]
    values = [str(item) for item in decoded if str(item).strip()]
    return values


def _row_payload(row: object) -> dict[str, object]:
    return {
        "job_id": int(row["id"]),
        "source": str(row["source"]),
        "company": str(row["company"]),
        "title": str(row["title"]),
        "location": str(row["location"] or ""),
        "posted_at": str(row["posted_at"] or ""),
        "url": str(row["url"]),
        "relevance_score": float(row["relevance_score"] or 0.0),
        "eligibility_status": str(row["eligibility_status"] or ""),
        "eligibility_confidence": float(row["eligibility_confidence"] or 0.0),
        "description": str(row["description"] or ""),
        "manual_fit_label": str(row["manual_fit_label"] or ""),
        "manual_fit_reason_codes": _decode_reasons(row["manual_fit_reason_codes"]),
    }


def _render_markdown(payloads: list[dict[str, object]]) -> str:
    lines = ["# Labeling Batch", ""]
    for payload in payloads:
        description = str(payload["description"])
        reasons = payload["manual_fit_reason_codes"] or []
        reason_text = ", ".join(str(item) for item in reasons) if reasons else "-"
        lines.extend(
            [
                f"## [{payload['job_id']}] {payload['title']}",
                "",
                f"- company: {payload['company']}",
                f"- source: {payload['source']}",
                f"- location: {payload['location']}",
                f"- posted_at: {payload['posted_at']}",
                f"- relevance_score: {payload['relevance_score']}",
                f"- eligibility_status: {payload['eligibility_status']} ({payload['eligibility_confidence']})",
                f"- manual_fit_label: {payload['manual_fit_label'] or '-'}",
                f"- manual_fit_reason_codes: {reason_text}",
                f"- url: {payload['url']}",
                "",
                "### Description",
                "",
                description,
                "",
            ]
        )
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
