from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class SourceRunStats:
    fetched_count: int = 0
    rejected_age_count: int = 0
    rejected_internship_count: int = 0
    rejected_us_scope_count: int = 0
    rejected_title_blacklist_count: int = 0
    rejected_eligibility_count: int = 0
    rejected_relevance_count: int = 0
    persisted_count: int = 0
    notified_count: int = 0
    duplicate_count: int = 0
    error_count: int = 0
    dead_token_count: int = 0
    feed_error_count: int = 0


@dataclass(slots=True)
class JobRecord:
    source: str
    external_id: str
    url: str
    title: str
    company: str
    location: str
    is_internship: bool
    posted_at: str | None
    description: str
    work_auth_signals: list[str] = field(default_factory=list)
    sponsorship_signals: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    ingested_at: str = ""

    relevance_score: float = 0.0
    eligibility_confidence: float = 0.0
    eligibility_status: str = "ambiguous"
    relevance_hits: list[str] = field(default_factory=list)
    age_days: float | None = None
    age_unknown: bool = True
    source_detail: str = ""


@dataclass(slots=True)
class PipelineOutcome:
    source_count: int = 0
    passed_filter_count: int = 0
    persisted_count: int = 0
    notified_count: int = 0
    duplicate_count: int = 0
    error_count: int = 0
    source_stats: dict[str, SourceRunStats] = field(default_factory=dict)
