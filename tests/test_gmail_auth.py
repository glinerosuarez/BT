from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from job_hunter.gmail_auth import (
    GoogleInstalledClient,
    build_authorization_url,
    load_google_installed_client,
    persist_gmail_env,
)


class GmailAuthTests(unittest.TestCase):
    def test_load_google_installed_client_reads_client_secret_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "client.json"
            path.write_text(
                json.dumps(
                    {
                        "installed": {
                            "client_id": "client-id",
                            "client_secret": "client-secret",
                            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                            "token_uri": "https://oauth2.googleapis.com/token",
                        }
                    }
                ),
                encoding="utf-8",
            )
            client = load_google_installed_client(path)
            self.assertEqual(client.client_id, "client-id")
            self.assertEqual(client.client_secret, "client-secret")

    def test_build_authorization_url_requests_offline_gmail_readonly_access(self) -> None:
        client = GoogleInstalledClient(
            client_id="client-id",
            client_secret="client-secret",
            auth_uri="https://accounts.google.com/o/oauth2/v2/auth",
            token_uri="https://oauth2.googleapis.com/token",
        )
        url = build_authorization_url(client=client, redirect_uri="http://localhost:8765", state="state-123")
        self.assertIn("access_type=offline", url)
        self.assertIn("prompt=consent", url)
        self.assertIn("gmail.readonly", url)
        self.assertIn("state=state-123", url)

    def test_persist_gmail_env_upserts_required_entries(self) -> None:
        client = GoogleInstalledClient(
            client_id="client-id",
            client_secret="client-secret",
            auth_uri="https://accounts.google.com/o/oauth2/v2/auth",
            token_uri="https://oauth2.googleapis.com/token",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text("EXISTING=1\nJOB_HUNTER_APPLY_GMAIL_ACCESS_TOKEN=old\n", encoding="utf-8")
            persist_gmail_env(
                dotenv_path=env_path,
                client=client,
                tokens={"access_token": "new-access", "refresh_token": "new-refresh"},
            )
            payload = env_path.read_text(encoding="utf-8")
            self.assertIn("EXISTING=1", payload)
            self.assertIn("JOB_HUNTER_APPLY_GMAIL_VERIFICATION_ENABLED=true", payload)
            self.assertIn("JOB_HUNTER_APPLY_GMAIL_CLIENT_ID=client-id", payload)
            self.assertIn("JOB_HUNTER_APPLY_GMAIL_CLIENT_SECRET=client-secret", payload)
            self.assertIn("JOB_HUNTER_APPLY_GMAIL_ACCESS_TOKEN=new-access", payload)
            self.assertIn("JOB_HUNTER_APPLY_GMAIL_REFRESH_TOKEN=new-refresh", payload)


if __name__ == "__main__":
    unittest.main()
