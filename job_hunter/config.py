from __future__ import annotations

import os
from dataclasses import dataclass


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    db_path: str
    poll_interval_minutes: int
    request_timeout_seconds: int

    use_arbeitnow: bool
    use_remotive: bool
    use_themuse: bool

    min_relevance_score: float
    min_eligibility_confidence: float
    notify_on_ambiguous_eligibility: bool

    telegram_bot_token: str | None
    telegram_chat_id: str | None


DEFAULT_DB_PATH = "job_hunter.db"


def load_settings() -> Settings:
    return Settings(
        db_path=os.getenv("JOB_HUNTER_DB_PATH", DEFAULT_DB_PATH),
        poll_interval_minutes=_env_int("JOB_HUNTER_POLL_INTERVAL_MINUTES", 15),
        request_timeout_seconds=_env_int("JOB_HUNTER_REQUEST_TIMEOUT_SECONDS", 20),
        use_arbeitnow=_env_bool("JOB_HUNTER_SOURCE_ARBEITNOW", True),
        use_remotive=_env_bool("JOB_HUNTER_SOURCE_REMOTIVE", True),
        use_themuse=_env_bool("JOB_HUNTER_SOURCE_THEMUSE", True),
        min_relevance_score=_env_float("JOB_HUNTER_MIN_RELEVANCE_SCORE", 2.0),
        min_eligibility_confidence=_env_float("JOB_HUNTER_MIN_ELIGIBILITY_CONFIDENCE", 0.4),
        notify_on_ambiguous_eligibility=_env_bool("JOB_HUNTER_NOTIFY_AMBIGUOUS", False),
        telegram_bot_token=os.getenv("JOB_HUNTER_TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=os.getenv("JOB_HUNTER_TELEGRAM_CHAT_ID"),
    )
