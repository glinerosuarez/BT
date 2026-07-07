from __future__ import annotations

import argparse
import json
import secrets
import threading
import time
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"


@dataclass
class GoogleInstalledClient:
    client_id: str
    client_secret: str
    auth_uri: str
    token_uri: str


@dataclass
class OAuthCallbackResult:
    code: str = ""
    state: str = ""
    error: str = ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap Gmail OAuth tokens for Greenhouse email verification")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init")
    init_parser.add_argument("--client-secret-file", required=True)
    init_parser.add_argument("--dotenv-path", default=".env")
    init_parser.add_argument("--port", type=int, default=8765)
    init_parser.add_argument("--no-open", action="store_true")

    args = parser.parse_args()
    if args.command == "init":
        return _run_init(
            client_secret_file=Path(args.client_secret_file).expanduser(),
            dotenv_path=Path(args.dotenv_path).expanduser(),
            port=args.port,
            open_browser=not args.no_open,
        )
    return 1


def _run_init(*, client_secret_file: Path, dotenv_path: Path, port: int, open_browser: bool) -> int:
    client = load_google_installed_client(client_secret_file)
    redirect_uri = f"http://localhost:{port}"
    state = secrets.token_urlsafe(24)
    callback_result = OAuthCallbackResult()
    server = _start_callback_server(port=port, callback_result=callback_result)
    try:
        auth_url = build_authorization_url(client=client, redirect_uri=redirect_uri, state=state)
        print(f"Authorization URL:\n{auth_url}\n")
        if open_browser:
            webbrowser.open(auth_url)
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            if callback_result.error:
                print(f"OAuth error: {callback_result.error}")
                return 1
            if callback_result.code:
                break
            time.sleep(0.2)
        if not callback_result.code:
            print("Timed out waiting for the OAuth callback.")
            return 1
        if callback_result.state != state:
            print("State mismatch in OAuth callback.")
            return 1
        tokens = exchange_code_for_tokens(
            client=client,
            code=callback_result.code,
            redirect_uri=redirect_uri,
        )
        persist_gmail_env(
            dotenv_path=dotenv_path,
            client=client,
            tokens=tokens,
        )
        print(f"Stored Gmail OAuth values in {dotenv_path}")
        return 0
    finally:
        server.shutdown()
        server.server_close()


def load_google_installed_client(path: Path) -> GoogleInstalledClient:
    payload = json.loads(path.read_text(encoding="utf-8"))
    installed = payload.get("installed") or {}
    client_id = str(installed.get("client_id") or "").strip()
    client_secret = str(installed.get("client_secret") or "").strip()
    auth_uri = str(installed.get("auth_uri") or _AUTH_URL).strip()
    token_uri = str(installed.get("token_uri") or _TOKEN_URL).strip()
    if not client_id or not client_secret:
        raise RuntimeError(f"Invalid Google client secret file: {path}")
    return GoogleInstalledClient(
        client_id=client_id,
        client_secret=client_secret,
        auth_uri=auth_uri,
        token_uri=token_uri,
    )


def build_authorization_url(*, client: GoogleInstalledClient, redirect_uri: str, state: str) -> str:
    params = {
        "client_id": client.client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": _GMAIL_READONLY_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    }
    return f"{client.auth_uri}?{urlencode(params)}"


def exchange_code_for_tokens(*, client: GoogleInstalledClient, code: str, redirect_uri: str) -> dict[str, str]:
    data = urlencode(
        {
            "client_id": client.client_id,
            "client_secret": client.client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        }
    ).encode("utf-8")
    request = Request(client.token_uri, data=data, method="POST")
    request.add_header("Accept", "application/json")
    request.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urlopen(request, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return {
        "access_token": str(payload.get("access_token") or ""),
        "refresh_token": str(payload.get("refresh_token") or ""),
    }


def persist_gmail_env(*, dotenv_path: Path, client: GoogleInstalledClient, tokens: dict[str, str]) -> None:
    dotenv_path.parent.mkdir(parents=True, exist_ok=True)
    if dotenv_path.exists():
        lines = dotenv_path.read_text(encoding="utf-8").splitlines()
    else:
        lines = []
    updates = {
        "JOB_HUNTER_APPLY_GMAIL_VERIFICATION_ENABLED": "true",
        "JOB_HUNTER_APPLY_GMAIL_CLIENT_ID": client.client_id,
        "JOB_HUNTER_APPLY_GMAIL_CLIENT_SECRET": client.client_secret,
        "JOB_HUNTER_APPLY_GMAIL_ACCESS_TOKEN": tokens.get("access_token", ""),
        "JOB_HUNTER_APPLY_GMAIL_REFRESH_TOKEN": tokens.get("refresh_token", ""),
    }
    for key, value in updates.items():
        lines = _upsert_env_line(lines, key, value)
    payload = "\n".join(lines).rstrip() + "\n"
    dotenv_path.write_text(payload, encoding="utf-8")


def _upsert_env_line(lines: list[str], key: str, value: str) -> list[str]:
    entry = f"{key}={value}"
    updated = []
    replaced = False
    prefix = f"{key}="
    for line in lines:
        if line.startswith(prefix):
            updated.append(entry)
            replaced = True
        else:
            updated.append(line)
    if not replaced:
        updated.append(entry)
    return updated


def _start_callback_server(*, port: int, callback_result: OAuthCallbackResult) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            query = parse_qs(urlparse(self.path).query)
            callback_result.code = (query.get("code") or [""])[0]
            callback_result.state = (query.get("state") or [""])[0]
            callback_result.error = (query.get("error") or [""])[0]
            body = b"Gmail authorization received. You can close this window."
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            return

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


if __name__ == "__main__":
    raise SystemExit(main())
