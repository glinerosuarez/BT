from __future__ import annotations

import argparse
import time

from job_hunter.config import load_settings
from job_hunter.logging_utils import configure_logging
from job_hunter.notify import TelegramNotifier
from job_hunter.pipeline import run_pipeline
from job_hunter.storage import JobStore, ensure_parent_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="Run internship sourcing loop")
    parser.add_argument("--interval-minutes", type=int, default=None)
    parser.add_argument("--verbose", action="store_true", help="Enable debug logs")
    args = parser.parse_args()

    configure_logging(verbose=args.verbose)
    settings = load_settings()
    interval_minutes = args.interval_minutes or settings.poll_interval_minutes

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
        while True:
            run_pipeline(settings, store, notifier)
            time.sleep(max(interval_minutes, 1) * 60)
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
