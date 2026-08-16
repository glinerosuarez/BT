from __future__ import annotations

import os
import shlex
from dataclasses import dataclass, field
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


def _derive_quarantine_file(path_value: str | None, fallback: str) -> str:
    base = Path(path_value or fallback).expanduser()
    suffix = base.suffix
    if suffix:
        return str(base.with_name(f"{base.stem}.quarantine{suffix}"))
    return str(base.with_name(f"{base.name}.quarantine"))


DEFAULT_GREENHOUSE_BOARDS = ["airbnb", "databricks", "discord", "stripe"]
DEFAULT_LEVER_COMPANIES = ["atlassian", "lever", "plaid"]
DEFAULT_RSS_FEEDS = [
    "https://remoteok.com/remote-internship-jobs.rss",
    "https://weworkremotely.com/categories/remote-programming-jobs.rss",
]
DEFAULT_GITHUB_REPO_READMES = [
    "https://raw.githubusercontent.com/vanshb03/Summer2027-Internships/dev/README.md",
]
DEFAULT_ASHBY_BOARDS = [
    "Etched",
    "apex-technology-inc",
    "homebase",
]
DEFAULT_HANDSHAKE_PROFILE_DIR = str(Path(__file__).resolve().parent.parent / ".handshake-profile")
DEFAULT_LINKEDIN_PROFILE_DIR = str(Path(__file__).resolve().parent.parent / ".linkedin-profile")
DEFAULT_INTERSTRIDE_PROFILE_DIR = str(Path(__file__).resolve().parent.parent / ".interstride-profile")
DEFAULT_INTERSTRIDE_SEARCH_URLS = ["https://student.interstride.com/jobs/search"]
DEFAULT_APPLE_QUERIES = [
    "machine learning and artificial intelligence masters internships",
    "machine learning and artificial intelligence undergrad internships",
    "data engineer intern",
    "data science intern",
    "ai ml intern",
    "software engineer intern",
    "backend engineer intern",
    "applied scientist intern",
    "data platform intern",
]
DEFAULT_HIRING_CAFE_SEARCH_URLS = [
    "https://hiring.cafe/jobs/machine-learning-intern-united-states",
    "https://hiring.cafe/jobs/ai-research-intern-united-states",
    "https://hiring.cafe/jobs/applied-ml-intern-united-states",
    "https://hiring.cafe/jobs/data-engineer-intern-united-states",
    "https://hiring.cafe/jobs/data-intern-united-states",
    "https://hiring.cafe/jobs/analytics-engineer-intern-united-states",
    "https://hiring.cafe/jobs/software-engineer-intern-ai-united-states",
    "https://hiring.cafe/jobs/backend-engineer-intern-united-states",
    "https://hiring.cafe/jobs/ml-intern-united-states",
    "https://hiring.cafe/jobs/data-platform-intern-united-states",
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
DEFAULT_DATA_ROLE_TITLE_PATTERNS = [
    r"\b(machine learning|ml)\b",
    r"\bdata (science|scientist)\b",
    r"\bdata engineer(ing)?\b",
    r"\banalytics engineer\b",
    r"\b(applied|research) scientist\b",
    r"\bquant(itative)?\b",
]
DEFAULT_NON_DATA_TITLE_PATTERNS = [
    r"\bdeveloper advocacy\b",
    r"\bgo[- ]to[- ]market\b",
    r"\b(content|video content|editorial)\b",
    r"\b(sales|marketing|partnerships?)\b",
    r"\bcustomer success\b",
]
DEFAULT_POLICY_REJECT_PATTERNS = [
    r"\bph\.?d\.?\b",
    r"\bdoctoral\b",
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
    use_github_repos: bool
    use_ashby: bool
    use_handshake: bool
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
    github_repo_readmes: list[str]
    ashby_boards: list[str]
    handshake_search_urls: list[str]
    title_blacklist_patterns: list[str]
    data_role_title_patterns: list[str]
    non_data_title_patterns: list[str]
    policy_reject_patterns: list[str]
    min_data_signal_count: int
    greenhouse_token_file: str | None
    lever_token_file: str | None
    rss_feed_file: str | None
    greenhouse_quarantine_file: str | None
    lever_quarantine_file: str | None
    rss_quarantine_file: str | None
    source_failure_quarantine_threshold: int
    source_restore_success_threshold: int
    source_probe_limit_per_run: int
    handshake_profile_dir: str
    handshake_headless: bool
    handshake_max_results: int
    handshake_page_timeout_seconds: int
    handshake_fetch_details: bool

    usajobs_user_agent: str | None
    usajobs_auth_key: str | None
    usajobs_results_per_page: int

    adzuna_app_id: str | None
    adzuna_app_key: str | None
    adzuna_country: str
    adzuna_pages: int

    tailoring_profile_root: str = "profiles"
    tailoring_output_root: str = "artifacts/tailoring"
    tailoring_provider: str = "anthropic"
    tailoring_anthropic_model: str | None = None
    tailoring_batch_default_limit: int = 10
    apply_provider: str = "anthropic"
    apply_anthropic_model: str | None = None
    apply_browser_profile_dir: str = ".job-apply-profile"
    apply_headless: bool = True
    apply_page_timeout_seconds: int = 30
    apply_batch_default_limit: int = 5
    apply_output_root: str = "artifacts/applications"
    apply_gmail_verification_enabled: bool = False
    apply_gmail_access_token: str | None = None
    apply_gmail_refresh_token: str | None = None
    apply_gmail_client_id: str | None = None
    apply_gmail_client_secret: str | None = None
    apply_gmail_poll_timeout_seconds: int = 120
    apply_gmail_poll_interval_seconds: int = 5
    apply_gmail_sender_filter: str = "greenhouse"
    use_linkedin: bool = False
    linkedin_search_urls: list[str] = field(default_factory=list)
    linkedin_profile_dir: str = DEFAULT_LINKEDIN_PROFILE_DIR
    linkedin_headless: bool = True
    linkedin_max_results: int = 25
    linkedin_page_timeout_seconds: int = 30
    linkedin_fetch_details: bool = True
    use_interstride: bool = False
    interstride_search_urls: list[str] = field(default_factory=lambda: list(DEFAULT_INTERSTRIDE_SEARCH_URLS))
    interstride_profile_dir: str = DEFAULT_INTERSTRIDE_PROFILE_DIR
    interstride_headless: bool = True
    interstride_max_results: int = 25
    interstride_page_timeout_seconds: int = 30
    interstride_fetch_details: bool = True
    use_apple: bool = False
    apple_queries: list[str] = field(default_factory=lambda: list(DEFAULT_APPLE_QUERIES))
    apple_max_results: int = 25
    apple_headless: bool = True
    apple_page_timeout_seconds: int = 30
    use_hiring_cafe: bool = False
    hiring_cafe_search_urls: list[str] = field(default_factory=lambda: list(DEFAULT_HIRING_CAFE_SEARCH_URLS))
    hiring_cafe_max_results: int = 20
    handshake_recent_pages: int = 10
    handshake_use_keyword_supplemental: bool = False
    handshake_direct_job_urls: list[str] = field(default_factory=list)
    orchestrator_daily_attempt_limit: int = 5
    orchestrator_checkpoint_db_path: str = "artifacts/orchestrator/checkpoints.db"
    orchestrator_profiles: list[str] = field(
        default_factory=lambda: ["backend", "data_intern", "ml_eng_intern"]
    )
    orchestrator_timezone: str = "America/Bogota"
    orchestrator_new_jobs_only: bool = True
    orchestrator_agent_timeout_seconds: int = 120
    orchestrator_agent_max_retries: int = 2
    orchestrator_provider: str = "openai"
    orchestrator_model: str | None = None
    sourcing_provider: str = "openai"
    sourcing_model: str | None = None
    writer_provider: str = "anthropic"
    writer_model: str | None = None
    applier_provider: str = "openai"
    applier_model: str | None = None
    nvidia_api_key: str | None = None
    nvidia_nim_base_url: str = "https://integrate.api.nvidia.com/v1"
    nvidia_nim_structured_output_method: str = "json_schema"
    tavily_api_key: str | None = None
    source_discovery_daily_credit_limit: int = 20
    source_discovery_max_results: int = 5
    phoenix_enabled: bool = True
    phoenix_collector_endpoint: str = "http://127.0.0.1:6006/v1/traces"
    phoenix_project_name: str = "job-hunter-orchestrator"


DEFAULT_DB_PATH = "job_hunter.db"


def _load_dotenv_file(path_value: str = ".env") -> None:
    path = Path(path_value).expanduser()
    if not path.exists() or not path.is_file():
        return

    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if not key or key in os.environ:
                continue
            parsed = value.strip()
            if parsed:
                try:
                    parsed = shlex.split(parsed, comments=False)[0]
                except ValueError:
                    parsed = parsed.strip("\"'")
            os.environ[key] = parsed


def load_settings(*, load_dotenv: bool = False, dotenv_path: str = ".env") -> Settings:
    if load_dotenv:
        _load_dotenv_file(dotenv_path)
    greenhouse_token_file = os.getenv("JOB_HUNTER_GREENHOUSE_TOKEN_FILE", DEFAULT_GREENHOUSE_TOKEN_FILE)
    lever_token_file = os.getenv("JOB_HUNTER_LEVER_TOKEN_FILE", DEFAULT_LEVER_TOKEN_FILE)
    rss_feed_file = os.getenv("JOB_HUNTER_RSS_FEED_FILE", DEFAULT_RSS_FEED_FILE)
    linkedin_search_urls = _env_csv("JOB_HUNTER_LINKEDIN_SEARCH_URLS", [])
    interstride_search_urls = _env_csv("JOB_HUNTER_INTERSTRIDE_SEARCH_URLS", DEFAULT_INTERSTRIDE_SEARCH_URLS)
    apple_queries = _env_csv("JOB_HUNTER_APPLE_QUERIES", DEFAULT_APPLE_QUERIES)
    greenhouse_quarantine_file = os.getenv(
        "JOB_HUNTER_GREENHOUSE_QUARANTINE_FILE",
        _derive_quarantine_file(greenhouse_token_file, DEFAULT_GREENHOUSE_TOKEN_FILE),
    )
    lever_quarantine_file = os.getenv(
        "JOB_HUNTER_LEVER_QUARANTINE_FILE",
        _derive_quarantine_file(lever_token_file, DEFAULT_LEVER_TOKEN_FILE),
    )
    rss_quarantine_file = os.getenv(
        "JOB_HUNTER_RSS_QUARANTINE_FILE",
        _derive_quarantine_file(rss_feed_file, DEFAULT_RSS_FEED_FILE),
    )

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
    github_repo_readmes = _env_csv("JOB_HUNTER_GITHUB_REPO_READMES", DEFAULT_GITHUB_REPO_READMES)
    ashby_boards = _env_csv("JOB_HUNTER_ASHBY_BOARDS", DEFAULT_ASHBY_BOARDS)
    handshake_search_urls = _env_csv("JOB_HUNTER_HANDSHAKE_SEARCH_URLS", [])
    handshake_direct_job_urls = _env_csv("JOB_HUNTER_HANDSHAKE_DIRECT_JOB_URLS", [])
    hiring_cafe_search_urls = _env_csv("JOB_HUNTER_HIRING_CAFE_SEARCH_URLS", DEFAULT_HIRING_CAFE_SEARCH_URLS)

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
        use_github_repos=_env_bool("JOB_HUNTER_SOURCE_GITHUB_REPOS", False),
        use_ashby=_env_bool("JOB_HUNTER_SOURCE_ASHBY", True),
        use_handshake=_env_bool("JOB_HUNTER_SOURCE_HANDSHAKE", False),
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
        github_repo_readmes=github_repo_readmes,
        ashby_boards=ashby_boards,
        handshake_search_urls=handshake_search_urls,
        title_blacklist_patterns=_env_csv("JOB_HUNTER_TITLE_BLACKLIST_PATTERNS", DEFAULT_TITLE_BLACKLIST_PATTERNS),
        data_role_title_patterns=_env_csv("JOB_HUNTER_DATA_ROLE_TITLE_PATTERNS", DEFAULT_DATA_ROLE_TITLE_PATTERNS),
        non_data_title_patterns=_env_csv("JOB_HUNTER_NON_DATA_TITLE_PATTERNS", DEFAULT_NON_DATA_TITLE_PATTERNS),
        policy_reject_patterns=_env_csv("JOB_HUNTER_POLICY_REJECT_PATTERNS", DEFAULT_POLICY_REJECT_PATTERNS),
        min_data_signal_count=_env_int("JOB_HUNTER_MIN_DATA_SIGNAL_COUNT", 2),
        greenhouse_token_file=greenhouse_token_file,
        lever_token_file=lever_token_file,
        rss_feed_file=rss_feed_file,
        greenhouse_quarantine_file=greenhouse_quarantine_file,
        lever_quarantine_file=lever_quarantine_file,
        rss_quarantine_file=rss_quarantine_file,
        source_failure_quarantine_threshold=_env_int("JOB_HUNTER_SOURCE_FAILURE_QUARANTINE_THRESHOLD", 2),
        source_restore_success_threshold=_env_int("JOB_HUNTER_SOURCE_RESTORE_SUCCESS_THRESHOLD", 2),
        source_probe_limit_per_run=_env_int("JOB_HUNTER_SOURCE_PROBE_LIMIT_PER_RUN", 5),
        handshake_profile_dir=os.getenv("JOB_HUNTER_HANDSHAKE_PROFILE_DIR", DEFAULT_HANDSHAKE_PROFILE_DIR),
        handshake_headless=_env_bool("JOB_HUNTER_HANDSHAKE_HEADLESS", True),
        handshake_max_results=_env_int("JOB_HUNTER_HANDSHAKE_MAX_RESULTS", 25),
        handshake_page_timeout_seconds=_env_int("JOB_HUNTER_HANDSHAKE_PAGE_TIMEOUT_SECONDS", 30),
        handshake_fetch_details=_env_bool("JOB_HUNTER_HANDSHAKE_FETCH_DETAILS", True),
        usajobs_user_agent=os.getenv("JOB_HUNTER_USAJOBS_USER_AGENT"),
        usajobs_auth_key=os.getenv("JOB_HUNTER_USAJOBS_AUTH_KEY"),
        usajobs_results_per_page=_env_int("JOB_HUNTER_USAJOBS_RESULTS_PER_PAGE", 250),
        adzuna_app_id=os.getenv("JOB_HUNTER_ADZUNA_APP_ID"),
        adzuna_app_key=os.getenv("JOB_HUNTER_ADZUNA_APP_KEY"),
        adzuna_country=os.getenv("JOB_HUNTER_ADZUNA_COUNTRY", "us"),
        adzuna_pages=_env_int("JOB_HUNTER_ADZUNA_PAGES", 2),
        tailoring_profile_root=os.getenv("JOB_HUNTER_TAILORING_PROFILE_ROOT", "profiles"),
        tailoring_output_root=os.getenv("JOB_HUNTER_TAILORING_OUTPUT_ROOT", "artifacts/tailoring"),
        tailoring_provider=os.getenv("JOB_HUNTER_TAILORING_PROVIDER", "anthropic"),
        tailoring_anthropic_model=os.getenv("JOB_HUNTER_TAILORING_ANTHROPIC_MODEL"),
        tailoring_batch_default_limit=_env_int("JOB_HUNTER_TAILORING_BATCH_DEFAULT_LIMIT", 10),
        apply_provider=os.getenv("JOB_HUNTER_APPLY_PROVIDER", "anthropic"),
        apply_anthropic_model=os.getenv("JOB_HUNTER_APPLY_ANTHROPIC_MODEL"),
        apply_browser_profile_dir=os.getenv("JOB_HUNTER_APPLY_BROWSER_PROFILE_DIR", ".job-apply-profile"),
        apply_headless=_env_bool("JOB_HUNTER_APPLY_HEADLESS", True),
        apply_page_timeout_seconds=_env_int("JOB_HUNTER_APPLY_PAGE_TIMEOUT_SECONDS", 30),
        apply_batch_default_limit=_env_int("JOB_HUNTER_APPLY_BATCH_DEFAULT_LIMIT", 5),
        apply_output_root=os.getenv("JOB_HUNTER_APPLY_OUTPUT_ROOT", "artifacts/applications"),
        apply_gmail_verification_enabled=_env_bool("JOB_HUNTER_APPLY_GMAIL_VERIFICATION_ENABLED", False),
        apply_gmail_access_token=os.getenv("JOB_HUNTER_APPLY_GMAIL_ACCESS_TOKEN"),
        apply_gmail_refresh_token=os.getenv("JOB_HUNTER_APPLY_GMAIL_REFRESH_TOKEN"),
        apply_gmail_client_id=os.getenv("JOB_HUNTER_APPLY_GMAIL_CLIENT_ID"),
        apply_gmail_client_secret=os.getenv("JOB_HUNTER_APPLY_GMAIL_CLIENT_SECRET"),
        apply_gmail_poll_timeout_seconds=_env_int("JOB_HUNTER_APPLY_GMAIL_POLL_TIMEOUT_SECONDS", 120),
        apply_gmail_poll_interval_seconds=_env_int("JOB_HUNTER_APPLY_GMAIL_POLL_INTERVAL_SECONDS", 5),
        apply_gmail_sender_filter=os.getenv("JOB_HUNTER_APPLY_GMAIL_SENDER_FILTER", "greenhouse"),
        use_linkedin=_env_bool("JOB_HUNTER_SOURCE_LINKEDIN", False),
        linkedin_search_urls=linkedin_search_urls,
        linkedin_profile_dir=os.getenv("JOB_HUNTER_LINKEDIN_PROFILE_DIR", DEFAULT_LINKEDIN_PROFILE_DIR),
        linkedin_headless=_env_bool("JOB_HUNTER_LINKEDIN_HEADLESS", True),
        linkedin_max_results=_env_int("JOB_HUNTER_LINKEDIN_MAX_RESULTS", 25),
        linkedin_page_timeout_seconds=_env_int("JOB_HUNTER_LINKEDIN_PAGE_TIMEOUT_SECONDS", 30),
        linkedin_fetch_details=_env_bool("JOB_HUNTER_LINKEDIN_FETCH_DETAILS", True),
        use_interstride=_env_bool("JOB_HUNTER_SOURCE_INTERSTRIDE", False),
        interstride_search_urls=interstride_search_urls,
        interstride_profile_dir=os.getenv("JOB_HUNTER_INTERSTRIDE_PROFILE_DIR", DEFAULT_INTERSTRIDE_PROFILE_DIR),
        interstride_headless=_env_bool("JOB_HUNTER_INTERSTRIDE_HEADLESS", True),
        interstride_max_results=_env_int("JOB_HUNTER_INTERSTRIDE_MAX_RESULTS", 25),
        interstride_page_timeout_seconds=_env_int("JOB_HUNTER_INTERSTRIDE_PAGE_TIMEOUT_SECONDS", 30),
        interstride_fetch_details=_env_bool("JOB_HUNTER_INTERSTRIDE_FETCH_DETAILS", True),
        use_apple=_env_bool("JOB_HUNTER_SOURCE_APPLE", False),
        apple_queries=apple_queries,
        apple_max_results=_env_int("JOB_HUNTER_APPLE_MAX_RESULTS", 25),
        apple_headless=_env_bool("JOB_HUNTER_APPLE_HEADLESS", True),
        apple_page_timeout_seconds=_env_int("JOB_HUNTER_APPLE_PAGE_TIMEOUT_SECONDS", 30),
        use_hiring_cafe=_env_bool("JOB_HUNTER_SOURCE_HIRING_CAFE", False),
        hiring_cafe_search_urls=hiring_cafe_search_urls,
        hiring_cafe_max_results=_env_int("JOB_HUNTER_HIRING_CAFE_MAX_RESULTS", 20),
        handshake_recent_pages=_env_int("JOB_HUNTER_HANDSHAKE_RECENT_PAGES", 10),
        handshake_use_keyword_supplemental=_env_bool("JOB_HUNTER_HANDSHAKE_USE_KEYWORD_SUPPLEMENTAL", False),
        handshake_direct_job_urls=handshake_direct_job_urls,
        orchestrator_daily_attempt_limit=max(_env_int("JOB_HUNTER_ORCHESTRATOR_DAILY_ATTEMPT_LIMIT", 5), 0),
        orchestrator_checkpoint_db_path=os.getenv(
            "JOB_HUNTER_ORCHESTRATOR_CHECKPOINT_DB_PATH",
            "artifacts/orchestrator/checkpoints.db",
        ),
        orchestrator_profiles=_env_csv(
            "JOB_HUNTER_ORCHESTRATOR_PROFILES",
            ["backend", "data_intern", "ml_eng_intern"],
        ),
        orchestrator_timezone=os.getenv("JOB_HUNTER_ORCHESTRATOR_TIMEZONE", "America/Bogota"),
        orchestrator_new_jobs_only=_env_bool("JOB_HUNTER_ORCHESTRATOR_NEW_JOBS_ONLY", True),
        orchestrator_agent_timeout_seconds=max(
            _env_int("JOB_HUNTER_ORCHESTRATOR_AGENT_TIMEOUT_SECONDS", 120),
            1,
        ),
        orchestrator_agent_max_retries=max(
            _env_int("JOB_HUNTER_ORCHESTRATOR_AGENT_MAX_RETRIES", 2),
            0,
        ),
        orchestrator_provider=os.getenv("JOB_HUNTER_ORCHESTRATOR_PROVIDER", "openai"),
        orchestrator_model=os.getenv("JOB_HUNTER_ORCHESTRATOR_MODEL"),
        sourcing_provider=os.getenv("JOB_HUNTER_SOURCING_PROVIDER", "openai"),
        sourcing_model=os.getenv("JOB_HUNTER_SOURCING_MODEL"),
        writer_provider=os.getenv("JOB_HUNTER_WRITER_PROVIDER", "anthropic"),
        writer_model=os.getenv("JOB_HUNTER_WRITER_MODEL"),
        applier_provider=os.getenv("JOB_HUNTER_APPLIER_PROVIDER", "openai"),
        applier_model=os.getenv("JOB_HUNTER_APPLIER_MODEL"),
        nvidia_api_key=os.getenv("NVIDIA_API_KEY"),
        nvidia_nim_base_url=os.getenv(
            "JOB_HUNTER_NVIDIA_NIM_BASE_URL",
            "https://integrate.api.nvidia.com/v1",
        ),
        nvidia_nim_structured_output_method=os.getenv(
            "JOB_HUNTER_NVIDIA_NIM_STRUCTURED_OUTPUT_METHOD",
            "json_schema",
        ),
        tavily_api_key=os.getenv("TAVILY_API_KEY"),
        source_discovery_daily_credit_limit=max(
            _env_int("JOB_HUNTER_SOURCE_DISCOVERY_DAILY_CREDIT_LIMIT", 20),
            0,
        ),
        source_discovery_max_results=max(
            _env_int("JOB_HUNTER_SOURCE_DISCOVERY_MAX_RESULTS", 5),
            1,
        ),
        phoenix_enabled=_env_bool("JOB_HUNTER_PHOENIX_ENABLED", True),
        phoenix_collector_endpoint=os.getenv(
            "PHOENIX_COLLECTOR_ENDPOINT",
            "http://127.0.0.1:6006/v1/traces",
        ),
        phoenix_project_name=os.getenv("PHOENIX_PROJECT_NAME", "job-hunter-orchestrator"),
    )
