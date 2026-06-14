from __future__ import annotations

import argparse
import json

from job_hunter.config import load_settings
from job_hunter.logging_utils import configure_logging
from job_hunter.source_maintenance import run_source_maintenance
from job_hunter.storage import JobStore, ensure_parent_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="Quarantine and restore source tokens/feeds")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logs")
    parser.add_argument(
        "--no-probe-quarantine",
        action="store_true",
        help="Skip quarantine probes and only apply file moves from existing health counters",
    )
    parser.add_argument(
        "--probe-limit-per-source",
        type=int,
        default=20,
        help="Max quarantined items to probe for each source",
    )
    parser.add_argument(
        "--probe-active",
        action="store_true",
        help="Probe active items and refresh health state before quarantine decisions",
    )
    args = parser.parse_args()

    configure_logging(verbose=args.verbose)
    settings = load_settings()
    ensure_parent_dir(settings.db_path)
    store = JobStore(settings.db_path)
    try:
        summary = run_source_maintenance(
            settings,
            store,
            probe_active=args.probe_active,
            probe_quarantine=not args.no_probe_quarantine,
            probe_limit_per_source=max(args.probe_limit_per_source, 0),
        )
        print(json.dumps(summary, sort_keys=True))
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
