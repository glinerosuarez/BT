from __future__ import annotations

from dataclasses import dataclass, field


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


@dataclass(slots=True)
class PipelineOutcome:
    source_count: int = 0
    passed_filter_count: int = 0
    persisted_count: int = 0
    notified_count: int = 0
    duplicate_count: int = 0
    error_count: int = 0
