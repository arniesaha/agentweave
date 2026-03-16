"""AgentWeave tracing decorators — trace_tool, trace_agent, trace_llm."""

from __future__ import annotations

import atexit
import asyncio
import contextvars
import functools
import hashlib
import inspect
import re
import secrets as _secrets
import signal
import sys
from typing import Any, Callable, Optional

# ---------------------------------------------------------------------------
# Per-session LLM turn counter
# ---------------------------------------------------------------------------
# Scoped naturally per async task / thread so parallel agent sessions never
# share state.  @trace_agent resets this to 0 at the start of each session;
# @trace_llm increments it before setting the span attribute.
_turn_counter: contextvars.ContextVar[int] = contextvars.ContextVar("_turn_counter", default=0)

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags

from agentweave import schema
from agentweave.exporter import get_tracer
from agentweave.pricing import compute_cost

# ── Deterministic trace ID helpers ───────────────────────────────────────────

_TRACE_ID_RE = re.compile(r'^[0-9a-fA-F]{32}$')


def _normalize_trace_id(trace_id: str) -> int | None:
    """Normalize a caller-supplied trace ID string to a 128-bit integer.

    - If *trace_id* is already a valid 32-char hex string it is used directly.
    - Otherwise it is SHA-256 hashed and the first 32 hex chars are used.
    - Returns ``None`` for empty / ``None`` input.
    """
    if not trace_id:
        return None
    trace_id = trace_id.strip()
    if not trace_id:
        return None
    if _TRACE_ID_RE.match(trace_id):
        return int(trace_id, 16)
    # Hash arbitrary string → valid 32 hex chars
    return int(hashlib.sha256(trace_id.encode()).hexdigest()[:32], 16)


def _context_for_trace_id(trace_id_int: int) -> Any:
    """Return an OTel context that seeds child spans with *trace_id_int*.

    Creates a synthetic remote parent :class:`NonRecordingSpan` so that any
    span started inside this context inherits the desired trace ID without
    itself being exported.
    """
    parent_span_id = int.from_bytes(_secrets.token_bytes(8), "big")
    span_ctx = SpanContext(
        trace_id=trace_id_int,
        span_id=parent_span_id,
        is_remote=True,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
    )
    return trace.set_span_in_context(NonRecordingSpan(span_ctx))


def _get_config_attrs() -> dict:
    """Pull agent identity attributes from global config (if setup).

    Returns both prov.* and gen_ai.* attributes for dual-emit.
    """
    try:
        from agentweave.config import AgentWeaveConfig
        cfg = AgentWeaveConfig.get_or_none()
        if cfg:
            attrs = {
                schema.PROV_AGENT_ID: cfg.agent_id,
                schema.PROV_AGENT_MODEL: cfg.agent_model,
                schema.PROV_AGENT_VERSION: cfg.agent_version,
                schema.PROV_WAS_ASSOCIATED_WITH: cfg.agent_id,
            }
            # OTel gen_ai.* dual-emit
            if cfg.agent_id:
                attrs[schema.GEN_AI_AGENT_NAME] = cfg.agent_id
            if cfg.agent_model:
                attrs[schema.GEN_AI_REQUEST_MODEL] = cfg.agent_model
            return attrs
    except Exception:
        pass
    return {}


# ── Span lifecycle tracking & graceful shutdown ───────────────────────────────

# Maps a unique key → open (not-yet-ended) span instance.
# The Python GIL protects this dict from concurrent modification.
_open_spans: dict[str, trace.Span] = {}

_handlers_registered: bool = False
_shutdown_called: bool = False


def _shutdown_handler(reason: str = "atexit") -> None:
    """Close all in-flight spans, set ABORTED status, then flush the exporter.

    This is called by signal handlers (SIGTERM/SIGINT) and atexit to ensure
    dangling traces are properly closed when the process exits unexpectedly.
    """
    global _shutdown_called
    if _shutdown_called:
        return
    _shutdown_called = True

    for span_key, span in list(_open_spans.items()):
        try:
            span.set_attribute("shutdown.reason", reason)
            span.set_status(trace.Status(trace.StatusCode.ERROR, f"Process aborted: {reason}"))
            span.end()
        except Exception:
            pass
    _open_spans.clear()

    # Flush the exporter so spans reach the backend before process exits.
    try:
        from agentweave.exporter import get_provider
        provider = get_provider()
        if provider is not None:
            provider.force_flush(timeout_millis=5000)
    except Exception:
        pass


def _handle_sigterm(signum: int, frame: object) -> None:
    _shutdown_handler("sigterm")
    sys.exit(0)


