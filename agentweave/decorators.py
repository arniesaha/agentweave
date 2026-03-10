"""Non-invasive decorators for tracing agent tool calls and agent turns."""

from __future__ import annotations

import asyncio
import functools
import inspect
import json
import sys
from typing import Any, Callable, Optional, TypeVar, overload

from opentelemetry import trace
from opentelemetry.trace import StatusCode

from agentweave import schema
from agentweave.exporter import get_tracer

F = TypeVar("F", bound=Callable[..., Any])


def _serialize(value: Any, max_length: int = 4096) -> str:
    """Best-effort serialisation of a value for span attributes."""
    try:
        text = json.dumps(value, default=str)
    except (TypeError, ValueError):
        text = repr(value)
    if len(text) > max_length:
        text = text[:max_length] + "…"
    return text


def _set_prov_attributes(
    span: trace.Span,
    *,
    activity_name: str,
    activity_type: str,
    captures_input: bool,
    captures_output: bool,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    result: Any = None,
    sig: Optional[inspect.Signature] = None,
) -> None:
    """Attach PROV-O attributes to a span."""
    from agentweave.config import AgentWeaveConfig

    config = AgentWeaveConfig.get_or_none()

    # Activity
    span.set_attribute(schema.PROV_ACTIVITY, activity_name)
    span.set_attribute(schema.PROV_ACTIVITY_TYPE, activity_type)

    # Agent
    if config:
        span.set_attribute(schema.PROV_AGENT_ID, config.agent_id)
        span.set_attribute(schema.PROV_AGENT_MODEL, config.agent_model)
        span.set_attribute(schema.PROV_AGENT_VERSION, config.agent_version)
        span.set_attribute(schema.PROV_WAS_ASSOCIATED_WITH, config.agent_id)

    # Inputs
    if captures_input:
        bound: dict[str, Any] = {}
        if sig:
            try:
                ba = sig.bind(*args, **kwargs)
                ba.apply_defaults()
                bound = {k: v for k, v in ba.arguments.items() if k != "self"}
            except TypeError:
                bound = {"args": args, "kwargs": kwargs}
        else:
            bound = {"args": args, "kwargs": kwargs}

        serialized_input = _serialize(bound)
        span.set_attribute(schema.PROV_USED, serialized_input)
        span.set_attribute(schema.PROV_ENTITY_TYPE, schema.ENTITY_INPUT)
        span.set_attribute(schema.PROV_ENTITY_VALUE, serialized_input)
        span.set_attribute(schema.PROV_ENTITY_SIZE_BYTES, len(serialized_input))

    # Outputs
    if captures_output and result is not None:
        serialized_output = _serialize(result)
        span.set_attribute(schema.PROV_WAS_GENERATED_BY, activity_name)
        span.set_attribute(f"{schema.PROV_ENTITY}.output.type", schema.ENTITY_OUTPUT)
        span.set_attribute(f"{schema.PROV_ENTITY}.output.value", serialized_output)
        span.set_attribute(f"{schema.PROV_ENTITY}.output.size_bytes", len(serialized_output))


# ---------------------------------------------------------------------------
# @trace_tool
# ---------------------------------------------------------------------------


@overload
def trace_tool(fn: F) -> F: ...


@overload
def trace_tool(
    *,
    name: Optional[str] = None,
    captures_input: Optional[bool] = None,
    captures_output: Optional[bool] = None,
) -> Callable[[F], F]: ...


def trace_tool(
    fn: Optional[F] = None,
    *,
    name: Optional[str] = None,
    captures_input: Optional[bool] = None,
    captures_output: Optional[bool] = None,
) -> F | Callable[[F], F]:
    """Decorator that wraps a tool/function call with an OTel span.

    Can be used bare (``@trace_tool``) or with arguments
    (``@trace_tool(name="web_search", captures_input=True)``).
    """

    def decorator(func: F) -> F:
        span_name = name or func.__name__
        sig = inspect.signature(func)
        is_async = asyncio.iscoroutinefunction(func)

        if is_async:

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                from agentweave.config import AgentWeaveConfig

                cfg = AgentWeaveConfig.get_or_none()
                ci = captures_input if captures_input is not None else (cfg.captures_input if cfg else False)
                co = captures_output if captures_output is not None else (cfg.captures_output if cfg else False)

                tracer = get_tracer()
                with tracer.start_as_current_span(f"{schema.SPAN_PREFIX_TOOL}.{span_name}") as span:
                    try:
                        result = await func(*args, **kwargs)
                        _set_prov_attributes(
                            span,
                            activity_name=span_name,
                            activity_type=schema.ACTIVITY_TOOL_CALL,
                            captures_input=ci,
                            captures_output=co,
                            args=args,
                            kwargs=kwargs,
                            result=result,
                            sig=sig,
                        )
                        span.set_status(StatusCode.OK)
                        return result
                    except Exception as exc:
                        span.set_status(StatusCode.ERROR, str(exc))
                        span.record_exception(exc)
                        raise

            return async_wrapper  # type: ignore[return-value]

        else:

            @functools.wraps(func)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                from agentweave.config import AgentWeaveConfig

                cfg = AgentWeaveConfig.get_or_none()
                ci = captures_input if captures_input is not None else (cfg.captures_input if cfg else False)
                co = captures_output if captures_output is not None else (cfg.captures_output if cfg else False)

                tracer = get_tracer()
                with tracer.start_as_current_span(f"{schema.SPAN_PREFIX_TOOL}.{span_name}") as span:
                    try:
                        result = func(*args, **kwargs)
                        _set_prov_attributes(
                            span,
                            activity_name=span_name,
                            activity_type=schema.ACTIVITY_TOOL_CALL,
                            captures_input=ci,
                            captures_output=co,
                            args=args,
                            kwargs=kwargs,
                            result=result,
                            sig=sig,
                        )
                        span.set_status(StatusCode.OK)
                        return result
                    except Exception as exc:
                        span.set_status(StatusCode.ERROR, str(exc))
                        span.record_exception(exc)
                        raise

            return sync_wrapper  # type: ignore[return-value]

    if fn is not None:
        return decorator(fn)
    return decorator  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# @trace_agent
