"""Auto-instrumentation for LLM SDKs (Anthropic, OpenAI).

Call ``auto_instrument()`` once to monkey-patch SDK client methods so every
non-streaming ``create()`` call gets an OTel span automatically.
"""

from __future__ import annotations

import functools
import inspect
from typing import Any, Callable, Optional, Sequence

from opentelemetry import trace

from agentweave import schema
from agentweave.decorators import _extract_llm_attrs, _get_config_attrs
from agentweave.exporter import get_tracer

_active_patches: dict[str, Callable] = {}  # provider -> unpatch fn

_KNOWN_PROVIDERS = ("anthropic", "openai")


# ---------------------------------------------------------------------------
# Core wrapper factory
# ---------------------------------------------------------------------------

def _is_already_in_llm_span() -> bool:
    """Return True if the current span is already an llm_call (explicit @trace_llm)."""
    span = trace.get_current_span()
    if not span or not span.is_recording():
        return False
    attrs = span.attributes or {}
    return attrs.get(schema.PROV_ACTIVITY_TYPE) == schema.ACTIVITY_LLM_CALL


def _make_llm_wrapper(
    original: Callable,
    provider: str,
    get_model: Callable[..., str],
    captures_output: bool,
) -> Callable:
    """Build a sync or async wrapper that creates an LLM span around *original*."""

    if inspect.iscoroutinefunction(original):
        @functools.wraps(original)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            if _is_already_in_llm_span():
                return await original(*args, **kwargs)

            model = get_model(args, kwargs)
            span_name = f"{schema.SPAN_PREFIX_LLM}.{model}"
            tracer = get_tracer()

            with tracer.start_as_current_span(span_name) as span:
                span.set_attribute(schema.PROV_ACTIVITY_TYPE, schema.ACTIVITY_LLM_CALL)
                span.set_attribute(schema.PROV_LLM_PROVIDER, provider)
                span.set_attribute(schema.PROV_LLM_MODEL, model)
                span.set_attribute(schema.AUTO_INSTRUMENTED, True)
                for k, v in _get_config_attrs().items():
                    span.set_attribute(k, v)

                result = await original(*args, **kwargs)

                for k, v in _extract_llm_attrs(result, captures_output).items():
                    span.set_attribute(k, v)
                return result

        return async_wrapper
    else:
        @functools.wraps(original)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            if _is_already_in_llm_span():
                return original(*args, **kwargs)

            model = get_model(args, kwargs)
            span_name = f"{schema.SPAN_PREFIX_LLM}.{model}"
            tracer = get_tracer()

            with tracer.start_as_current_span(span_name) as span:
                span.set_attribute(schema.PROV_ACTIVITY_TYPE, schema.ACTIVITY_LLM_CALL)
                span.set_attribute(schema.PROV_LLM_PROVIDER, provider)
                span.set_attribute(schema.PROV_LLM_MODEL, model)
                span.set_attribute(schema.AUTO_INSTRUMENTED, True)
                for k, v in _get_config_attrs().items():
                    span.set_attribute(k, v)

                result = original(*args, **kwargs)

                for k, v in _extract_llm_attrs(result, captures_output).items():
                    span.set_attribute(k, v)
                return result

        return sync_wrapper


# ---------------------------------------------------------------------------
# Provider patchers
# ---------------------------------------------------------------------------

def _get_model_from_kwargs(args: tuple, kwargs: dict) -> str:
    """Extract model from kwargs or positional args (shared by Anthropic & OpenAI)."""
    return kwargs.get("model", args[1] if len(args) > 1 else "unknown")


def _patch_anthropic(captures_output: bool) -> Callable:
    """Patch anthropic.resources.Messages.create (sync + async)."""
    try:
        import anthropic.resources  # noqa: F811
    except ImportError:
        raise

    saved: dict[str, Any] = {}

    # Sync
    cls = anthropic.resources.Messages
    saved["sync"] = cls.create
    cls.create = _make_llm_wrapper(cls.create, "anthropic", _get_model_from_kwargs, captures_output)

    # Async
    async_cls = anthropic.resources.AsyncMessages
    saved["async"] = async_cls.create
    async_cls.create = _make_llm_wrapper(async_cls.create, "anthropic", _get_model_from_kwargs, captures_output)

    def _unpatch() -> None:
        anthropic.resources.Messages.create = saved["sync"]
        anthropic.resources.AsyncMessages.create = saved["async"]

    return _unpatch


def _patch_openai(captures_output: bool) -> Callable:
    """Patch openai.resources.chat.completions.Completions.create (sync + async)."""
    try:
        import openai.resources.chat.completions  # noqa: F811
    except ImportError:
        raise

    mod = openai.resources.chat.completions
    saved: dict[str, Any] = {}

    # Sync
    saved["sync"] = mod.Completions.create
    original_sync = mod.Completions.create

    def _sync_skip_stream(*args: Any, **kwargs: Any) -> Any:
        if kwargs.get("stream"):
            return original_sync(*args, **kwargs)
        return _make_llm_wrapper(original_sync, "openai", _get_model_from_kwargs, captures_output)(*args, **kwargs)

    mod.Completions.create = _sync_skip_stream

    # Async
    saved["async"] = mod.AsyncCompletions.create
    original_async = mod.AsyncCompletions.create

    async def _async_skip_stream(*args: Any, **kwargs: Any) -> Any:
        if kwargs.get("stream"):
            return await original_async(*args, **kwargs)
        return await _make_llm_wrapper(original_async, "openai", _get_model_from_kwargs, captures_output)(*args, **kwargs)

    mod.AsyncCompletions.create = _async_skip_stream

    def _unpatch() -> None:
        mod.Completions.create = saved["sync"]
        mod.AsyncCompletions.create = saved["async"]

    return _unpatch


_PATCHERS: dict[str, Callable] = {
    "anthropic": _patch_anthropic,
    "openai": _patch_openai,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def auto_instrument(
    providers: Optional[Sequence[str]] = None,
    captures_output: bool = False,
) -> None:
    """Monkey-patch LLM SDK methods to emit OTel spans automatically.

    Args:
        providers: List of provider names to patch (default: all detected).
        captures_output: If True, capture response preview in span attributes.
    """
    targets = list(providers) if providers else list(_KNOWN_PROVIDERS)

    for name in targets:
        if name in _active_patches:
            continue  # idempotent
        patcher = _PATCHERS.get(name)
        if patcher is None:
            continue
        try:
            unpatch = patcher(captures_output)
            _active_patches[name] = unpatch
        except ImportError:
            pass  # SDK not installed — skip silently


def uninstrument(providers: Optional[Sequence[str]] = None) -> None:
    """Restore original SDK methods, undoing ``auto_instrument()``."""
    targets = list(providers) if providers else list(_active_patches)

    for name in targets:
        unpatch = _active_patches.pop(name, None)
        if unpatch is not None:
            unpatch()
