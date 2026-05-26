from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass


@dataclass(slots=True)
class SourceConnector:
    name: str

    def fetch(self, timeout_seconds: int) -> list[dict]:
        raise NotImplementedError


USER_AGENT = "job-hunter/0.1 (+internship-sourcing)"


def get_json(
    url: str,
    timeout_seconds: int,
    params: dict | None = None,
    headers: dict[str, str] | None = None,
) -> dict:
    if params:
        query = urllib.parse.urlencode(params)
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}{query}"

    req_headers = {"User-Agent": USER_AGENT}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers)
    with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
        payload = resp.read().decode("utf-8")
    return json.loads(payload)
