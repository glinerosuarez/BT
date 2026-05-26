from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


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


DEFAULT_GREENHOUSE_BOARDS = ["airbnb", "databricks", "discord", "stripe"]
DEFAULT_LEVER_COMPANIES = ["atlassian", "lever", "plaid"]
DEFAULT_RSS_FEEDS = [
    "https://remoteok.com/remote-internship-jobs.rss",
    "https://weworkremotely.com/categories/remote-programming-jobs.rss",
]
DEFAULT_TITLE_BLACKLIST_PATTERNS = [
    r"\brecruiter\b",
    r"\brecruiting\b",
    r"\btalent\b",
    r"\bhuman resources\b",
    r"\bhr\b",
    r"\bpeople operations\b",
    r"\bmanager\b",
    r"\bdirector\b",
]
DEFAULT_DATA_DIR = Path(__file__).resolve().parent / "data"
DEFAULT_GREENHOUSE_TOKEN_FILE = str(DEFAULT_DATA_DIR / "greenhouse_tokens.txt")
DEFAULT_LEVER_TOKEN_FILE = str(DEFAULT_DATA_DIR / "lever_tokens.txt")
DEFAULT_RSS_FEED_FILE = str(DEFAULT_DATA_DIR / "rss_feeds.txt")


def _read_list_file(path_value: str | None) -> list[str]:
    if not path_value:
        return []
    path = Path(path_value).expanduser()
    if not path.exists() or not path.is_file():
        return []

    items: list[str] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text or text.startswith("#"):
                continue
            items.append(text)
    return items


def _merge_unique(primary: list[str], secondary: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for value in [*primary, *secondary]:
        key = value.strip()
        if not key:
            continue
        lowered = key.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        merged.append(key)
    return merged


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
    title_blacklist_patterns: list[str]
    greenhouse_token_file: str | None
    lever_token_file: str | None
    rss_feed_file: str | None

    usajobs_user_agent: str | None
    usajobs_auth_key: str | None
    usajobs_results_per_page: int

    adzuna_app_id: str | None
    adzuna_app_key: str | None
    adzuna_country: str
    adzuna_pages: int


DEFAULT_DB_PATH = "job_hunter.db"


def load_settings() -> Settings:
    greenhouse_token_file = os.getenv("JOB_HUNTER_GREENHOUSE_TOKEN_FILE", DEFAULT_GREENHOUSE_TOKEN_FILE)
    lever_token_file = os.getenv("JOB_HUNTER_LEVER_TOKEN_FILE", DEFAULT_LEVER_TOKEN_FILE)
    rss_feed_file = os.getenv("JOB_HUNTER_RSS_FEED_FILE", DEFAULT_RSS_FEED_FILE)

    greenhouse_boards = _merge_unique(
        _read_list_file(greenhouse_token_file),
        _env_csv("JOB_HUNTER_GREENHOUSE_BOARDS", DEFAULT_GREENHOUSE_BOARDS),
    )
    lever_companies = _merge_unique(
        _read_list_file(lever_token_file),
        _env_csv("JOB_HUNTER_LEVER_COMPANIES", DEFAULT_LEVER_COMPANIES),
    )
    rss_feeds = _merge_unique(
        _read_list_file(rss_feed_file),
        _env_csv("JOB_HUNTER_RSS_FEEDS", DEFAULT_RSS_FEEDS),
    )

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
        greenhouse_boards=greenhouse_boards,
        lever_companies=lever_companies,
        rss_feeds=rss_feeds,
        title_blacklist_patterns=_env_csv("JOB_HUNTER_TITLE_BLACKLIST_PATTERNS", DEFAULT_TITLE_BLACKLIST_PATTERNS),
        greenhouse_token_file=greenhouse_token_file,
        lever_token_file=lever_token_file,
        rss_feed_file=rss_feed_file,
        usajobs_user_agent=os.getenv("JOB_HUNTER_USAJOBS_USER_AGENT"),
        usajobs_auth_key=os.getenv("JOB_HUNTER_USAJOBS_AUTH_KEY"),
        usajobs_results_per_page=_env_int("JOB_HUNTER_USAJOBS_RESULTS_PER_PAGE", 250),
        adzuna_app_id=os.getenv("JOB_HUNTER_ADZUNA_APP_ID"),
        adzuna_app_key=os.getenv("JOB_HUNTER_ADZUNA_APP_KEY"),
        adzuna_country=os.getenv("JOB_HUNTER_ADZUNA_COUNTRY", "us"),
        adzuna_pages=_env_int("JOB_HUNTER_ADZUNA_PAGES", 2),
    )
