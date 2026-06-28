from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class SourceRunStats:
    fetched_count: int = 0
    normalized_count: int = 0
    rejected_missing_core_fields_count: int = 0
    rejected_age_count: int = 0
    after_stage_1a_count: int = 0
    rejected_internship_count: int = 0
    rejected_us_scope_count: int = 0
    rejected_title_blacklist_count: int = 0
    rejected_data_role_count: int = 0
    after_stage_1b_count: int = 0
    rejected_policy_gate_count: int = 0
    after_stage_1c_count: int = 0
    rejected_eligibility_count: int = 0
    rejected_relevance_count: int = 0
    rejected_source_quality_count: int = 0
    recovered_source_quality_count: int = 0
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
    compensation_type: str = "unknown"
    work_auth_signals: list[str] = field(default_factory=list)
    sponsorship_signals: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    ingested_at: str = ""

    relevance_score: float = 0.0
    eligibility_confidence: float = 0.0
    eligibility_status: str = "ambiguous"
    relevance_hits: list[str] = field(default_factory=list)
    role_relevance_label: str = ""
    role_relevance_reason_codes: list[str] = field(default_factory=list)
    policy_gate_status: str = ""
    policy_gate_reason_codes: list[str] = field(default_factory=list)
    profile_match_score: float = 0.0
    profile_match_label: str = ""
    profile_match_reason_codes: list[str] = field(default_factory=list)
    profile_version: str = ""
    scorer_version: str = ""
    job_text_version: str = ""
    job_text_snapshot: str = ""
    semantic_match_score: float = 0.0
    semantic_match_label: str = ""
    semantic_match_reason_codes: list[str] = field(default_factory=list)
    semantic_base_score: float = 0.0
    semantic_research_heaviness_score: float = 0.0
    semantic_adjustment_reason_codes: list[str] = field(default_factory=list)
    semantic_profile_id: str = ""
    semantic_model_name: str = ""
    semantic_scorer_version: str = ""
    semantic_text_hash: str = ""
    age_days: float | None = None
    age_unknown: bool = True
    source_detail: str = ""
    source_metadata: dict[str, object] = field(default_factory=dict)
    source_quality_status: str = ""
    source_quality_reason_codes: list[str] = field(default_factory=list)
    source_quality_prev_status: str = ""
    source_quality_recovered_at: str = ""


@dataclass(slots=True)
class PipelineOutcome:
    source_count: int = 0
    normalized_count: int = 0
    rejected_missing_core_fields_count: int = 0
    after_stage_1a_count: int = 0
    after_stage_1b_count: int = 0
    after_stage_1c_count: int = 0
    passed_filter_count: int = 0
    persisted_count: int = 0
    notified_count: int = 0
    duplicate_count: int = 0
    error_count: int = 0
    source_stats: dict[str, SourceRunStats] = field(default_factory=dict)
