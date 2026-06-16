from __future__ import annotations

import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright

from job_hunter.config import load_settings


def main() -> int:
    parser = argparse.ArgumentParser(description="Open a persistent Handshake browser session for login")
    parser.add_argument(
        "--url",
        default="https://app.joinhandshake.com/login",
        help="URL to open for login bootstrap",
    )
    args = parser.parse_args()

    settings = load_settings()
    profile_path = Path(settings.handshake_profile_dir).expanduser()
    profile_path.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(profile_path),
            channel="chrome",
            headless=False,
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(args.url, wait_until="domcontentloaded")
            print(f"Handshake browser opened with profile: {profile_path}")
            print("Log in to Handshake in that window, then return here and press Enter.")
            input()
            return 0
        finally:
            context.close()


if __name__ == "__main__":
    raise SystemExit(main())