def _handle_sigint(signum: int, frame: object) -> None:
    _shutdown_handler("sigint")
    raise KeyboardInterrupt


def _register_shutdown_handlers() -> None:
    """Register SIGTERM, SIGINT, and atexit handlers (idempotent)."""
    global _handlers_registered
    if _handlers_registered:
        return
    _handlers_registered = True
    try:
        signal.signal(signal.SIGTERM, _handle_sigterm)
    except (OSError, ValueError):
        pass  # Not in main thread or unsupported platform
    try:
        signal.signal(signal.SIGINT, _handle_sigint)
    except (OSError, ValueError):
        pass
    atexit.register(_shutdown_handler, "atexit")


# ── trace_tool ────────────────────────────────────────────────────────────────

def _make_tool_wrapper(fn: Callable, name: str, captures_input: bool, captures_output: bool) -> Callable:
    span_name = f"{schema.SPAN_PREFIX_TOOL}.{name}"

    if inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            _register_shutdown_handlers()
            tracer = get_tracer()
            with tracer.start_as_current_span(span_name) as span:
                _span_key = str(id(span))
                _open_spans[_span_key] = span
                try:
                    span.set_attribute(schema.PROV_ACTIVITY_TYPE, schema.ACTIVITY_TOOL_CALL)
                    for k, v in _get_config_attrs().items():
                        span.set_attribute(k, v)
                    if captures_input:
                        span.set_attribute(schema.PROV_USED, str(args[0]) if args else str(kwargs))
                    try:
                        result = await fn(*args, **kwargs)
                        if captures_output:
                            span.set_attribute(schema.PROV_WAS_GENERATED_BY, span_name)
                            span.set_attribute(f"{schema.PROV_ENTITY}.output.value", str(result))
                        return result
                    except Exception as exc:
                        span.record_exception(exc)
                        span.set_status(trace.StatusCode.ERROR, str(exc))
                        raise
                finally:
                    _open_spans.pop(_span_key, None)
        return async_wrapper
    else:
        @functools.wraps(fn)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            _register_shutdown_handlers()
            tracer = get_tracer()
            with tracer.start_as_current_span(span_name) as span:
                _span_key = str(id(span))
                _open_spans[_span_key] = span
                try:
                    span.set_attribute(schema.PROV_ACTIVITY_TYPE, schema.ACTIVITY_TOOL_CALL)
                    for k, v in _get_config_attrs().items():
                        span.set_attribute(k, v)
                    if captures_input:
                        span.set_attribute(schema.PROV_USED, str(args[0]) if args else str(kwargs))
                    try:
                        result = fn(*args, **kwargs)
                        if captures_output:
                            span.set_attribute(schema.PROV_WAS_GENERATED_BY, span_name)
                            span.set_attribute(f"{schema.PROV_ENTITY}.output.value", str(result))
                        return result
                    except Exception as exc:
                        span.record_exception(exc)
                        span.set_status(trace.StatusCode.ERROR, str(exc))
                        raise
                finally:
                    _open_spans.pop(_span_key, None)
        return sync_wrapper


def trace_tool(fn: Optional[Callable] = None, *, name: Optional[str] = None,
               captures_input: bool = False, captures_output: bool = False):
    """Trace a tool call. Supports bare @trace_tool or @trace_tool(name=...).

    Usage::
        @trace_tool
        def search(query): ...

        @trace_tool(name="web_search", captures_output=True)
        def search(query): ...
    """
    if callable(fn):
        # Bare @trace_tool — fn is the decorated function
        return _make_tool_wrapper(fn, fn.__name__, False, False)

    def decorator(inner_fn: Callable) -> Callable:
        return _make_tool_wrapper(inner_fn, name or inner_fn.__name__, captures_input, captures_output)

    return decorator


# ── trace_agent ───────────────────────────────────────────────────────────────

