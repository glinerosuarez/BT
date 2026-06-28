from __future__ import annotations

import argparse
import json

from job_hunter.config import load_settings
from job_hunter.storage import JobStore, ensure_parent_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="Show the latest pipeline funnel report")
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format",
    )
    args = parser.parse_args()

    settings = load_settings(load_dotenv=True)
    ensure_parent_dir(settings.db_path)
    store = JobStore(settings.db_path)
    try:
        report = store.get_latest_run_report()
        if report is None:
            print("No run logs found.")
            return 0

        if args.format == "json":
            print(json.dumps(report, sort_keys=True, indent=2))
        else:
            print(_render_text(report))
        return 0
    finally:
        store.close()


def _render_text(report: dict[str, object]) -> str:
    lines = [
        f"run_id={report['run_id']} run_at={report['run_at']}",
        "",
        "overall",
        f"  fetched={report['source_count']} normalized={report['normalized_count']} missing_core={report['rejected_missing_core_fields_count']}",
        f"  after_stage_1a={report['after_stage_1a_count']} after_stage_1b={report['after_stage_1b_count']} after_stage_1c={report['after_stage_1c_count']}",
        f"  passed_filters={report['passed_filter_count']} duplicates={report['duplicate_count']} persisted={report['persisted_count']} notified={report['notified_count']} errors={report['error_count']}",
        "",
        "sources",
    ]
    source_stats = list(report["source_stats"])
    for row in source_stats:
        lines.extend(
            [
                f"  {row['source_name']}",
                f"    fetched={row['fetched_count']} normalized={row['normalized_count']} missing_core={row['rejected_missing_core_fields_count']}",
                f"    rejected_age={row['rejected_age_count']} after_stage_1a={row['after_stage_1a_count']}",
                (
                    "    rejected_internship={rejected_internship_count} "
                    "rejected_us_scope={rejected_us_scope_count} "
                    "rejected_title_blacklist={rejected_title_blacklist_count} "
                    "rejected_data_role={rejected_data_role_count} "
                    "after_stage_1b={after_stage_1b_count}"
                ).format(**row),
                (
                    "    rejected_policy_gate={rejected_policy_gate_count} "
                    "after_stage_1c={after_stage_1c_count} "
                    "rejected_eligibility={rejected_eligibility_count} "
                    "rejected_relevance={rejected_relevance_count} "
                    "rejected_source_quality={rejected_source_quality_count} "
                    "recovered_source_quality={recovered_source_quality_count}"
                ).format(**row),
                (
                    "    duplicates={duplicate_count} persisted={persisted_count} "
                    "notified={notified_count} errors={error_count} "
                    "dead_tokens={dead_token_count} feed_errors={feed_error_count}"
                ).format(**row),
            ]
        )
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