# ---------------------------------------------------------------------------


@overload
def trace_agent(fn: F) -> F: ...


@overload
def trace_agent(
    *,
    name: Optional[str] = None,
    captures_input: Optional[bool] = None,
    captures_output: Optional[bool] = None,
) -> Callable[[F], F]: ...


def trace_agent(
    fn: Optional[F] = None,
    *,
    name: Optional[str] = None,
    captures_input: Optional[bool] = None,
    captures_output: Optional[bool] = None,
) -> F | Callable[[F], F]:
    """Decorator that wraps an agent turn/prompt execution with an OTel span.

    Can be used bare (``@trace_agent``) or with arguments
    (``@trace_agent(name="handler", captures_input=True)``).
    """

    def decorator(func: F) -> F:
        span_name = name or func.__name__
        sig = inspect.signature(func)
        is_async = asyncio.iscoroutinefunction(func)

        if is_async:

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                from agentweave.config import AgentWeaveConfig

                cfg = AgentWeaveConfig.get_or_none()
                ci = captures_input if captures_input is not None else (cfg.captures_input if cfg else False)
                co = captures_output if captures_output is not None else (cfg.captures_output if cfg else False)

                tracer = get_tracer()
                with tracer.start_as_current_span(f"{schema.SPAN_PREFIX_AGENT}.{span_name}") as span:
                    try:
                        result = await func(*args, **kwargs)
                        _set_prov_attributes(
                            span,
                            activity_name=span_name,
                            activity_type=schema.ACTIVITY_AGENT_TURN,
                            captures_input=ci,
                            captures_output=co,
                            args=args,
                            kwargs=kwargs,
                            result=result,
                            sig=sig,
                        )
                        span.set_status(StatusCode.OK)
                        return result
                    except Exception as exc:
                        span.set_status(StatusCode.ERROR, str(exc))
                        span.record_exception(exc)
                        raise

            return async_wrapper  # type: ignore[return-value]

        else:

            @functools.wraps(func)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                from agentweave.config import AgentWeaveConfig

                cfg = AgentWeaveConfig.get_or_none()
                ci = captures_input if captures_input is not None else (cfg.captures_input if cfg else False)
                co = captures_output if captures_output is not None else (cfg.captures_output if cfg else False)

                tracer = get_tracer()
                with tracer.start_as_current_span(f"{schema.SPAN_PREFIX_AGENT}.{span_name}") as span:
                    try:
                        result = func(*args, **kwargs)
                        _set_prov_attributes(
                            span,
                            activity_name=span_name,
                            activity_type=schema.ACTIVITY_AGENT_TURN,
                            captures_input=ci,
                            captures_output=co,
                            args=args,
                            kwargs=kwargs,
                            result=result,
                            sig=sig,
                        )
                        span.set_status(StatusCode.OK)
                        return result
                    except Exception as exc:
                        span.set_status(StatusCode.ERROR, str(exc))
                        span.record_exception(exc)
                        raise

            return sync_wrapper  # type: ignore[return-value]

    if fn is not None:
        return decorator(fn)
    return decorator  # type: ignore[return-value]


