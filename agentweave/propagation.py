from opentelemetry import trace
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

_propagator = TraceContextTextMapPropagator()


def inject_trace_context(headers: dict = None) -> dict:
    """Inject current span context into headers for propagation to sub-agents.

    Call this before making an A2A or HTTP request to a sub-agent so the
    receiving agent can link its spans as children of the current trace.
    """
    headers = headers if headers is not None else {}
    _propagator.inject(headers)
    return headers


def extract_trace_context(headers: dict) -> trace.Context:
    """Extract trace context from incoming headers.

    Call this at the start of a sub-agent's work to link its spans to the
    parent agent's trace.
    """
    return _propagator.extract(headers)


def get_traceparent() -> "str | None":
    """Return the W3C traceparent header value for the current active span."""
    headers: dict = {}
    _propagator.inject(headers)
    return headers.get("traceparent")