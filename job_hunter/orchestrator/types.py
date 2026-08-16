from __future__ import annotations

from typing import Literal, TypedDict

from pydantic import BaseModel, Field, model_validator


AgentProvider = Literal["openai", "anthropic", "nvidia_nim"]
WorkflowStatus = Literal[
    "queued",
    "deciding",
    "skipped",
    "writing",
    "applying",
    "blocked",
    "submitted",
    "failed",
]


class ApplyDecision(BaseModel):
    action: Literal["apply", "skip"]
    profile_name: str | None = None
    rationale: str = Field(min_length=1, max_length=1200)
    alternatives: list[str] = Field(default_factory=list, max_length=3)
    label_considered: Literal["pass", "review"]

    @model_validator(mode="after")
    def require_profile_for_apply(self) -> "ApplyDecision":
        if self.action == "apply" and not self.profile_name:
            raise ValueError("profile_name is required when action=apply")
        if self.action == "skip":
            self.profile_name = None
        return self


class SourcingPlan(BaseModel):
    search_queries: list[str] = Field(default_factory=list, max_length=5)
    source_types: list[str] = Field(default_factory=list, max_length=12)
    rationale: str = Field(min_length=1, max_length=1200)


class WriterOutput(BaseModel):
    resume_markdown: str = Field(min_length=1)
    cover_letter_markdown: str = Field(min_length=1)
    highlight_requirements: list[str] = Field(min_length=1)
    evidence_map: list[dict[str, str]] = Field(min_length=1)


class ApplierAssessment(BaseModel):
    action: Literal["accept", "intervene", "terminal"]
    rationale: str = Field(min_length=1, max_length=1200)
    intervention_kind: str | None = None


class WorkflowState(TypedDict, total=False):
    workflow_id: int
    run_id: int
    thread_id: str
    job_id: int
    job: dict[str, object]
    profile_names: list[str]
    attempt_limit: int
    decision: dict[str, object]
    selected_profile: str
    artifact_id: int
    application_run_id: int
    application_status: str
    blocker: dict[str, object]
    intervention_id: int
    intervention_resolution: dict[str, object]
    error: str
    status: str
    events: list[dict[str, object]]
