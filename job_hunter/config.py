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


def _env_csv(name: str, default: list[str]) -> list[str]:
    value = os.getenv(name)
    if value is None:
        return default
    items = [item.strip() for item in value.split(",")]
    return [item for item in items if item]


DEFAULT_GREENHOUSE_BOARDS = [
    "airbnb",
    "databricks",
    "discord",
    "stripe",
]

DEFAULT_LEVER_COMPANIES = [
    "atlassian",
    "lever",
    "plaid",
]

DEFAULT_RSS_FEEDS = [
    "https://remoteok.com/remote-internship-jobs.rss",
    "https://weworkremotely.com/categories/remote-programming-jobs.rss",
]


@dataclass(frozen=True)
class Settings:
    db_path: str
    poll_interval_minutes: int
    request_timeout_seconds: int

    use_arbeitnow: bool
    use_remotive: bool
    use_themuse: bool
    use_greenhouse: bool
    use_lever: bool
    use_rss: bool
    use_usajobs: bool
    use_adzuna: bool

    min_relevance_score: float
    min_eligibility_confidence: float
    notify_on_ambiguous_eligibility: bool
    max_posting_age_days: int

    telegram_bot_token: str | None
    telegram_chat_id: str | None
    themuse_pages: int

    greenhouse_boards: list[str]
    lever_companies: list[str]
    rss_feeds: list[str]

    usajobs_user_agent: str | None
    usajobs_auth_key: str | None
    usajobs_results_per_page: int

    adzuna_app_id: str | None
    adzuna_app_key: str | None
    adzuna_country: str
    adzuna_pages: int


DEFAULT_DB_PATH = "job_hunter.db"


def load_settings() -> Settings:
    return Settings(
        db_path=os.getenv("JOB_HUNTER_DB_PATH", DEFAULT_DB_PATH),
        poll_interval_minutes=_env_int("JOB_HUNTER_POLL_INTERVAL_MINUTES", 15),
        request_timeout_seconds=_env_int("JOB_HUNTER_REQUEST_TIMEOUT_SECONDS", 20),
        use_arbeitnow=_env_bool("JOB_HUNTER_SOURCE_ARBEITNOW", True),
        use_remotive=_env_bool("JOB_HUNTER_SOURCE_REMOTIVE", True),
        use_themuse=_env_bool("JOB_HUNTER_SOURCE_THEMUSE", True),
        use_greenhouse=_env_bool("JOB_HUNTER_SOURCE_GREENHOUSE", True),
        use_lever=_env_bool("JOB_HUNTER_SOURCE_LEVER", True),
        use_rss=_env_bool("JOB_HUNTER_SOURCE_RSS", True),
        use_usajobs=_env_bool("JOB_HUNTER_SOURCE_USAJOBS", False),
        use_adzuna=_env_bool("JOB_HUNTER_SOURCE_ADZUNA", False),
        min_relevance_score=_env_float("JOB_HUNTER_MIN_RELEVANCE_SCORE", 3.0),
        min_eligibility_confidence=_env_float("JOB_HUNTER_MIN_ELIGIBILITY_CONFIDENCE", 0.4),
        notify_on_ambiguous_eligibility=_env_bool("JOB_HUNTER_NOTIFY_AMBIGUOUS", True),
        max_posting_age_days=_env_int("JOB_HUNTER_MAX_POSTING_AGE_DAYS", 7),
        telegram_bot_token=os.getenv("JOB_HUNTER_TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=os.getenv("JOB_HUNTER_TELEGRAM_CHAT_ID"),
        themuse_pages=_env_int("JOB_HUNTER_THEMUSE_PAGES", 2),
        greenhouse_boards=_env_csv("JOB_HUNTER_GREENHOUSE_BOARDS", DEFAULT_GREENHOUSE_BOARDS),
        lever_companies=_env_csv("JOB_HUNTER_LEVER_COMPANIES", DEFAULT_LEVER_COMPANIES),
        rss_feeds=_env_csv("JOB_HUNTER_RSS_FEEDS", DEFAULT_RSS_FEEDS),
        usajobs_user_agent=os.getenv("JOB_HUNTER_USAJOBS_USER_AGENT"),
        usajobs_auth_key=os.getenv("JOB_HUNTER_USAJOBS_AUTH_KEY"),
        usajobs_results_per_page=_env_int("JOB_HUNTER_USAJOBS_RESULTS_PER_PAGE", 250),
        adzuna_app_id=os.getenv("JOB_HUNTER_ADZUNA_APP_ID"),
        adzuna_app_key=os.getenv("JOB_HUNTER_ADZUNA_APP_KEY"),
        adzuna_country=os.getenv("JOB_HUNTER_ADZUNA_COUNTRY", "us"),
        adzuna_pages=_env_int("JOB_HUNTER_ADZUNA_PAGES", 2),
    )