def _make_agent_wrapper(
    fn: Callable, name: str, captures_input: bool, captures_output: bool,
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
    parent_session_id: Optional[str] = None,
    agent_type: Optional[str] = None,
    turn_depth: Optional[int] = None,
) -> Callable:
    span_name = f"{schema.SPAN_PREFIX_AGENT}.{name}"

    # Pre-compute the deterministic context once at decoration time (if provided)
    _det_trace_id_int = _normalize_trace_id(trace_id) if trace_id else None

    # Resolve sub-agent attribution: explicit params take precedence, then env var
    import os as _os
    _parent_session_id = parent_session_id or _os.environ.get("AGENTWEAVE_PARENT_SESSION_ID") or None
    _agent_type = agent_type
    _turn_depth = turn_depth
    # Auto-infer agent_type and turn_depth when parent session is set via env var
    if _parent_session_id and _agent_type is None:
        _agent_type = schema.AGENT_TYPE_SUBAGENT
    if _parent_session_id and _turn_depth is None:
        _turn_depth = 2  # default: first-level sub-agent

    def _start_ctx() -> Any:
        """Return an OTel context to use when starting the root agent span."""
        if _det_trace_id_int is not None:
            return _context_for_trace_id(_det_trace_id_int)
        return None  # let OTel use the ambient context

    def _set_subagent_attrs(span: Any) -> None:
        """Set sub-agent attribution attributes on the span."""
        if _parent_session_id is not None:
            span.set_attribute(schema.PROV_PARENT_SESSION_ID, _parent_session_id)
        if _agent_type is not None:
            span.set_attribute(schema.PROV_AGENT_TYPE, _agent_type)
        if _turn_depth is not None:
            span.set_attribute(schema.PROV_SESSION_TURN, _turn_depth)

    if inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            _register_shutdown_handlers()
            # Reset LLM turn counter to 0 for this agent session
            _turn_counter.set(0)
            tracer = get_tracer()
            with tracer.start_as_current_span(span_name, context=_start_ctx()) as span:
                _span_key = str(id(span))
                _open_spans[_span_key] = span
                try:
                    span.set_attribute(schema.PROV_ACTIVITY_TYPE, schema.ACTIVITY_AGENT_TURN)
                    # OTel gen_ai.* dual-emit
                    span.set_attribute(schema.GEN_AI_OPERATION_NAME, schema.GEN_AI_OP_INVOKE_AGENT)
                    for k, v in _get_config_attrs().items():
                        span.set_attribute(k, v)
                    if _det_trace_id_int is not None:
                        span.set_attribute(schema.AGENTWEAVE_TRACE_ID, trace_id)
                    if session_id is not None:
                        span.set_attribute(schema.SESSION_ID, session_id)
                        span.set_attribute(schema.PROV_SESSION_ID, session_id)
                    _set_subagent_attrs(span)
                    if captures_input:
                        span.set_attribute(schema.PROV_USED, str(args[0]) if args else str(kwargs))
                    result = await fn(*args, **kwargs)
                    if captures_output:
                        span.set_attribute(schema.PROV_WAS_GENERATED_BY, span_name)
                        span.set_attribute(f"{schema.PROV_ENTITY}.output.value", str(result))
                    return result
                finally:
                    _open_spans.pop(_span_key, None)
        return async_wrapper
    else:
        @functools.wraps(fn)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            _register_shutdown_handlers()
            # Reset LLM turn counter to 0 for this agent session
            _turn_counter.set(0)
            tracer = get_tracer()
            with tracer.start_as_current_span(span_name, context=_start_ctx()) as span:
                _span_key = str(id(span))
                _open_spans[_span_key] = span
                try:
                    span.set_attribute(schema.PROV_ACTIVITY_TYPE, schema.ACTIVITY_AGENT_TURN)
                    # OTel gen_ai.* dual-emit
                    span.set_attribute(schema.GEN_AI_OPERATION_NAME, schema.GEN_AI_OP_INVOKE_AGENT)
                    for k, v in _get_config_attrs().items():
                        span.set_attribute(k, v)
                    if _det_trace_id_int is not None:
                        span.set_attribute(schema.AGENTWEAVE_TRACE_ID, trace_id)
                    if session_id is not None:
                        span.set_attribute(schema.SESSION_ID, session_id)
                        span.set_attribute(schema.PROV_SESSION_ID, session_id)
                    _set_subagent_attrs(span)
                    if captures_input:
                        span.set_attribute(schema.PROV_USED, str(args[0]) if args else str(kwargs))
                    result = fn(*args, **kwargs)
                    if captures_output:
                        span.set_attribute(schema.PROV_WAS_GENERATED_BY, span_name)
                        span.set_attribute(f"{schema.PROV_ENTITY}.output.value", str(result))
                    return result
                finally:
                    _open_spans.pop(_span_key, None)
        return sync_wrapper


