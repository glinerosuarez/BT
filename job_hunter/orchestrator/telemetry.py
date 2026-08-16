from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

from job_hunter.config import Settings

LOG = logging.getLogger(__name__)


@dataclass
class PhoenixTelemetry:
    enabled: bool
    tracer: object | None = None
    provider: object | None = None

    @contextmanager
    def span(self, name: str, attributes: dict[str, object] | None = None) -> Iterator[object | None]:
        if not self.enabled or self.tracer is None:
            yield None
            return
        sanitized = _sanitize_attributes(attributes or {})
        with self.tracer.start_as_current_span(name, attributes=sanitized) as current:
            yield current

    def shutdown(self) -> None:
        if self.provider is None:
            return
        try:
            self.provider.force_flush()
            self.provider.shutdown()
        except Exception:
            LOG.exception("phoenix_shutdown_failed")


def configure_phoenix(settings: Settings) -> PhoenixTelemetry:
    if not settings.phoenix_enabled:
        return PhoenixTelemetry(enabled=False)
    _enable_openinference_privacy()
    try:
        from openinference.instrumentation.langchain import LangChainInstrumentor
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ModuleNotFoundError:
        LOG.warning("phoenix_instrumentation_unavailable install_project_dependencies")
        return PhoenixTelemetry(enabled=False)
    try:
        provider = TracerProvider(
            resource=Resource.create(
                {
                    "service.name": "job-hunter-orchestrator",
                    "deployment.environment": "local",
                }
            )
        )
        exporter = OTLPSpanExporter(
            endpoint=settings.phoenix_collector_endpoint,
            headers={"x-project-name": settings.phoenix_project_name},
        )
        provider.add_span_processor(BatchSpanProcessor(exporter))
        LangChainInstrumentor().instrument(skip_dep_check=True, tracer_provider=provider)
        tracer = provider.get_tracer("job_hunter.orchestrator")
        return PhoenixTelemetry(enabled=True, tracer=tracer, provider=provider)
    except Exception:
        LOG.exception("phoenix_configuration_failed endpoint=%s", settings.phoenix_collector_endpoint)
        return PhoenixTelemetry(enabled=False)


def _enable_openinference_privacy() -> None:
    privacy_flags = (
        "OPENINFERENCE_HIDE_INPUTS",
        "OPENINFERENCE_HIDE_OUTPUTS",
        "OPENINFERENCE_HIDE_INPUT_MESSAGES",
        "OPENINFERENCE_HIDE_OUTPUT_MESSAGES",
        "OPENINFERENCE_HIDE_INPUT_TEXT",
        "OPENINFERENCE_HIDE_OUTPUT_TEXT",
        "OPENINFERENCE_HIDE_LLM_PROMPTS",
        "OPENINFERENCE_HIDE_LLM_TOOLS",
        "OPENINFERENCE_HIDE_EMBEDDING_VECTORS",
    )
    for name in privacy_flags:
        os.environ.setdefault(name, "true")


def _sanitize_attributes(attributes: dict[str, object]) -> dict[str, object]:
    allowed: dict[str, object] = {}
    for key, value in attributes.items():
        lowered = key.lower()
        if any(token in lowered for token in ("email", "phone", "address", "name", "token", "secret", "password", "prompt", "output", "input")):
            continue
        if isinstance(value, (str, bool, int, float)):
            allowed[key] = value
        elif value is None:
            continue
        else:
            allowed[key] = str(value)[:500]
    return allowed
