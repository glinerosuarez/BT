from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Protocol

from job_hunter.config import Settings

_DEFAULT_CHROME_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/137.0.0.0 Safari/537.36"
)


class BrowserPage(Protocol):
    url: str

    def goto(self, url: str, *, wait_until: str = "domcontentloaded") -> None: ...

    def screenshot(self, *, path: str, full_page: bool = True) -> None: ...

    def content(self) -> str: ...


class BrowserSession(Protocol):
    def new_page(self) -> BrowserPage: ...

    def close(self) -> None: ...


class PlaywrightBrowserSession:
    def __init__(self, context) -> None:
        self._context = context

    def new_page(self):
        return self._context.new_page()

    def close(self) -> None:
        self._context.close()


class BrowserManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def open(self, *, adapter_name: str, headless: bool | None = None) -> BrowserSession:
        try:
            from playwright.sync_api import sync_playwright
        except ModuleNotFoundError as exc:
            raise RuntimeError("Playwright is not installed. Run `pip install -e .`.") from exc

        profile_dir = self._profile_dir(adapter_name)
        profile_dir.mkdir(parents=True, exist_ok=True)
        playwright = sync_playwright().start()
        temp_profile_dir: Path | None = None
        try:
            context = self._launch_context(playwright, profile_dir, headless=headless)
        except Exception:
            if adapter_name not in {"handshake", "handshake_fellow"}:
                playwright.stop()
                raise
            temp_profile_dir = Path(tempfile.mkdtemp(prefix="job-hunter-handshake-", dir="/tmp"))
            self._clone_profile_dir(profile_dir, temp_profile_dir)
            try:
                context = self._launch_context(playwright, temp_profile_dir, headless=headless)
            except Exception:
                playwright.stop()
                raise
        context.set_default_timeout(self.settings.apply_page_timeout_seconds * 1000)
        context.add_init_script(
            """
            Object.defineProperty(navigator, 'language', { get: () => 'en-US' });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
            """
        )
        session = PlaywrightBrowserSession(context)
        original_close = session.close

        def _close() -> None:
            try:
                original_close()
            finally:
                if temp_profile_dir is not None:
                    shutil.rmtree(temp_profile_dir, ignore_errors=True)
                playwright.stop()

        session.close = _close  # type: ignore[method-assign]
        return session

    def _launch_context(self, playwright, profile_dir: Path, *, headless: bool | None = None):
        return playwright.chromium.launch_persistent_context(
            str(profile_dir),
            channel="chrome",
            headless=self.settings.apply_headless if headless is None else headless,
            user_agent=_DEFAULT_CHROME_USER_AGENT,
            args=[
                "--lang=en-US",
                "--disable-translate",
                "--disable-features=Translate,TranslateUI",
                "--translate-script-url=",
            ],
            locale="en-US",
            timezone_id="America/Bogota",
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )

    def _clone_profile_dir(self, source: Path, destination: Path) -> None:
        for child in source.iterdir():
            target = destination / child.name
            if child.is_dir():
                shutil.copytree(child, target, dirs_exist_ok=True)
            elif child.is_file():
                shutil.copy2(child, target)

    def _profile_dir(self, adapter_name: str) -> Path:
        if adapter_name == "linkedin":
            return Path(self.settings.linkedin_profile_dir).expanduser()
        if adapter_name in {"handshake", "handshake_fellow"}:
            return Path(self.settings.handshake_profile_dir).expanduser()
        return Path(self.settings.apply_browser_profile_dir).expanduser()
