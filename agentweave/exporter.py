"""OpenTelemetry tracer and OTLP HTTP exporter setup."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor

if TYPE_CHECKING:
    from agentweave.config import AgentWeaveConfig

_provider: Optional[TracerProvider] = None


def init_tracer(config: "AgentWeaveConfig") -> TracerProvider:
    """Initialise (or re-initialise) the global OTel TracerProvider."""
    global _provider

    resource = Resource.create(
        {
            "service.name": config.service_name,
            "agent.id": config.agent_id,
            "agent.model": config.agent_model,
            "agent.version": config.agent_version,
        }
    )

    _provider = TracerProvider(resource=resource)

    if config.enabled:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        exporter = OTLPSpanExporter(endpoint=f"{config.otel_endpoint.rstrip('/')}/v1/traces")
        _provider.add_span_processor(BatchSpanProcessor(exporter))

    trace.set_tracer_provider(_provider)
    return _provider


def get_tracer(name: str = "agentweave") -> trace.Tracer:
    """Return an OTel Tracer, using the module-level provider if set."""
    if _provider is not None:
        return _provider.get_tracer(name)
    return trace.get_tracer(name)


def get_provider() -> Optional[TracerProvider]:
    """Return the current TracerProvider (or None if not initialised)."""
    return _provider


def add_console_exporter() -> None:
    """Add a console exporter for debugging — prints spans to stdout."""
    from opentelemetry.sdk.trace.export import ConsoleSpanExporter

    if _provider is not None:
        _provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))


def shutdown() -> None:
    """Flush and shut down the tracer provider."""
    if _provider is not None:
        _provider.shutdown()
