from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class TailoringProfile:
    profile_name: str
    profile_dir: str
    resume_markdown: str
    cover_letter_markdown: str
    preferences_markdown: str
    shared_preferences_markdown: str
    profile_preferences_markdown: str
    resume_source_hash: str
    cover_letter_source_hash: str
    preferences_source_hash: str


@dataclass(slots=True)
class TailoringJobContext:
    job_id: int
    source: str
    title: str
    company: str
    location: str
    posted_at: str
    url: str
    description: str
    company_context: str
    job_text_version: str
    job_text_snapshot: str
    profile_match_label: str
    profile_match_score: float
    job_context_hash: str


@dataclass(slots=True)
class TailoringResult:
    resume_markdown: str
    cover_letter_markdown: str
    highlight_requirements: list[str]
    evidence_map: list[dict[str, str]]
    provider_name: str
    model_name: str


@dataclass(slots=True)
class TailoringArtifactRecord:
    artifact_id: int
    job_id: int
    profile_name: str
    output_dir: str
    created: bool
    forced: bool
