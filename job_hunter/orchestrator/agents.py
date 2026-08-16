from __future__ import annotations

import json

from job_hunter.config import Settings
from job_hunter.orchestrator.types import ApplierAssessment, ApplyDecision, SourcingPlan, WriterOutput
from job_hunter.tailoring.provider import build_tailoring_user_prompt, tailoring_system_prompt
from job_hunter.tailoring.types import TailoringJobContext, TailoringProfile, TailoringResult


class StructuredOutputChatModel:
    """Pins a provider-compatible LangChain structured-output strategy."""

    def __init__(self, model, *, method: str) -> None:
        self.model = model
        self.method = method

    def with_structured_output(self, schema):
        return self.model.with_structured_output(schema, method=self.method)


class OrchestratorAgent:
    role = "orchestrator"

    def __init__(self, model) -> None:
        self._model = model.with_structured_output(ApplyDecision)

    def decide(self, *, job: dict[str, object], profile_names: list[str]) -> ApplyDecision:
        label = str(job.get("profile_match_label") or "")
        if label not in {"pass", "review"}:
            raise RuntimeError(f"Unsupported profile_match_label: {label or 'missing'}")
        decision = self._model.invoke(
            [
                (
                    "system",
                    "You orchestrate a job-application workflow. Decide whether to apply and which supplied profile "
                    "to use. Treat profile_match_label as one signal rather than the decision itself. Never override "
                    "hard eligibility gates. Prefer skipping when fit is materially ambiguous. Return only the "
                    "requested structured decision and a concise rationale.",
                ),
                (
                    "human",
                    json.dumps(
                        {"job": _job_for_model(job), "available_profiles": profile_names},
                        sort_keys=True,
                    ),
                ),
            ]
        )
        if decision.action == "apply" and decision.profile_name not in profile_names:
            raise RuntimeError(f"Agent selected unknown profile: {decision.profile_name}")
        if decision.label_considered != label:
            raise RuntimeError("Agent decision did not preserve profile_match_label")
        return decision


class SourcingAgent:
    role = "sourcing"

    def __init__(self, model) -> None:
        self._model = model.with_structured_output(SourcingPlan)

    def plan(self, *, operational_summary: dict[str, object]) -> SourcingPlan:
        return self._model.invoke(
            [
                (
                    "system",
                    "You manage sourcing for US software, data, ML, and AI internships. Produce at most five focused "
                    "web-search queries for discovering company ATS boards, internship repositories, or RSS feeds. "
                    "Use only these source types: greenhouse, lever, ashby, rss, github_repo. Avoid repeating unhealthy "
                    "or low-yield sources. Return structured output.",
                ),
                ("human", json.dumps(operational_summary, sort_keys=True)),
            ]
        )


class ApplicationWriterAgent:
    """Tailoring provider backed by a role-configured LangChain chat model."""

    role = "writer"

    def __init__(self, model, *, provider_name: str, model_name: str) -> None:
        self.provider_name = f"langchain-{provider_name}"
        self.model_name = model_name
        self._model = model.with_structured_output(WriterOutput)

    def generate(self, *, profile: TailoringProfile, job_context: TailoringJobContext) -> TailoringResult:
        # Deliberately share the exact system and user prompts with AnthropicTailoringProvider.
        result = self._model.invoke(
            [
                ("system", tailoring_system_prompt()),
                ("human", build_tailoring_user_prompt(profile=profile, job_context=job_context)),
            ]
        )
        evidence = []
        for item in result.evidence_map:
            requirement = str(item.get("job_requirement") or "").strip()
            profile_evidence = str(item.get("profile_evidence") or "").strip()
            if not requirement or not profile_evidence:
                raise RuntimeError("Writer returned an incomplete evidence_map entry")
            evidence.append({"job_requirement": requirement, "profile_evidence": profile_evidence})
        return TailoringResult(
            resume_markdown=result.resume_markdown.strip(),
            cover_letter_markdown=result.cover_letter_markdown.strip(),
            highlight_requirements=[item.strip() for item in result.highlight_requirements],
            evidence_map=evidence,
            provider_name=self.provider_name,
            model_name=self.model_name,
        )