def trace_llm(
    fn: Optional[F] = None,
    *,
    provider: str = "anthropic",
    model: Optional[str] = None,
    captures_input: bool = False,
    captures_output: bool = False,
) -> F | Callable[[F], F]:
    """Decorator that wraps an LLM API call with a provenance-aware OTel span.

    Records model, provider, token counts, stop reason, and optional
    prompt/response previews. Token counts are extracted automatically from
    the response if it follows the Anthropic or OpenAI usage conventions.

    Usage::

        @trace_llm(provider="anthropic", model="claude-sonnet-4-6",
                   captures_input=True, captures_output=True)
        def call_claude(messages: list[dict]) -> anthropic.Message:
            return client.messages.create(model=..., messages=messages, ...)

    Works with any sync or async callable that returns a response object with
    a ``usage`` attribute (Anthropic/OpenAI convention).
    """

    def decorator(func: F) -> F:
        sig = inspect.signature(func)
        span_name = model or func.__name__

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            tracer = get_tracer()
            with tracer.start_as_current_span(
                f"{schema.SPAN_PREFIX_LLM}.{span_name}"
            ) as span:
                _set_llm_request_attributes(
                    span, provider=provider, model=model or span_name,
                    captures_input=captures_input, args=args, kwargs=kwargs, sig=sig,
                )
                try:
                    result = await func(*args, **kwargs)
                    _set_llm_response_attributes(span, result, captures_output)
                    span.set_status(StatusCode.OK)
                    return result
                except Exception as exc:
                    span.set_status(StatusCode.ERROR, str(exc))
                    span.record_exception(exc)
                    raise

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            tracer = get_tracer()
            with tracer.start_as_current_span(
                f"{schema.SPAN_PREFIX_LLM}.{span_name}"
            ) as span:
                _set_llm_request_attributes(
                    span, provider=provider, model=model or span_name,
                    captures_input=captures_input, args=args, kwargs=kwargs, sig=sig,
                )
                try:
                    result = func(*args, **kwargs)
                    _set_llm_response_attributes(span, result, captures_output)
                    span.set_status(StatusCode.OK)
                    return result
                except Exception as exc:
                    span.set_status(StatusCode.ERROR, str(exc))
                    span.record_exception(exc)
                    raise

        if inspect.iscoroutinefunction(func):
            return async_wrapper  # type: ignore[return-value]
        return sync_wrapper  # type: ignore[return-value]

    if fn is not None:
        return decorator(fn)
    return decorator  # type: ignore[return-value]


def _set_llm_request_attributes(
    span: trace.Span,
    provider: str,
    model: str,
    captures_input: bool,
    args: tuple,
    kwargs: dict,
    sig: inspect.Signature,
) -> None:
    span.set_attribute(schema.PROV_ACTIVITY_TYPE, schema.ACTIVITY_LLM_CALL)
    span.set_attribute(schema.PROV_LLM_PROVIDER, provider)
    span.set_attribute(schema.PROV_LLM_MODEL, model)

    from agentweave.config import AgentWeaveConfig
    cfg = AgentWeaveConfig.get_or_none()
    if cfg:
        span.set_attribute(schema.PROV_AGENT_ID, cfg.agent_id)
        span.set_attribute(schema.PROV_WAS_ASSOCIATED_WITH, cfg.agent_id)

    if not captures_input:
        return
    try:
        bound = sig.bind(*args, **kwargs)
        bound.apply_defaults()
        prompt_text = _serialize(dict(bound.arguments))
        span.set_attribute(schema.PROV_LLM_PROMPT_PREVIEW, prompt_text[:512])
        span.set_attribute(schema.PROV_USED, prompt_text[:4096])
    except Exception:
        pass


def _set_llm_response_attributes(
    span: trace.Span,
    result: Any,
    captures_output: bool,
) -> None:
    # Token extraction — Anthropic, OpenAI, and Google Gemini conventions
    usage = getattr(result, "usage", None) or getattr(result, "usage_metadata", None)
    if usage is not None:
        # Anthropic: input_tokens / output_tokens
        # OpenAI: prompt_tokens / completion_tokens
        # Google: prompt_token_count / candidates_token_count
        pt = (
            getattr(usage, "input_tokens", None)
            or getattr(usage, "prompt_tokens", None)
            or getattr(usage, "prompt_token_count", None)
        )
        ct = (
            getattr(usage, "output_tokens", None)
            or getattr(usage, "completion_tokens", None)
            or getattr(usage, "candidates_token_count", None)
        )
        if pt is not None:
            span.set_attribute(schema.PROV_LLM_PROMPT_TOKENS, int(pt))
        if ct is not None:
            span.set_attribute(schema.PROV_LLM_COMPLETION_TOKENS, int(ct))
        if pt is not None and ct is not None:
            span.set_attribute(schema.PROV_LLM_TOTAL_TOKENS, int(pt) + int(ct))

    # Stop reason — Anthropic, OpenAI, Google
    stop = (
        getattr(result, "stop_reason", None)
        or getattr(result, "finish_reason", None)
    )
    # Google: candidates[0].finish_reason
    if stop is None:
        try:
            stop = result.candidates[0].finish_reason
        except (AttributeError, IndexError, TypeError):
            pass
    if stop:
        span.set_attribute(schema.PROV_LLM_STOP_REASON, str(stop))

    if not captures_output:
        return
    try:
        content = getattr(result, "content", None)
        if content and isinstance(content, list):
            text = getattr(content[0], "text", None) or _serialize(content[0])
        elif hasattr(result, "choices"):
            text = result.choices[0].message.content or ""
        else:
            text = _serialize(result)
        span.set_attribute(schema.PROV_LLM_RESPONSE_PREVIEW, str(text)[:512])
        span.set_attribute(schema.PROV_WAS_GENERATED_BY, str(text)[:4096])
    except Exception:
        pass
