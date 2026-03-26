"""Auto-instrumentation for LLM SDKs (Anthropic, OpenAI, Google GenAI).

Call ``auto_instrument()`` once to monkey-patch SDK client methods so every
non-streaming ``create()`` / ``generate_content()`` call gets an OTel span
automatically.

Two modes
---------
* **direct** (default): SDK patches emit OTel spans directly to the OTLP
  endpoint.  No proxy needed.
* **proxy**: Rewrite SDK base URLs to point at the AgentWeave proxy.  The
  proxy handles tracing — no local OTel emission.
"""

from __future__ import annotations

import functools
import inspect
import os
from typing import Any, Callable, Optional, Sequence

from opentelemetry import trace

from agentweave import schema
from agentweave.decorators import _extract_llm_attrs, _get_config_attrs
from agentweave.exporter import get_tracer

_active_patches: dict[str, Callable] = {}  # provider -> unpatch fn
_proxy_env_overrides: dict[str, Optional[str]] = {}  # env key -> original value

_KNOWN_PROVIDERS = ("anthropic", "openai", "google")


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
    extract_attrs: Optional[Callable[[Any, bool], dict]] = None,
) -> Callable:
    """Build a sync or async wrapper that creates an LLM span around *original*.

    Args:
        original: The original SDK method to wrap.
        provider: Provider name ("anthropic", "openai", "google").
        get_model: Called with ``(args, kwargs)`` to determine the model name.
        captures_output: Whether to capture response preview.
        extract_attrs: Optional custom response→attributes extractor.  Falls
            back to the generic ``_extract_llm_attrs`` when not provided.
    """
    _extract = extract_attrs or (lambda r, c: _extract_llm_attrs(r, c))

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
                # OTel gen_ai.* dual-emit
                span.set_attribute(schema.GEN_AI_OPERATION_NAME, schema.GEN_AI_OP_CHAT)
                span.set_attribute(schema.GEN_AI_SYSTEM, provider)
                for k, v in _get_config_attrs().items():
                    span.set_attribute(k, v)
                # Set after config attrs so explicit model wins over cfg.agent_model
                span.set_attribute(schema.GEN_AI_REQUEST_MODEL, model)

                result = await original(*args, **kwargs)

                for k, v in _extract(result, captures_output).items():
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
                # OTel gen_ai.* dual-emit
                span.set_attribute(schema.GEN_AI_OPERATION_NAME, schema.GEN_AI_OP_CHAT)
                span.set_attribute(schema.GEN_AI_SYSTEM, provider)
                for k, v in _get_config_attrs().items():
                    span.set_attribute(k, v)
                # Set after config attrs so explicit model wins over cfg.agent_model
                span.set_attribute(schema.GEN_AI_REQUEST_MODEL, model)

                result = original(*args, **kwargs)

                for k, v in _extract(result, captures_output).items():
                    span.set_attribute(k, v)
                return result

        return sync_wrapper


# ---------------------------------------------------------------------------
# Provider-specific helpers
# ---------------------------------------------------------------------------

def _get_model_from_kwargs(args: tuple, kwargs: dict) -> str:
    """Extract model from kwargs or positional args (shared by Anthropic & OpenAI)."""
    return kwargs.get("model", args[1] if len(args) > 1 else "unknown")


def _get_google_model(args: tuple, kwargs: dict) -> str:
    """Extract model from a Google GenerativeModel instance (args[0] is self)."""
    if args:
        model_name = getattr(args[0], "model_name", None)
        if model_name:
            # Strip "models/" prefix if present (google-generativeai returns it that way)
            return str(model_name).removeprefix("models/")
    return kwargs.get("model", "unknown")


def _extract_google_attrs(result: Any, captures_output: bool) -> dict:
    """Extract token counts, stop reason, and response preview from Google GenAI response."""
    attrs: dict = {}

    usage = getattr(result, "usage_metadata", None)
    if usage is not None:
        prompt = getattr(usage, "prompt_token_count", None)
        completion = getattr(usage, "candidates_token_count", None)
        if prompt is not None:
            attrs[schema.PROV_LLM_PROMPT_TOKENS] = prompt
        if completion is not None:
            attrs[schema.PROV_LLM_COMPLETION_TOKENS] = completion
        if prompt is not None and completion is not None:
            attrs[schema.PROV_LLM_TOTAL_TOKENS] = prompt + completion
        # OTel gen_ai.* dual-emit
        if prompt is not None:
            attrs[schema.GEN_AI_USAGE_INPUT_TOKENS] = prompt
        if completion is not None:
            attrs[schema.GEN_AI_USAGE_OUTPUT_TOKENS] = completion

    candidates = getattr(result, "candidates", None)
    if candidates:
        finish_reason = getattr(candidates[0], "finish_reason", None)
        if finish_reason is not None:
            attrs[schema.PROV_LLM_STOP_REASON] = str(finish_reason)
            attrs[schema.GEN_AI_RESPONSE_FINISH_REASONS] = [str(finish_reason)]

    if captures_output:
        if candidates:
            content = getattr(candidates[0], "content", None)
            if content is not None:
                parts = getattr(content, "parts", None)
                if parts:
                    text = getattr(parts[0], "text", None)
                    if text:
                        attrs[schema.PROV_LLM_RESPONSE_PREVIEW] = str(text)[:512]

    return attrs


# ---------------------------------------------------------------------------
# Provider patchers
# ---------------------------------------------------------------------------

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