class ApplierAgent:
    role = "applier"

    def __init__(self, model) -> None:
        self._model = model.with_structured_output(ApplierAssessment)

    def assess(self, *, application_status: str, blocker: dict[str, object]) -> ApplierAssessment:
        if application_status == "submitted":
            return ApplierAssessment(action="accept", rationale="Application service confirmed submission.")
        return self._model.invoke(
            [
                (
                    "system",
                    "Assess a persisted application attempt. Never infer submission without explicit confirmation. "
                    "Ambiguous confirmation, captcha, login, account bootstrap, missing answers, and manual checkpoints "
                    "require intervention. Unsupported portals and structurally unsupported widgets may be terminal.",
                ),
                ("human", json.dumps({"status": application_status, "blocker": blocker}, sort_keys=True)),
            ]
        )


def build_chat_model(*, provider: str, model_name: str | None, settings: Settings):
    normalized = provider.strip().lower().replace("-", "_")
    if normalized in {"nvidia", "nim"}:
        normalized = "nvidia_nim"
    model = (model_name or "").strip()
    if normalized not in {"openai", "anthropic", "nvidia_nim"}:
        raise RuntimeError(f"Unsupported agent provider: {provider}")
    if not model:
        raise RuntimeError(f"Model must be configured for {normalized} agent provider")
    kwargs = {
        "model": model,
        "temperature": 0,
        "timeout": settings.orchestrator_agent_timeout_seconds,
        "max_retries": settings.orchestrator_agent_max_retries,
    }
    if normalized in {"openai", "nvidia_nim"}:
        try:
            from langchain_openai import ChatOpenAI
        except ModuleNotFoundError as exc:
            raise RuntimeError("langchain-openai is not installed. Run `pip install -e .`.") from exc
        if normalized == "openai":
            return ChatOpenAI(**kwargs)
        api_key = (settings.nvidia_api_key or "").strip()
        if not api_key:
            raise RuntimeError("NVIDIA_API_KEY must be configured for the nvidia_nim agent provider")
        base_url = settings.nvidia_nim_base_url.strip().rstrip("/")
        if not base_url:
            raise RuntimeError("JOB_HUNTER_NVIDIA_NIM_BASE_URL must not be empty")
        method = settings.nvidia_nim_structured_output_method.strip().lower()
        if method not in {"json_schema", "function_calling"}:
            raise RuntimeError(
                "JOB_HUNTER_NVIDIA_NIM_STRUCTURED_OUTPUT_METHOD must be one of "
                "json_schema or function_calling"
            )
        nim_model = ChatOpenAI(
            **kwargs,
            base_url=base_url,
            api_key=api_key,
        )
        return StructuredOutputChatModel(nim_model, method=method)
    try:
        from langchain_anthropic import ChatAnthropic
    except ModuleNotFoundError as exc:
        raise RuntimeError("langchain-anthropic is not installed. Run `pip install -e .`.") from exc
    return ChatAnthropic(**kwargs)


def build_default_agents(settings: Settings):
    orchestrator = OrchestratorAgent(
        build_chat_model(
            provider=settings.orchestrator_provider,
            model_name=settings.orchestrator_model,
            settings=settings,
        )
    )
    sourcing = SourcingAgent(
        build_chat_model(
            provider=settings.sourcing_provider,
            model_name=settings.sourcing_model,
            settings=settings,
        )
    )
    writer = ApplicationWriterAgent(
        build_chat_model(
            provider=settings.writer_provider,
            model_name=settings.writer_model,
            settings=settings,
        ),
        provider_name=settings.writer_provider,
        model_name=settings.writer_model or "",
    )
    applier = ApplierAgent(
        build_chat_model(
            provider=settings.applier_provider,
            model_name=settings.applier_model,
            settings=settings,
        )
    )
    return orchestrator, sourcing, writer, applier


def _job_for_model(job: dict[str, object]) -> dict[str, object]:
    allowed = {
        "id", "source", "company", "title", "location", "posted_at", "url", "description",
        "profile_match_label", "profile_match_score", "profile_match_reason_codes",
        "eligibility_status", "eligibility_confidence", "policy_gate_status",
        "policy_gate_reason_codes", "source_quality_status", "relevance_score", "compensation_type",
    }
    return {key: value for key, value in job.items() if key in allowed}
