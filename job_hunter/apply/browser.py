from __future__ import annotations

from pathlib import Path
from typing import Protocol

from job_hunter.config import Settings


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
        return self._context.pages[0] if self._context.pages else self._context.new_page()

    def close(self) -> None:
        self._context.close()


class BrowserManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def open(self, *, adapter_name: str) -> BrowserSession:
        try:
            from playwright.sync_api import sync_playwright
        except ModuleNotFoundError as exc:
            raise RuntimeError("Playwright is not installed. Run `pip install -e .`.") from exc

        profile_dir = self._profile_dir(adapter_name)
        profile_dir.mkdir(parents=True, exist_ok=True)
        playwright = sync_playwright().start()
        context = playwright.chromium.launch_persistent_context(
            str(profile_dir),
            channel="chrome",
            headless=self.settings.apply_headless,
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
                playwright.stop()

        session.close = _close  # type: ignore[method-assign]
        return session

    def _profile_dir(self, adapter_name: str) -> Path:
        if adapter_name == "linkedin":
            return Path(self.settings.linkedin_profile_dir).expanduser()
        return Path(self.settings.apply_browser_profile_dir).expanduser()
