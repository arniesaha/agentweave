"""AgentWeave tracing decorators — trace_tool, trace_agent, trace_llm."""

from __future__ import annotations

import asyncio
import functools
from typing import Any, Callable, Optional

from opentelemetry import trace

from agentweave import schema
from agentweave.exporter import get_tracer


def _get_config_attrs() -> dict:
    """Pull agent identity attributes from global config (if setup)."""
    try:
        from agentweave.config import AgentWeaveConfig
        cfg = AgentWeaveConfig.get_or_none()
        if cfg:
            return {
                schema.PROV_AGENT_ID: cfg.agent_id,
                schema.PROV_AGENT_MODEL: cfg.agent_model,
                schema.PROV_AGENT_VERSION: cfg.agent_version,
                schema.PROV_WAS_ASSOCIATED_WITH: cfg.agent_id,
            }
    except Exception:
        pass
    return {}


# ── trace_tool ────────────────────────────────────────────────────────────────

def _make_tool_wrapper(fn: Callable, name: str, captures_input: bool, captures_output: bool) -> Callable:
    span_name = f"{schema.SPAN_PREFIX_TOOL}.{name}"

    if asyncio.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            tracer = get_tracer()
            with tracer.start_as_current_span(span_name) as span:
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
        return async_wrapper
    else:
        @functools.wraps(fn)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            tracer = get_tracer()
            with tracer.start_as_current_span(span_name) as span:
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

def _make_agent_wrapper(fn: Callable, name: str) -> Callable:
    span_name = f"{schema.SPAN_PREFIX_AGENT}.{name}"

    if asyncio.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            tracer = get_tracer()
            with tracer.start_as_current_span(span_name) as span:
                span.set_attribute(schema.PROV_ACTIVITY_TYPE, schema.ACTIVITY_AGENT_TURN)
                for k, v in _get_config_attrs().items():
                    span.set_attribute(k, v)
                return await fn(*args, **kwargs)
        return async_wrapper
    else:
        @functools.wraps(fn)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            tracer = get_tracer()
            with tracer.start_as_current_span(span_name) as span:
                span.set_attribute(schema.PROV_ACTIVITY_TYPE, schema.ACTIVITY_AGENT_TURN)
                for k, v in _get_config_attrs().items():
                    span.set_attribute(k, v)
                return fn(*args, **kwargs)
        return sync_wrapper


def trace_agent(fn: Optional[Callable] = None, *, name: Optional[str] = None, context=None,
                captures_input: bool = False, captures_output: bool = False):
    """Trace an agent turn. Supports bare @trace_agent or @trace_agent(name=...).

    Usage::
        @trace_agent
        def handle(msg): ...

        @trace_agent(name="nix")
        def handle(msg): ...
    """
    if callable(fn):
        return _make_agent_wrapper(fn, fn.__name__)

    def decorator(inner_fn: Callable) -> Callable:
        return _make_agent_wrapper(inner_fn, name or inner_fn.__name__)

    return decorator


# ── trace_llm ─────────────────────────────────────────────────────────────────

def _extract_llm_attrs(response: Any, captures_output: bool) -> dict:
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


def trace_llm(provider: str, model: str, captures_input: bool = False, captures_output: bool = False):
    """Trace an LLM call with provider/model attributes and token extraction.

    Usage::
        @trace_llm(provider="anthropic", model="claude-sonnet-4-6", captures_output=True)
        def call_claude(messages): ...
    """
    span_name = f"{schema.SPAN_PREFIX_LLM}.{model}"

    def decorator(fn: Callable) -> Callable:
        if asyncio.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                tracer = get_tracer()
                with tracer.start_as_current_span(span_name) as span:
                    span.set_attribute(schema.PROV_ACTIVITY_TYPE, schema.ACTIVITY_LLM_CALL)
                    span.set_attribute(schema.PROV_LLM_PROVIDER, provider)
                    span.set_attribute(schema.PROV_LLM_MODEL, model)
                    for k, v in _get_config_attrs().items():
                        span.set_attribute(k, v)
                    result = await fn(*args, **kwargs)
                    for k, v in _extract_llm_attrs(result, captures_output).items():
                        span.set_attribute(k, v)
                    return result
            return async_wrapper
        else:
            @functools.wraps(fn)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                tracer = get_tracer()
                with tracer.start_as_current_span(span_name) as span:
                    span.set_attribute(schema.PROV_ACTIVITY_TYPE, schema.ACTIVITY_LLM_CALL)
                    span.set_attribute(schema.PROV_LLM_PROVIDER, provider)
                    span.set_attribute(schema.PROV_LLM_MODEL, model)
                    for k, v in _get_config_attrs().items():
                        span.set_attribute(k, v)
                    result = fn(*args, **kwargs)
                    for k, v in _extract_llm_attrs(result, captures_output).items():
                        span.set_attribute(k, v)
                    return result
            return sync_wrapper

    return decorator
