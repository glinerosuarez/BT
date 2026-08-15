from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class BrowserApi:
    sync_playwright: Callable[[], Any]
    timeout_error: type[Exception]
    name: str


def normalize_browser_backend(value: str) -> str:
    backend = value.strip().lower()
    if backend in {"", "playwright"}:
        return "playwright"
    if backend == "patchright":
        return backend
    raise ValueError("Browser backend must be 'playwright' or 'patchright'.")


def load_browser_api(backend: str) -> BrowserApi:
    normalized = normalize_browser_backend(backend)
    if normalized == "playwright":
        from playwright.sync_api import TimeoutError, sync_playwright

        return BrowserApi(sync_playwright=sync_playwright, timeout_error=TimeoutError, name=normalized)

    from patchright.sync_api import TimeoutError, sync_playwright

    return BrowserApi(sync_playwright=sync_playwright, timeout_error=TimeoutError, name=normalized)
