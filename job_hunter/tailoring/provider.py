from __future__ import annotations

import json
import os
from dataclasses import asdict
from typing import Protocol

from job_hunter.tailoring.types import TailoringJobContext, TailoringProfile, TailoringResult

_TOOL_NAME = "submit_tailoring"


class TailoringProvider(Protocol):
    provider_name: str
    model_name: str

    def generate(self, *, profile: TailoringProfile, job_context: TailoringJobContext) -> TailoringResult:
        ...


class AnthropicTailoringProvider:
    provider_name = "anthropic"

    def __init__(self, *, model_name: str, api_key: str | None = None) -> None:
        if not model_name.strip():
            raise RuntimeError("JOB_HUNTER_TAILORING_ANTHROPIC_MODEL must be configured.")
        api_key = (api_key or os.getenv("ANTHROPIC_API_KEY") or "").strip()
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY must be set to generate tailored artifacts.")
        try:
            from anthropic import Anthropic
        except ModuleNotFoundError as exc:
            raise RuntimeError("Anthropic SDK is not installed. Run `pip install -e .`.") from exc

        self.model_name = model_name
        self._client = Anthropic(api_key=api_key)

    def generate(self, *, profile: TailoringProfile, job_context: TailoringJobContext) -> TailoringResult:
        message = self._client.messages.create(
            model=self.model_name,
            max_tokens=3000,
            system=_system_prompt(),
            messages=[
                {
                    "role": "user",
                    "content": _build_user_prompt(profile=profile, job_context=job_context),
                }
            ],
            tools=[
                {
                    "name": _TOOL_NAME,
                    "description": "Return tailored resume and cover-letter artifacts without inventing facts.",
                    "input_schema": _tool_input_schema(),
                    "type": "custom",
                }
            ],
            tool_choice={
                "type": "tool",
                "name": _TOOL_NAME,
                "disable_parallel_tool_use": True,
            },
        )
        for block in message.content:
            if getattr(block, "type", "") != "tool_use":
                continue
            if getattr(block, "name", "") != _TOOL_NAME:
                continue
            payload = getattr(block, "input", {})
            return _parse_tool_payload(payload, model_name=self.model_name)
        raise RuntimeError("Anthropic response did not include the expected structured tailoring payload.")


def _system_prompt() -> str:
    return (
        "You tailor resumes and cover letters for job applications. "
        "Never invent employers, dates, education, projects, titles, metrics, or skills not present in the provided profile. "
        "You may only reorganize, summarize, emphasize, and lightly rewrite supplied facts. "
        "Preserve the resume's top-level section order. "
        "Make the cover letter specific to the company and role using only the supplied evidence. "
        "Use a natural, restrained tone. "
        "Do not restate the job description back to the reader. "
        "Do not write lines such as 'this experience directly mirrors the role', 'this taught me how to', "
        "'this experience demonstrates my ability to', or similar explanatory fit-signaling phrases. "
        "Show relevance through concrete examples rather than commentary about relevance. "
        "Return the final result via the required tool."
    )


def _build_user_prompt(*, profile: TailoringProfile, job_context: TailoringJobContext) -> str:
    payload = {
        "job_context": asdict(job_context),
        "profile": {
            "resume_markdown": profile.resume_markdown,
            "cover_letter_markdown": profile.cover_letter_markdown,
            "preferences_markdown": profile.preferences_markdown,
        },
        "requirements": {
            "resume_rules": [
                "Keep Markdown output.",
                "Preserve top-level section order from the source resume.",
                "Do not add facts that do not exist in the source profile.",
            ],
            "cover_letter_rules": [
                "Tailor to the target company and job.",
                "Use only supplied profile evidence.",
                "Keep it concise and specific.",
            ],
        },
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def _tool_input_schema() -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "resume_markdown": {
                "type": "string",
                "description": "The tailored resume in Markdown, preserving the original top-level section order.",
            },
            "cover_letter_markdown": {
                "type": "string",
                "description": "The tailored cover letter in Markdown.",
            },
            "highlight_requirements": {
                "type": "array",
                "description": "A concise list of the highest-priority job requirements the tailoring optimized for.",
                "items": {"type": "string"},
            },
            "evidence_map": {
                "type": "array",
                "description": "Evidence mapping between job requirements and resume facts.",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "job_requirement": {"type": "string"},
                        "profile_evidence": {"type": "string"},
                    },
                    "required": ["job_requirement", "profile_evidence"],
                },
            },
        },
        "required": [
            "resume_markdown",
            "cover_letter_markdown",
            "highlight_requirements",
            "evidence_map",
        ],
    }


def _parse_tool_payload(payload: object, *, model_name: str) -> TailoringResult:
    if not isinstance(payload, dict):
        raise RuntimeError("Anthropic tool payload was not a JSON object.")
    resume_markdown = str(payload.get("resume_markdown") or "").strip()
    cover_letter_markdown = str(payload.get("cover_letter_markdown") or "").strip()
    highlight_requirements = payload.get("highlight_requirements")
    evidence_map = payload.get("evidence_map")
    if not resume_markdown or not cover_letter_markdown:
        raise RuntimeError("Anthropic returned an empty resume or cover letter.")
    if not isinstance(highlight_requirements, list) or not all(str(item).strip() for item in highlight_requirements):
        raise RuntimeError("Anthropic returned invalid highlight_requirements.")
    if not isinstance(evidence_map, list):
        raise RuntimeError("Anthropic returned invalid evidence_map.")

    normalized_evidence: list[dict[str, str]] = []
    for item in evidence_map:
        if not isinstance(item, dict):
            raise RuntimeError("Anthropic returned a non-object evidence_map entry.")
        job_requirement = str(item.get("job_requirement") or "").strip()
        profile_evidence = str(item.get("profile_evidence") or "").strip()
        if not job_requirement or not profile_evidence:
            raise RuntimeError("Anthropic returned an incomplete evidence_map entry.")
        normalized_evidence.append(
            {
                "job_requirement": job_requirement,
                "profile_evidence": profile_evidence,
            }
        )

    return TailoringResult(
        resume_markdown=resume_markdown,
        cover_letter_markdown=cover_letter_markdown,
        highlight_requirements=[str(item).strip() for item in highlight_requirements],
        evidence_map=normalized_evidence,
        provider_name="anthropic",
        model_name=model_name,
    )