def trace_agent(fn: Optional[Callable] = None, *, name: Optional[str] = None, context=None,
                captures_input: bool = False, captures_output: bool = False,
                traceId: Optional[str] = None, session_id: Optional[str] = None,
                parent_session_id: Optional[str] = None,
                agent_type: Optional[str] = None,
                turn_depth: Optional[int] = None):
    """Trace an agent turn. Supports bare @trace_agent or @trace_agent(name=...).

    Pass ``traceId`` to pin the root span to a deterministic trace ID so that
    retries of the same logical request can be deduplicated by your backend.

    Pass ``session_id`` to group all spans from a single user conversation /
    agent session.  The value is attached as the ``session.id`` span attribute
    (and ``prov.session.id`` for backward compatibility) so it can be used as
    a filterable dimension in Grafana / Tempo.

    **Sub-agent attribution** (issue #15):

    Pass ``parent_session_id`` to link this sub-agent to its parent session.
    Pass ``agent_type`` to tag the agent role (``"main"``, ``"subagent"``, or
    ``"delegated"``).  Pass ``turn_depth`` to indicate the nesting level
    (1 = main session, 2 = first-level sub-agent, etc.).

    If the ``AGENTWEAVE_PARENT_SESSION_ID`` environment variable is set, it is
    used as the default ``parent_session_id``.  When a parent session is
    detected (explicit or env var), ``agent_type`` defaults to ``"subagent"``
    and ``turn_depth`` defaults to ``2``.

    Usage::
        @trace_agent
        def handle(msg): ...

        @trace_agent(name="nix")
        def handle(msg): ...

        # Sub-agent linked to parent session
        @trace_agent(name="max", parent_session_id="sess-parent-123",
                     agent_type="subagent", turn_depth=2)
        def handle(msg): ...

        # Auto-detect via env var: AGENTWEAVE_PARENT_SESSION_ID=sess-parent-123
        @trace_agent(name="max")
        def handle(msg): ...
    """
    if callable(fn):
        return _make_agent_wrapper(fn, fn.__name__, False, False)

    def decorator(inner_fn: Callable) -> Callable:
        return _make_agent_wrapper(
            inner_fn, name or inner_fn.__name__,
            captures_input, captures_output,
            trace_id=traceId,
            session_id=session_id,
            parent_session_id=parent_session_id,
            agent_type=agent_type,
            turn_depth=turn_depth,
        )

    return decorator


# ── trace_llm ─────────────────────────────────────────────────────────────────

def _extract_llm_attrs(response: Any, captures_output: bool, model: str = "", cost_usd_override: Optional[float] = None) -> dict:
    """Extract token counts, stop reason, and response preview from any LLM response."""
    attrs: dict = {}

    usage = getattr(response, "usage", None)
    if usage is not None:
        # Anthropic: input_tokens / output_tokens
        prompt = getattr(usage, "input_tokens", None)
        completion = getattr(usage, "output_tokens", None)
        # OpenAI: prompt_tokens / completion_tokens
        if prompt is None:
            prompt = getattr(usage, "prompt_tokens", None)
        if completion is None:
            completion = getattr(usage, "completion_tokens", None)

        if prompt is not None:
            attrs[schema.PROV_LLM_PROMPT_TOKENS] = prompt
        if completion is not None:
            attrs[schema.PROV_LLM_COMPLETION_TOKENS] = completion
        if prompt is not None and completion is not None:
            attrs[schema.PROV_LLM_TOTAL_TOKENS] = prompt + completion

    # Stop reason — Anthropic: response.stop_reason, OpenAI: choices[0].finish_reason
    stop_reason = getattr(response, "stop_reason", None)
    if stop_reason is None:
        choices = getattr(response, "choices", None)
        if choices:
            stop_reason = getattr(choices[0], "finish_reason", None)
    if stop_reason is not None:
        attrs[schema.PROV_LLM_STOP_REASON] = stop_reason
        # OTel gen_ai.* dual-emit
        attrs[schema.GEN_AI_RESPONSE_FINISH_REASONS] = [stop_reason]

    # OTel gen_ai.* token dual-emit
    if schema.PROV_LLM_PROMPT_TOKENS in attrs:
        attrs[schema.GEN_AI_USAGE_INPUT_TOKENS] = attrs[schema.PROV_LLM_PROMPT_TOKENS]
    if schema.PROV_LLM_COMPLETION_TOKENS in attrs:
        attrs[schema.GEN_AI_USAGE_OUTPUT_TOKENS] = attrs[schema.PROV_LLM_COMPLETION_TOKENS]

    # Cost tracking — use override if provided, else compute from token counts
    if cost_usd_override is not None:
        attrs[schema.COST_USD] = cost_usd_override
    elif model and (
        schema.PROV_LLM_PROMPT_TOKENS in attrs or schema.PROV_LLM_COMPLETION_TOKENS in attrs
    ):
        pt = attrs.get(schema.PROV_LLM_PROMPT_TOKENS, 0)
        ct = attrs.get(schema.PROV_LLM_COMPLETION_TOKENS, 0)
        attrs[schema.COST_USD] = compute_cost(model, pt, ct)

    # Response preview
    if captures_output:
        preview: Optional[str] = None
        # Anthropic: content[0].text
        content = getattr(response, "content", None)
        if content and hasattr(content[0], "text"):
            preview = content[0].text
        # OpenAI: choices[0].message.content
        if preview is None:
            choices = getattr(response, "choices", None)
            if choices and hasattr(choices[0], "message"):
                preview = getattr(choices[0].message, "content", None)
        if preview:
            attrs[schema.PROV_LLM_RESPONSE_PREVIEW] = preview[:512]

    return attrs