def _patch_google(captures_output: bool) -> Callable:
    """Patch google.generativeai.GenerativeModel.generate_content (sync + async).

    Supports the ``google-generativeai`` package (``import google.generativeai``).
    """
    try:
        import google.generativeai as genai  # noqa: F811
    except ImportError:
        raise

    saved: dict[str, Any] = {}
    cls = genai.GenerativeModel

    saved["sync"] = cls.generate_content
    cls.generate_content = _make_llm_wrapper(
        cls.generate_content,
        "google",
        _get_google_model,
        captures_output,
        extract_attrs=_extract_google_attrs,
    )

    # Async variant (generate_content_async) — may not exist on all versions
    if hasattr(cls, "generate_content_async"):
        saved["async"] = cls.generate_content_async
        cls.generate_content_async = _make_llm_wrapper(
            cls.generate_content_async,
            "google",
            _get_google_model,
            captures_output,
            extract_attrs=_extract_google_attrs,
        )

    def _unpatch() -> None:
        genai.GenerativeModel.generate_content = saved["sync"]
        if "async" in saved:
            genai.GenerativeModel.generate_content_async = saved["async"]

    return _unpatch


_PATCHERS: dict[str, Callable] = {
    "anthropic": _patch_anthropic,
    "openai": _patch_openai,
    "google": _patch_google,
}

# ---------------------------------------------------------------------------
# Proxy mode — rewrite SDK base URLs via environment variables
# ---------------------------------------------------------------------------

# Env vars recognised by each provider's SDK
_PROXY_ENV_KEYS: dict[str, str] = {
    "anthropic": "ANTHROPIC_BASE_URL",
    "openai": "OPENAI_BASE_URL",
    # Google doesn't have a universally recognised env-var for the REST endpoint,
    # so we set GOOGLE_GENAI_BASE_URL which some forks/wrappers respect.
    "google": "GOOGLE_GENAI_BASE_URL",
}


def _apply_proxy_mode(proxy_url: str, providers: Sequence[str]) -> None:
    """Save current env-var values and rewrite them to point at the proxy."""
    base = proxy_url.rstrip("/")
    for name in providers:
        env_key = _PROXY_ENV_KEYS.get(name)
        if not env_key:
            continue
        _proxy_env_overrides[env_key] = os.environ.get(env_key)
        os.environ[env_key] = f"{base}/v1"


def _restore_proxy_mode() -> None:
    """Restore env-var values that were changed by :func:`_apply_proxy_mode`."""
    for env_key, original_value in _proxy_env_overrides.items():
        if original_value is None:
            os.environ.pop(env_key, None)
        else:
            os.environ[env_key] = original_value
    _proxy_env_overrides.clear()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def auto_instrument(
    providers: Optional[Sequence[str]] = None,
    captures_output: bool = False,
    mode: str = "direct",
    proxy_url: Optional[str] = None,
) -> None:
    """Monkey-patch LLM SDK methods to emit OTel spans automatically.

    Supports zero-code-change instrumentation for the Anthropic, OpenAI, and
    Google GenAI SDKs.  Just call ``auto_instrument()`` once at the top of your
    agent script before any other imports.

    Two modes
    ---------
    * **direct** (default): Emit OTel spans directly from each SDK call.  Works
      without the AgentWeave proxy.
    * **proxy**: Rewrite SDK base URLs so all calls are routed through the
      AgentWeave proxy.  The proxy handles tracing — no local OTel emission.

    Args:
        providers: List of provider names to patch.  Defaults to all detected
            providers (``anthropic``, ``openai``, ``google``).
        captures_output: If True, capture response preview in span attributes.
        mode: ``"direct"`` or ``"proxy"``.
        proxy_url: Base URL of the AgentWeave proxy (e.g.
            ``"http://192.168.1.70:30400"``).  Required when ``mode="proxy"``;
            ignored in ``"direct"`` mode.

    Example — direct mode::

        import agentweave
        agentweave.auto_instrument()

    Example — proxy mode::

        import agentweave
        agentweave.auto_instrument(mode="proxy", proxy_url="http://192.168.1.70:30400")

    Example — selective providers::

        import agentweave
        agentweave.auto_instrument(providers=["anthropic", "openai"])
    """
    if mode not in ("direct", "proxy"):
        raise ValueError(f"mode must be 'direct' or 'proxy', got '{mode}'")
    if mode == "proxy" and not proxy_url:
        raise ValueError("proxy_url is required when mode='proxy'")

    targets = list(providers) if providers else list(_KNOWN_PROVIDERS)

    if mode == "proxy":
        # Proxy mode: rewrite base URLs — no SDK-level span emission
        _apply_proxy_mode(proxy_url, targets)  # type: ignore[arg-type]
        # Mark providers as "patched" so uninstrument() knows to restore env vars
        for name in targets:
            if name not in _active_patches:
                _active_patches[name] = lambda: None  # no-op unpatch fn
        return

    # Direct mode: monkey-patch SDK methods to emit spans
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
    """Restore original SDK methods, undoing ``auto_instrument()``.

    Also restores any environment variables overridden in proxy mode.
    """
    targets = list(providers) if providers else list(_active_patches)

    for name in targets:
        unpatch = _active_patches.pop(name, None)
        if unpatch is not None:
            unpatch()

    # Restore proxy env vars if any were set
    if not _active_patches and _proxy_env_overrides:
        _restore_proxy_mode()
    elif providers:
        # Selective uninstrument — only restore env vars for the given providers
        for name in targets:
            env_key = _PROXY_ENV_KEYS.get(name)
            if env_key and env_key in _proxy_env_overrides:
                original_value = _proxy_env_overrides.pop(env_key)
                if original_value is None:
                    os.environ.pop(env_key, None)
                else:
                    os.environ[env_key] = original_value
