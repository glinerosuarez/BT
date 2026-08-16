from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from job_hunter.orchestrator.store import OrchestratorStore


@dataclass(frozen=True)
class TelegramCommand:
    update_id: int
    action: str
    target_id: int | None


class TelegramController:
    def __init__(
        self,
        *,
        bot_token: str,
        chat_id: str,
        store: OrchestratorStore,
        timeout_seconds: int = 20,
    ) -> None:
        self.bot_token = bot_token
        self.chat_id = str(chat_id)
        self.store = store
        self.timeout_seconds = max(timeout_seconds, 1)

    def send_text(self, text: str) -> bool:
        payload = self._request(
            "sendMessage",
            {"chat_id": self.chat_id, "text": text, "disable_web_page_preview": "true"},
        )
        return bool(payload.get("ok"))

    def poll(self, *, long_poll_seconds: int = 0) -> list[TelegramCommand]:
        offset = int(self.store.get_state("telegram_update_offset") or 0)
        payload = self._request(
            "getUpdates",
            {
                "offset": str(offset),
                "timeout": str(max(long_poll_seconds, 0)),
                "allowed_updates": json.dumps(["message"]),
            },
            timeout_seconds=max(self.timeout_seconds, long_poll_seconds + 5),
        )
        commands: list[TelegramCommand] = []
        results = payload.get("result", []) if isinstance(payload, dict) else []
        for update in results if isinstance(results, list) else []:
            if not isinstance(update, dict):
                continue
            update_id = int(update.get("update_id") or 0)
            self.store.set_state("telegram_update_offset", update_id + 1)
            message = update.get("message")
            if not isinstance(message, dict):
                continue
            chat = message.get("chat")
            if not isinstance(chat, dict) or str(chat.get("id") or "") != self.chat_id:
                continue
            command = _parse_command(str(message.get("text") or ""), update_id=update_id)
            if command is not None:
                commands.append(command)
        return commands

    def wait_for_gate(self, intervention_id: int) -> str:
        self.send_text(
            f"Browser opened for intervention {intervention_id}. Complete the manual step, then send "
            f"/continue {intervention_id} or /skip {intervention_id}."
        )
        while True:
            for command in self.poll(long_poll_seconds=20):
                if command.target_id != intervention_id:
                    continue
                if command.action == "continue":
                    return "continue"
                if command.action == "skip":
                    raise RuntimeError("manual_gate_skipped")
            time.sleep(1)

    def _request(
        self,
        method: str,
        values: dict[str, str],
        *,
        timeout_seconds: int | None = None,
    ) -> dict[str, object]:
        endpoint = f"https://api.telegram.org/bot{self.bot_token}/{method}"
        data = urllib.parse.urlencode(values).encode("utf-8")
        request = urllib.request.Request(endpoint, data=data, method="POST")
        try:
            with urllib.request.urlopen(
                request,
                timeout=timeout_seconds or self.timeout_seconds,
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"telegram_{method}_failed:{type(exc).__name__}") from exc
        return payload if isinstance(payload, dict) else {}


def _parse_command(text: str, *, update_id: int) -> TelegramCommand | None:
    parts = text.strip().split()
    if not parts:
        return None
    action = parts[0].split("@", 1)[0].lstrip("/").lower()
    if action not in {"status", "open", "retry", "continue", "skip"}:
        return None
    target_id = None
    if action != "status":
        if len(parts) < 2:
            return None
        try:
            target_id = int(parts[1])
        except ValueError:
            return None
    return TelegramCommand(update_id=update_id, action=action, target_id=target_id)