def trace_llm(
    provider: str,
    model: str,
    captures_input: bool = False,
    captures_output: bool = False,
    cost_usd: Optional[float] = None,
):
    """Trace an LLM call with provider/model attributes, token extraction, and cost tracking.

    Pass ``cost_usd`` to override the auto-computed cost (e.g. when you have
    exact billing data from the provider's response).  When omitted, cost is
    computed automatically from token counts using the built-in pricing table.

    Usage::
        @trace_llm(provider="anthropic", model="claude-sonnet-4-6", captures_output=True)
        def call_claude(messages): ...

        # Manual cost override
        @trace_llm(provider="anthropic", model="claude-sonnet-4-6", cost_usd=0.005)
        def call_claude(messages): ...
    """
    span_name = f"{schema.SPAN_PREFIX_LLM}.{model}"

    def decorator(fn: Callable) -> Callable:
        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                _register_shutdown_handlers()
                tracer = get_tracer()
                with tracer.start_as_current_span(span_name) as span:
                    _span_key = str(id(span))
                    _open_spans[_span_key] = span
                    try:
                        span.set_attribute(schema.PROV_ACTIVITY_TYPE, schema.ACTIVITY_LLM_CALL)
                        span.set_attribute(schema.PROV_LLM_PROVIDER, provider)
                        span.set_attribute(schema.PROV_LLM_MODEL, model)
                        # OTel gen_ai.* dual-emit
                        span.set_attribute(schema.GEN_AI_OPERATION_NAME, schema.GEN_AI_OP_CHAT)
                        span.set_attribute(schema.GEN_AI_SYSTEM, provider)
                        for k, v in _get_config_attrs().items():
                            span.set_attribute(k, v)
                        # Set after config attrs so explicit model param wins over cfg.agent_model
                        span.set_attribute(schema.GEN_AI_REQUEST_MODEL, model)
                        # Increment per-session turn counter and record on span
                        _turn = _turn_counter.get() + 1
                        _turn_counter.set(_turn)
                        span.set_attribute(schema.AGENT_TURN_COUNT, _turn)
                        result = await fn(*args, **kwargs)
                        for k, v in _extract_llm_attrs(result, captures_output, model=model, cost_usd_override=cost_usd).items():
                            span.set_attribute(k, v)
                        return result
                    finally:
                        _open_spans.pop(_span_key, None)
            return async_wrapper
        else:
            @functools.wraps(fn)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                _register_shutdown_handlers()
                tracer = get_tracer()
                with tracer.start_as_current_span(span_name) as span:
                    _span_key = str(id(span))
                    _open_spans[_span_key] = span
                    try:
                        span.set_attribute(schema.PROV_ACTIVITY_TYPE, schema.ACTIVITY_LLM_CALL)
                        span.set_attribute(schema.PROV_LLM_PROVIDER, provider)
                        span.set_attribute(schema.PROV_LLM_MODEL, model)
                        # OTel gen_ai.* dual-emit
                        span.set_attribute(schema.GEN_AI_OPERATION_NAME, schema.GEN_AI_OP_CHAT)
                        span.set_attribute(schema.GEN_AI_SYSTEM, provider)
                        for k, v in _get_config_attrs().items():
                            span.set_attribute(k, v)
                        # Set after config attrs so explicit model param wins over cfg.agent_model
                        span.set_attribute(schema.GEN_AI_REQUEST_MODEL, model)
                        # Increment per-session turn counter and record on span
                        _turn = _turn_counter.get() + 1
                        _turn_counter.set(_turn)
                        span.set_attribute(schema.AGENT_TURN_COUNT, _turn)
                        result = fn(*args, **kwargs)
                        for k, v in _extract_llm_attrs(result, captures_output, model=model, cost_usd_override=cost_usd).items():
                            span.set_attribute(k, v)
                        return result
                    finally:
                        _open_spans.pop(_span_key, None)
            return sync_wrapper

    return decorator
