from __future__ import annotations

import argparse
import json
from pathlib import Path

from job_hunter.config import load_settings
from job_hunter.storage import JobStore, ensure_parent_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect Stage 2 shadow-mode outputs")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List jobs with Stage 2 shadow outputs")
    list_parser.add_argument("--limit", type=int, default=20)
    list_parser.add_argument("--label", choices=("pass", "review", "reject"))
    list_parser.add_argument("--source")
    list_parser.add_argument("--format", choices=("text", "json"), default="text")

    show_parser = subparsers.add_parser("show", help="Show one job's Stage 2 shadow output in detail")
    show_parser.add_argument("--job-id", type=int, required=True)
    show_parser.add_argument("--format", choices=("text", "json"), default="text")

    export_parser = subparsers.add_parser("export-labeled", help="Export labeled Stage 2 rows for embeddings/eval work")
    export_parser.add_argument("--output", required=True)
    export_parser.add_argument("--limit", type=int, default=200)

    args = parser.parse_args()

    settings = load_settings()
    ensure_parent_dir(settings.db_path)
    store = JobStore(settings.db_path)
    try:
        if args.command == "list":
            rows = store.list_stage2_jobs(limit=args.limit, label=args.label, source=args.source)
            payload = [_serialize_list_row(row) for row in rows]
            if args.format == "json":
                print(json.dumps(payload, indent=2, sort_keys=True))
            else:
                print(_render_list_text(payload))
            return 0

        if args.command == "export-labeled":
            rows = store.list_stage2_labeled_jobs(limit=args.limit)
            payload = [_serialize_show_row(row) for row in rows]
            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
            print(f"Wrote {len(payload)} labeled Stage 2 rows to {output_path}")
            return 0

        row = store.get_stage2_job(args.job_id)
        if row is None:
            print(f"No Stage 2 job found for id={args.job_id}")
            return 1
        payload = _serialize_show_row(row)
        if args.format == "json":
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(_render_show_text(payload))
        return 0
    finally:
        store.close()


def _serialize_list_row(row) -> dict[str, object]:
    reasons = _decode_json_array(row["profile_match_reason_codes"])
    return {
        "id": int(row["id"]),
        "source": str(row["source"]),
        "company": str(row["company"]),
        "title": str(row["title"]),
        "location": str(row["location"] or ""),
        "posted_at": str(row["posted_at"] or ""),
        "compensation_type": str(row["compensation_type"] or "unknown"),
        "profile_match_score": float(row["profile_match_score"] or 0.0),
        "profile_match_label": str(row["profile_match_label"] or ""),
        "profile_match_reason_codes": reasons,
        "profile_version": str(row["profile_version"] or ""),
        "scorer_version": str(row["scorer_version"] or ""),
        "job_text_version": str(row["job_text_version"] or ""),
    }


def _serialize_show_row(row) -> dict[str, object]:
    return {
        "id": int(row["id"]),
        "source": str(row["source"]),
        "company": str(row["company"]),
        "title": str(row["title"]),
        "location": str(row["location"] or ""),
        "posted_at": str(row["posted_at"] or ""),
        "url": str(row["url"]),
        "compensation_type": str(row["compensation_type"] or "unknown"),
        "relevance_score": float(row["relevance_score"] or 0.0),
        "eligibility_status": str(row["eligibility_status"] or ""),
        "eligibility_confidence": float(row["eligibility_confidence"] or 0.0),
        "profile_match_score": float(row["profile_match_score"] or 0.0),
        "profile_match_label": str(row["profile_match_label"] or ""),
        "profile_match_reason_codes": _decode_json_array(row["profile_match_reason_codes"]),
        "profile_version": str(row["profile_version"] or ""),
        "scorer_version": str(row["scorer_version"] or ""),
        "job_text_version": str(row["job_text_version"] or ""),
        "job_text_snapshot": str(row["job_text_snapshot"] or ""),
        "manual_fit_label": str(row["manual_fit_label"] or ""),
        "manual_fit_reason_codes": _decode_json_array(row["manual_fit_reason_codes"]),
    }


def _decode_json_array(value: object) -> list[str]:
    if value is None:
        return []
    raw = str(value).strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return [raw]
    if not isinstance(parsed, list):
        return [str(parsed)]
    return [str(item) for item in parsed]


def _render_list_text(rows: list[dict[str, object]]) -> str:
    if not rows:
        return "No Stage 2 jobs found."
    chunks: list[str] = []
    for row in rows:
        reasons = ",".join(row["profile_match_reason_codes"]) or "-"
        chunks.append(
            f"[{row['id']}] {row['title']}\n"
            f"  company={row['company']} source={row['source']}\n"
            f"  location={row['location']} posted_at={row['posted_at']} compensation={row['compensation_type']}\n"
            f"  stage2={row['profile_match_label']} score={row['profile_match_score']:.2f} reasons={reasons}\n"
            f"  versions: profile={row['profile_version']} scorer={row['scorer_version']} text={row['job_text_version']}"
        )
    return "\n\n".join(chunks)


def _render_show_text(row: dict[str, object]) -> str:
    reasons = ",".join(row["profile_match_reason_codes"]) or "-"
    manual_reasons = ",".join(row["manual_fit_reason_codes"]) or "-"
    return "\n".join(
        [
            f"id={row['id']}",
            f"source={row['source']}",
            f"company={row['company']}",
            f"title={row['title']}",
            f"location={row['location']}",
            f"posted_at={row['posted_at']}",
            f"compensation_type={row['compensation_type']}",
            f"url={row['url']}",
            f"relevance_score={row['relevance_score']}",
            f"eligibility={row['eligibility_status']} ({row['eligibility_confidence']:.2f})",
            f"stage2_label={row['profile_match_label']}",
            f"stage2_score={row['profile_match_score']:.2f}",
            f"stage2_reasons={reasons}",
            f"profile_version={row['profile_version']}",
            f"scorer_version={row['scorer_version']}",
            f"job_text_version={row['job_text_version']}",
            f"manual_fit_label={row['manual_fit_label'] or '-'}",
            f"manual_fit_reason_codes={manual_reasons}",
            "",
            "job_text_snapshot:",
            str(row["job_text_snapshot"] or "-"),
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
