from __future__ import annotations

import argparse
from dataclasses import replace
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
    parser.add_argument(
        "--handshake-quarantine-refresh-days",
        type=int,
        default=0,
        help="Refresh recent quarantined Handshake rows from direct job URLs and disable all other sources",
    )
    parser.add_argument(
        "--handshake-quarantine-refresh-limit",
        type=int,
        default=25,
        help="Max quarantined Handshake rows to refresh when --handshake-quarantine-refresh-days is enabled",
    )
    args = parser.parse_args()

    configure_logging(verbose=args.verbose)
    settings = load_settings(load_dotenv=True)
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
        if args.handshake_quarantine_refresh_days > 0:
            refresh_urls = store.list_recent_handshake_quarantined_urls(
                days=args.handshake_quarantine_refresh_days,
                limit=args.handshake_quarantine_refresh_limit,
            )
            if not refresh_urls:
                print({"refreshed_source": "handshake_quarantine", "url_count": 0, "skipped": True})
                return 0
            settings = replace(
                settings,
                use_arbeitnow=False,
                use_remotive=False,
                use_themuse=False,
                use_greenhouse=False,
                use_lever=False,
                use_rss=False,
                use_github_repos=False,
                use_ashby=False,
                use_usajobs=False,
                use_adzuna=False,
                use_handshake=True,
                handshake_search_urls=refresh_urls,
            )
        outcome = run_pipeline(settings, store, notifier)
        store.cleanup_handshake_duplicate_rows()
        print(asdict(outcome))
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
