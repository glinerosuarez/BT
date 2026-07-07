from __future__ import annotations

import base64
import json
import re
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus, urlencode
from urllib.request import Request, urlopen

from job_hunter.config import Settings

_GMAIL_API_ROOT = "https://gmail.googleapis.com/gmail/v1/users/me"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_CODE_PATTERNS = (
    re.compile(r"(?:security code|copy and paste this code[^:]*|enter this code[^:]*)[:\s]+([A-Za-z0-9]{8})", re.IGNORECASE),
    re.compile(r"\b([A-Z0-9]{8})\b"),
    re.compile(r"\b([a-zA-Z0-9]{8})\b"),
)


class GmailVerificationCodeClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._cached_access_token = settings.apply_gmail_access_token

    def is_enabled(self) -> bool:
        if not self.settings.apply_gmail_verification_enabled:
            return False
        if self.settings.apply_gmail_access_token:
            return True
        return bool(
            self.settings.apply_gmail_refresh_token
            and self.settings.apply_gmail_client_id
            and self.settings.apply_gmail_client_secret
        )

    def poll_for_greenhouse_code(self, *, recipient_email: str, requested_at: datetime) -> str | None:
        deadline = time.monotonic() + max(self.settings.apply_gmail_poll_timeout_seconds, 1)
        while time.monotonic() < deadline:
            code = self._fetch_recent_code(recipient_email=recipient_email, requested_at=requested_at)
            if code:
                return code
            time.sleep(max(self.settings.apply_gmail_poll_interval_seconds, 1))
        return None

    def _fetch_recent_code(self, *, recipient_email: str, requested_at: datetime) -> str | None:
        window_minutes = max(5, int(self.settings.apply_gmail_poll_timeout_seconds / 60) + 5)
        query = f'to:{recipient_email} newer_than:{window_minutes}m -in:trash'
        search_url = f"{_GMAIL_API_ROOT}/messages?{urlencode({'q': query, 'maxResults': 10})}"
        search_payload = self._request_json(search_url)
        messages = search_payload.get("messages") or []
        sender_filter = self.settings.apply_gmail_sender_filter.strip().lower()
        for message in messages:
            message_id = str(message.get("id") or "").strip()
            if not message_id:
                continue
            payload = self._request_json(f"{_GMAIL_API_ROOT}/messages/{quote_plus(message_id)}?format=full")
            if not self._message_is_recent_enough(payload, requested_at):
                continue
            if sender_filter and sender_filter not in self._sender_text(payload):
                continue
            code = extract_verification_code(_message_search_text(payload))
            if code:
                return code
        return None

    def _message_is_recent_enough(self, payload: dict[str, Any], requested_at: datetime) -> bool:
        internal_ms = str(payload.get("internalDate") or "").strip()
        if internal_ms.isdigit():
            sent_at = datetime.fromtimestamp(int(internal_ms) / 1000, tz=timezone.utc)
            return sent_at >= requested_at - timedelta(minutes=2)
        date_header = _header_value(payload, "Date")
        if date_header:
            try:
                parsed = parsedate_to_datetime(date_header)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return parsed.astimezone(timezone.utc) >= requested_at - timedelta(minutes=2)
            except Exception:
                return True
        return True

    def _sender_text(self, payload: dict[str, Any]) -> str:
        return " ".join(
            value.lower()
            for value in (
                _header_value(payload, "From"),
                _header_value(payload, "Sender"),
                _header_value(payload, "Return-Path"),
                str(payload.get("snippet") or ""),
            )
            if value
        )

    def _request_json(self, url: str, *, method: str = "GET", data: bytes | None = None) -> dict[str, Any]:
        token = self._access_token()
        request = Request(url, data=data, method=method)
        request.add_header("Authorization", f"Bearer {token}")
        request.add_header("Accept", "application/json")
        if data is not None:
            request.add_header("Content-Type", "application/x-www-form-urlencoded")
        try:
            with urlopen(request, timeout=20) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"Gmail API request failed: {exc.code} {body}") from exc
        except URLError as exc:
            raise RuntimeError(f"Gmail API request failed: {exc.reason}") from exc

    def _access_token(self) -> str:
        if self._cached_access_token:
            return self._cached_access_token
        refresh_token = self.settings.apply_gmail_refresh_token or ""
        client_id = self.settings.apply_gmail_client_id or ""
        client_secret = self.settings.apply_gmail_client_secret or ""
        if not (refresh_token and client_id and client_secret):
            raise RuntimeError("Gmail verification is enabled but no Gmail OAuth credentials are configured.")
        payload = urlencode(
            {
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            }
        ).encode("utf-8")
        request = Request(_TOKEN_URL, data=payload, method="POST")
        request.add_header("Accept", "application/json")
        request.add_header("Content-Type", "application/x-www-form-urlencoded")
        try:
            with urlopen(request, timeout=20) as response_handle:
                response = json.loads(response_handle.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"Gmail token refresh failed: {exc.code} {body}") from exc
        except URLError as exc:
            raise RuntimeError(f"Gmail token refresh failed: {exc.reason}") from exc
        token = str(response.get("access_token") or "").strip()
        if not token:
            raise RuntimeError("Gmail token refresh did not return an access token.")
        self._cached_access_token = token
        return token


def extract_verification_code(text: str) -> str | None:
    for pattern in _CODE_PATTERNS:
        for match in pattern.finditer(text):
            code = match.group(1).strip()
            if len(code) == 8:
                return code
    return None


def _message_search_text(payload: dict[str, Any]) -> str:
    parts = [str(payload.get("snippet") or ""), _header_value(payload, "Subject"), _header_value(payload, "From")]
    body = _collect_body_text(payload.get("payload") or {})
    if body:
        parts.append(body)
    return "\n".join(part for part in parts if part)


def _collect_body_text(part: dict[str, Any]) -> str:
    texts: list[str] = []
    body = part.get("body") or {}
    data = str(body.get("data") or "")
    if data:
        decoded = _decode_base64url(data)
        if decoded:
            texts.append(decoded)
    for child in part.get("parts") or []:
        texts.append(_collect_body_text(child))
    return "\n".join(text for text in texts if text)


def _decode_base64url(value: str) -> str:
    padded = value + "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(padded.encode("utf-8")).decode("utf-8", errors="ignore")
    except Exception:
        return ""


def _header_value(payload: dict[str, Any], name: str) -> str:
    for header in (payload.get("payload") or {}).get("headers") or []:
        if str(header.get("name") or "").lower() == name.lower():
            return str(header.get("value") or "")
    return ""
