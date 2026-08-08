"""Normalize Claude Code's native OTel spans into the AgentWeave prov.* schema.

Claude Code emits its own span hierarchy (``claude_code.interaction`` ->
``claude_code.llm_request`` / ``claude_code.tool`` / ``claude_code.tool.execution``
/ ``claude_code.tool.blocked_on_user``) with ``gen_ai.*`` attributes and
``service.name = "claude-code"``. The dashboard queries ``prov.activity.type``
and filters on the proxy's service names, so those spans are invisible to it
despite being stored. This module adds the ``prov.*`` vocabulary so they aren't.

Design: docs/superpowers/specs/2026-08-07-native-otel-normalization-design.md
Issue:  https://github.com/arniesaha/agentweave/issues/249

Two behaviours here are load-bearing and easy to get wrong:

* ``claude_code.llm_request`` is deliberately **not** given
  ``prov.activity.type = "llm_call"``. The proxy emits its own span for the
  same model call, and the dashboard aggregates cost and tokens on that exact
  attribute value — marking both would double every figure.
* Native reports the *uncached* input tokens separately from cache reads
  (``input_tokens: 2``, ``cache_read_tokens: 55627``) while the proxy reports
  the sum. ``prov.llm.prompt_tokens`` follows the proxy's convention, so the
  two must be added.
"""

from __future__ import annotations

import copy
from typing import Any, Optional

from agentweave import pricing, schema

# Marks which collector produced a span, so the later migration to
# native-as-primary (#249 phase C) is a switch rather than a re-mapping.
PROV_SOURCE = "prov.source"
SOURCE_NATIVE = "native"

_HARNESS = "claude-code"

# claude_code.* span name -> prov.activity.type
_ACTIVITY_BY_SPAN = {
    "claude_code.interaction": "agent_turn",
    "claude_code.tool": "tool_call",
    "claude_code.tool.execution": "tool_execution",
    "claude_code.tool.blocked_on_user": "permission_wait",
    # claude_code.llm_request is absent on purpose — see the module docstring.
}


def _attr_value(value: dict) -> Any:
    """Decode a single OTLP-JSON ``AnyValue``.

    Strict protobuf-JSON encodes int64 as a *string*; Claude Code's exporter
    emits a number. Accept both rather than depending on which one we happen to
    have observed. Unsupported shapes (bytes, arrays, nested maps) decode to
    ``None`` so callers can skip them instead of crashing on a payload change.
    """
    if "stringValue" in value:
        return value["stringValue"]
    if "intValue" in value:
        try:
            return int(value["intValue"])
        except (TypeError, ValueError):
            return None
    if "boolValue" in value:
        return bool(value["boolValue"])
    if "doubleValue" in value:
        return float(value["doubleValue"])
    return None


def _decode_attrs(span: dict) -> dict[str, Any]:
    return {a["key"]: _attr_value(a["value"]) for a in span.get("attributes", [])}


def _set(span: dict, key: str, value: Any) -> None:
    """Append an OTLP-JSON attribute, encoding by Python type."""
    if value is None:
        return
    if isinstance(value, bool):
        encoded = {"boolValue": value}
    elif isinstance(value, int):
        encoded = {"intValue": value}
    elif isinstance(value, float):
        encoded = {"doubleValue": value}
    else:
        encoded = {"stringValue": str(value)}
    span.setdefault("attributes", []).append({"key": key, "value": encoded})


def _normalize_llm_request(span: dict, attrs: dict[str, Any]) -> None:
    """Map token counts, model, and cost — but not prov.activity.type."""
    uncached_in = attrs.get("input_tokens") or 0
    cache_read = attrs.get("cache_read_tokens") or 0
    cache_write = attrs.get("cache_creation_tokens") or 0
    completion = attrs.get("output_tokens") or 0

    # The proxy's prompt_tokens convention includes cache reads; native's
    # input_tokens does not.
    _set(span, schema.PROV_LLM_PROMPT_TOKENS, uncached_in + cache_read)
    _set(span, schema.PROV_LLM_COMPLETION_TOKENS, completion)
    _set(span, "tokens.cache_read", cache_read)
    _set(span, "tokens.cache_write", cache_write)

    model = attrs.get("gen_ai.request.model")
    if model:
        _set(span, schema.PROV_LLM_MODEL, model)
    provider = attrs.get("gen_ai.system")
    if provider:
        _set(span, schema.PROV_LLM_PROVIDER, provider)
    if attrs.get("stop_reason"):
        _set(span, schema.PROV_LLM_STOP_REASON, attrs["stop_reason"])

    if model:
        # Three different token conventions meet here, so be explicit:
        #   native      input_tokens = uncached only, cache buckets separate
        #   prompt_tokens (above)    = uncached + cache_read (proxy convention)
        #   compute_cost input_tokens = TOTAL; it subtracts both cache buckets
        #                               to recover the uncached portion
        # Passing the native or the prompt_tokens value here would floor the
        # uncached portion at 0 and silently under-charge every cached turn.
        cost = pricing.compute_cost(
            model,
            input_tokens=uncached_in + cache_read + cache_write,
            output_tokens=completion,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
        )
        _set(span, "cost.usd", cost)


def _normalize_span(span: dict) -> None:
    """Add prov.* attributes in place. Non-claude_code spans are left alone."""
    name = span.get("name", "")
    if not name.startswith("claude_code."):
        return

    attrs = _decode_attrs(span)

    _set(span, schema.PROV_HARNESS, _HARNESS)
    _set(span, PROV_SOURCE, SOURCE_NATIVE)

    activity = _ACTIVITY_BY_SPAN.get(name)
    if activity:
        _set(span, schema.PROV_ACTIVITY_TYPE, activity)

    session_id = attrs.get("session.id")
    if session_id:
        _set(span, schema.PROV_SESSION_ID, session_id)

    turn = attrs.get("interaction.sequence")
    if turn is not None:
        _set(span, schema.PROV_SESSION_TURN, turn)

    if name == "claude_code.tool":
        _set(span, schema.PROV_TOOL_NAME, attrs.get("tool_name"))
        _set(span, "prov.tool.call_id", attrs.get("tool_use_id"))
    elif name == "claude_code.tool.execution":
        # `success` is the native field; `outcome` matches openclaw.turn spans.
        success = attrs.get("success")
        if success is not None:
            _set(span, "outcome", "completed" if success else "failed")
    elif name == "claude_code.llm_request":
        _normalize_llm_request(span, attrs)


def normalize_payload(document: dict) -> dict:
    """Return a normalized copy of an OTLP-JSON trace export.

    The input is not mutated — the normalizer sits in the telemetry path and
    must not surprise a caller that still holds the original payload.
    """
    out = copy.deepcopy(document)
    for resource_spans in out.get("resourceSpans", []):
        for scope_spans in resource_spans.get("scopeSpans", []):
            for span in scope_spans.get("spans", []):
                _normalize_span(span)
    return out
