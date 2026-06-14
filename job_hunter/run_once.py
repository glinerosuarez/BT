from __future__ import annotations

import argparse
from dataclasses import asdict

from job_hunter.config import load_settings
from job_hunter.logging_utils import configure_logging
from job_hunter.notify import TelegramNotifier
from job_hunter.pipeline import run_pipeline
from job_hunter.source_maintenance import run_source_maintenance
from job_hunter.storage import JobStore, ensure_parent_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one internship sourcing cycle")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logs")
    parser.add_argument("--skip-source-maintenance", action="store_true", help="Skip source file quarantine updates")
    args = parser.parse_args()

    configure_logging(verbose=args.verbose)
    settings = load_settings()
    ensure_parent_dir(settings.db_path)
    store = JobStore(settings.db_path)

    notifier = None
    if settings.telegram_bot_token and settings.telegram_chat_id:
        notifier = TelegramNotifier(
            bot_token=settings.telegram_bot_token,
            chat_id=settings.telegram_chat_id,
            timeout_seconds=settings.request_timeout_seconds,
        )

    try:
        if not args.skip_source_maintenance:
            run_source_maintenance(settings, store)
        outcome = run_pipeline(settings, store, notifier)
        print(asdict(outcome))
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
