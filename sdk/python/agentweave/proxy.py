"""AgentWeave Multi-Provider AI Proxy.

Intercepts requests to Anthropic, Google Gemini, and OpenAI APIs, emits an
OTel span per call with token counts, model, stop reason, and latency, then
forwards the response transparently to the caller.

Provider is detected automatically from the request path:
  /v1/messages              → Anthropic  (api.anthropic.com)
  /v1/models                → Anthropic or OpenAI (detected from auth headers)
  /v1beta/models/...        → Google     (generativelanguage.googleapis.com)
  /v1/models/...            → Google     (generativelanguage.googleapis.com)
  /v1/chat/completions      → OpenAI     (api.openai.com)
  /v1/completions           → OpenAI     (api.openai.com)
  /v1/embeddings            → OpenAI     (api.openai.com)
  /v1/responses             → OpenAI     (api.openai.com)  [Responses API]

Note: ``GET /v1/models`` is handled by a dedicated route that inspects auth
headers to determine whether to forward to Anthropic or OpenAI.  This fixes
Claude Code CLI model validation when ``ANTHROPIC_BASE_URL`` points at the
AgentWeave proxy (see issue #119).

Works for both streaming and non-streaming requests. Zero code changes needed
in calling agents — just point ANTHROPIC_BASE_URL / GOOGLE_GENAI_BASE_URL /
OPENAI_BASE_URL at this proxy.

Usage::

    agentweave proxy start --port 4000 --endpoint http://tempo-host:4318

    # Anthropic agents
    export ANTHROPIC_BASE_URL=http://localhost:4000

    # Google / Gemini agents (pi-mono / Max)
    export GOOGLE_GENAI_BASE_URL=http://localhost:4000
    # or set in Google SDK: genai.configure(client_options={"api_endpoint": "localhost:4000"})

    # OpenAI agents
    export OPENAI_BASE_URL=http://localhost:4000

    # Tag calls by agent
    # X-AgentWeave-Agent-Id: max-v1

    # Proxy-side key injection (callers can use ANTHROPIC_API_KEY=dummy):
    export AGENTWEAVE_ANTHROPIC_API_KEY=sk-ant-...   # on the proxy host
    export AGENTWEAVE_GOOGLE_API_KEY=AIza...          # on the proxy host
    export AGENTWEAVE_OPENAI_API_KEY=sk-...           # on the proxy host
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import secrets
import time
from typing import Any, AsyncIterator

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from opentelemetry.trace import NonRecordingSpan, SpanContext, StatusCode, TraceFlags

from agentweave import schema
from agentweave.config import AgentWeaveConfig
from agentweave.pii import PIIBlockedError, PIIMode, scan_text as _pii_scan
from agentweave.exporter import get_tracer, _provider
from agentweave.health import (
    record_span as _health_record_span,
    get_all_scores as _health_get_all_scores,
    maybe_fire_webhook as _health_maybe_fire_webhook,
    _agent_config as _health_agent_config,
    _window_seconds as _health_window_seconds,
    _global_threshold as _health_global_threshold,
)
from agentweave.budget import get_tracker as _get_budget_tracker
from agentweave.pricing import compute_cost

logger = logging.getLogger("agentweave.proxy")

# --- Upstream base URLs ---
_ANTHROPIC_BASE = "https://api.anthropic.com"
_GOOGLE_BASE = "https://generativelanguage.googleapis.com"
_OPENAI_BASE = "https://api.openai.com"

# Headers always stripped before forwarding (hop-by-hop + proxy-specific)
_SKIP_HEADERS_ALWAYS = {
    "host", "content-length", "transfer-encoding", "connection",
    "x-agentweave-agent-id",
    "x-agentweave-agent-model",
    "x-agentweave-session-id",
    "x-agentweave-project",
    "x-agentweave-turn",
    "x-agentweave-trace-id",
    "x-agentweave-turn-count",
    "x-agentweave-parent-session-id",
    "x-agentweave-agent-type",
    "x-agentweave-turn-depth",
}

# ---------------------------------------------------------------------------
# Deterministic trace ID helpers
# ---------------------------------------------------------------------------

_TRACE_ID_RE = re.compile(r'^[0-9a-fA-F]{32}$')


def _normalize_trace_id(raw: str) -> int | None:
    """Normalize a caller-supplied trace ID to a 128-bit integer.

    Accepts a valid 32-char hex string or any arbitrary string (hashed via
    SHA-256 to produce a stable 32-char hex).  Returns ``None`` for empty input.
    """
    if not raw:
        return None
    raw = raw.strip()
    if not raw:
        return None
    if _TRACE_ID_RE.match(raw):
        return int(raw, 16)
    return int(hashlib.sha256(raw.encode()).hexdigest()[:32], 16)


def _context_for_trace_id(trace_id_int: int):
    """Return an OTel context that seeds child spans with *trace_id_int*."""
    from opentelemetry import trace as _trace
    parent_span_id = int.from_bytes(secrets.token_bytes(8), "big")
    span_ctx = SpanContext(
        trace_id=trace_id_int,
        span_id=parent_span_id,
        is_remote=True,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
    )
    return _trace.set_span_in_context(NonRecordingSpan(span_ctx))

# Runtime auth token. Set AGENTWEAVE_PROXY_TOKEN or --auth-token.
# Empty = open mode (dev/localhost only).
_PROXY_TOKEN: str | None = os.getenv("AGENTWEAVE_PROXY_TOKEN") or None

# Proxy-side API key injection — allows callers to set ANTHROPIC_API_KEY=dummy
# when the proxy host holds the real credentials.
# IMPORTANT: Only standard API keys (sk-ant-api03_*) can be injected.
# OAuth tokens (sk-ant-oat*) MUST NOT be used — they expire and require
# SDK-level auth flow with TLS fingerprinting.  See DEPLOYMENT-RUNBOOK.md.
_raw_anthropic_key = os.getenv("AGENTWEAVE_ANTHROPIC_API_KEY", "").strip()
if _raw_anthropic_key and _raw_anthropic_key.startswith("sk-ant-oat"):
    logging.getLogger("agentweave.proxy").warning(
        "AGENTWEAVE_ANTHROPIC_API_KEY contains an OAuth token (sk-ant-oat*) "
        "which CANNOT be used for proxy injection — OAuth tokens expire and "
        "require SDK-level auth.  Use a standard API key (sk-ant-api03_*) or "
        "clear this env var to enable pass-through mode.  Ignoring."
    )
    _ANTHROPIC_INJECT_KEY: str | None = None
else:
    _ANTHROPIC_INJECT_KEY: str | None = _raw_anthropic_key or None

_GOOGLE_INJECT_KEY: str | None = os.getenv("AGENTWEAVE_GOOGLE_API_KEY") or None
_OPENAI_INJECT_KEY: str | None = os.getenv("AGENTWEAVE_OPENAI_API_KEY") or None


def _inject_anthropic_key(forward_headers: dict[str, str], query_string: str) -> str:
    """Inject a standard Anthropic API key into *forward_headers*.

    Only standard ``sk-ant-api03_*`` keys are supported.  OAuth tokens
    are rejected at startup (see guard above).

    Returns the (possibly modified) *query_string*.
    """
    if not _ANTHROPIC_INJECT_KEY:
        return query_string
    forward_headers["x-api-key"] = _ANTHROPIC_INJECT_KEY
    return query_string


# Global session context — set at startup from env, overrideable via POST /session
_session_context: dict[str, str] = {
    k: v for k, v in {
        "prov.session.id": os.getenv("AGENTWEAVE_SESSION_ID", ""),
        "prov.parent.session.id": os.getenv("AGENTWEAVE_PARENT_SESSION_ID", ""),
        "prov.task.label": os.getenv("AGENTWEAVE_TASK_LABEL", ""),
        "prov.agent.type": os.getenv("AGENTWEAVE_AGENT_TYPE", ""),
        "prov.project": os.getenv("AGENTWEAVE_PROJECT", ""),
    }.items() if v
}

# Gemini model name from URL, e.g. /v1beta/models/gemini-2.5-pro:generateContent
_GEMINI_MODEL_RE = re.compile(r"/models/([^/:]+)")

app = FastAPI(
    title="AgentWeave Proxy",
    description="Multi-provider AI observability proxy (Anthropic + Google Gemini + OpenAI)",
    version="0.2.0",
)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def _check_auth(request: Request) -> JSONResponse | None:
    """Return 401 if token auth fails, else None (pass through)."""
    if not _PROXY_TOKEN:
        return None
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return JSONResponse(
            {"error": "missing_token", "message": "Authorization: Bearer <token> required"},
            status_code=401,
        )
    if not secrets.compare_digest(auth[len("Bearer "):], _PROXY_TOKEN):
        return JSONResponse(
            {"error": "invalid_token", "message": "Invalid proxy token"},
            status_code=401,
        )
    return None


# ---------------------------------------------------------------------------
# Provider detection
# ---------------------------------------------------------------------------

_OPENAI_PATHS = {"v1/chat/completions", "v1/completions", "v1/embeddings", "v1/responses"}
_OPENAI_PREFIXES = ("v1/chat/", "v1/completions", "v1/embeddings", "v1/responses", "v1/models", "v1/files", "v1/fine_tuning", "v1/assistants", "v1/threads", "v1/images", "v1/audio")


def _detect_provider(path: str) -> str:
    """Return 'google', 'openai', or 'anthropic' based on the request path."""
    # Google: v1beta/* or v1/models/{model}:action (colon-action syntax is Google-specific)
    if path.startswith("v1beta/") or (
        path.startswith("v1/") and "/models/" in path and ":" in path
    ):
        return "google"
    # OpenAI: exact match (fast path) then prefix match for future/unknown endpoints
    if path in _OPENAI_PATHS or any(path.startswith(p) for p in _OPENAI_PREFIXES):
        return "openai"
    return "anthropic"


def _upstream_url(provider: str, path: str, query_string: str) -> str:
    if provider == "google":
        base = _GOOGLE_BASE
    elif provider == "openai":
        base = _OPENAI_BASE
    else:
        base = _ANTHROPIC_BASE
    url = f"{base}/{path}"
    if query_string:
        url += f"?{query_string}"
    return url


def _extract_model(provider: str, path: str, body: dict) -> str:
    if provider == "google":
        m = _GEMINI_MODEL_RE.search(path)
        return m.group(1) if m else "gemini"
    return body.get("model", "unknown")


def _is_streaming(provider: str, path: str, body: dict) -> bool:
    if provider == "google":
        return "streamGenerateContent" in path
    return bool(body.get("stream", False))


# ---------------------------------------------------------------------------
# Main route
# ---------------------------------------------------------------------------

@app.get("/health", include_in_schema=True)
async def health() -> dict:
    """Liveness/readiness probe — no auth required."""
    resp: dict[str, Any] = {"status": "ok", "version": app.version}
    # Surface key injection status so operators can verify config
    resp["key_injection"] = {
        "anthropic": bool(_ANTHROPIC_INJECT_KEY),
        "openai": bool(_OPENAI_INJECT_KEY),
        "google": bool(_GOOGLE_INJECT_KEY),
    }
    return resp


@app.post("/session", include_in_schema=True)
async def set_session_context(body: dict):
    """Override the global session context for all subsequent spans."""
    global _session_context
    _session_context = {k: v for k, v in {
        "prov.session.id": body.get("session_id", ""),
        "prov.parent.session.id": body.get("parent_session_id", ""),
        "prov.task.label": body.get("task_label", ""),
        "prov.agent.type": body.get("agent_type", ""),
        "prov.project": body.get("project", ""),
    }.items() if v}
    return {"ok": True, "context": _session_context}


@app.get("/session", include_in_schema=True)
async def get_session_context():
    """Return the current global session context."""
    return _session_context


# ---------------------------------------------------------------------------
# Budget endpoints (issue #110)
# ---------------------------------------------------------------------------

@app.get("/budget/status", include_in_schema=True)
async def budget_status():
    """Return current daily spend per agent and global, plus configured limits.

    Response shape::

        {
          "agents": {
            "nix-v1": {"spent": 1.23, "limit": 5.00},
            ...
          },
          "global": {"spent": 3.45, "limit": 10.00}
        }

    ``limit`` is ``null`` when no limit is configured for that scope.
    """
    tracker = _get_budget_tracker()
    cfg = tracker._cfg
    agents: dict = {}
    # Include any agent that either has a limit configured or has spend recorded
    known_ids = set(cfg.agents.keys()) | tracker.known_agent_ids()
    for aid in known_ids:
        ab = cfg.agents.get(aid)
        agents[aid] = {
            "spent": round(tracker.get_spent(aid), 6),
            "limit": ab.daily if ab else None,
        }
    return {
        "agents": agents,
        "global": {
            "spent": round(tracker.get_spent(), 6),
            "limit": cfg.global_daily,
        },
    }


@app.post("/budget/config", include_in_schema=True)
async def set_budget_config(body: dict):
    """Update budget limits at runtime and optionally persist them to the config file.

    Request body::

        {
          "global_daily": 10.00,           // optional
          "agents": {                       // optional
            "nix-v1": {"daily": 5.00}
          },
          "webhook_url": "https://...",    // optional
          "persist": true                   // optional; saves to budget.json file
        }

    Returns the updated configuration.
    """
    from agentweave.budget import BudgetConfig, AgentBudget, reset_tracker

    tracker = _get_budget_tracker()
    cfg = tracker._cfg

    # Update config fields from request
    if "global_daily" in body:
        cfg.global_daily = float(body["global_daily"]) if body["global_daily"] is not None else None
    if "agents" in body:
        for aid, limits in body["agents"].items():
            cfg.agents[aid] = AgentBudget(
                daily=float(limits["daily"]) if limits.get("daily") is not None else None,
            )
    if "webhook_url" in body:
        cfg.webhook_url = str(body["webhook_url"]) if body["webhook_url"] else None

    if body.get("persist"):
        cfg.save()

    return {
        "ok": True,
        "config": {
            "global_daily": cfg.global_daily,
            "agents": {aid: {"daily": ab.daily} for aid, ab in cfg.agents.items()},
            "webhook_url": cfg.webhook_url,
        },
    }


# ---------------------------------------------------------------------------
# Agent health scoring endpoints (issue #116)
# ---------------------------------------------------------------------------

@app.get("/v1/agent-health", include_in_schema=True)
async def get_agent_health():
    """Return health scores for all agents observed in the current window.

    Each score is a 0-100 composite of error rate, P95 latency, cost per
    session, and tool retry rate.  The ``badge`` field maps to a colour:
      ``green``  (score >= 80)
      ``yellow`` (score >= 60)
      ``red``    (score < 60)

    Scores are recomputed on every request from the in-memory span buffer.
    """
    scores = _health_get_all_scores()
    # Fire webhooks asynchronously (non-blocking)
    for score in scores:
        asyncio.create_task(_health_maybe_fire_webhook(score))
    return {
        "scores": [
            {
                "agent_id": s.agent_id,
                "score": s.score,
                "badge": s.badge,
                "error_rate": s.error_rate,
                "p95_latency_ms": s.p95_latency_ms,
                "avg_cost_per_session": s.avg_cost_per_session,
                "tool_retry_rate": s.tool_retry_rate,
                "span_count": s.span_count,
                "window_seconds": s.window_seconds,
                "threshold": s.threshold,
                "computed_at": s.computed_at,
                "components": s.components,
            }
            for s in scores
        ],
        "window_seconds": _health_window_seconds(),
        "global_threshold": _health_global_threshold(),
    }


@app.get("/v1/agent-health/{agent_id}", include_in_schema=True)
async def get_agent_health_single(agent_id: str):
    """Return the health score for a single agent."""
    import time as _time
    from agentweave.health import _spans, compute_health_score, _window_seconds
    cutoff_ms = (_time.time() - _window_seconds()) * 1000
    agent_spans = [s for s in _spans if s.agent_id == agent_id and s.timestamp_ms >= cutoff_ms]
    score = compute_health_score(agent_id, agent_spans)
    asyncio.create_task(_health_maybe_fire_webhook(score))
    return {
        "agent_id": score.agent_id,
        "score": score.score,
        "badge": score.badge,
        "error_rate": score.error_rate,
        "p95_latency_ms": score.p95_latency_ms,
        "avg_cost_per_session": score.avg_cost_per_session,
        "tool_retry_rate": score.tool_retry_rate,
        "span_count": score.span_count,
        "window_seconds": score.window_seconds,
        "threshold": score.threshold,
        "computed_at": score.computed_at,
        "components": score.components,
    }


@app.post("/v1/agent-health/config", include_in_schema=True)
async def set_agent_health_config(body: dict):
    """Configure per-agent SLO thresholds and baselines.

    Body example::

        {
            "agent_id": "nix-v1",
            "threshold": 70,
            "p95_baseline_ms": 5000,
            "cost_baseline_usd": 0.005
        }

    Set ``agent_id`` to ``"*"`` to update the global defaults for all agents.
    """
    agent_id = body.get("agent_id", "*")
    if not agent_id:
        return {"ok": False, "error": "agent_id is required"}
    _health_agent_config[agent_id] = {k: v for k, v in body.items() if k != "agent_id"}
    return {"ok": True, "agent_id": agent_id, "config": _health_agent_config[agent_id]}


@app.get("/v1/agent-health/config/all", include_in_schema=True)
async def get_agent_health_config():
    """Return the current per-agent SLO config."""
    return {
        "configs": _health_agent_config,
        "global_threshold": _health_global_threshold(),
        "window_seconds": _health_window_seconds(),
    }


# ---------------------------------------------------------------------------
# Claude Code hooks endpoints
# ---------------------------------------------------------------------------

def _extract_parent_context(traceparent: str | None):
    """Parse a W3C traceparent header into an OTel context for span linking."""
    if not traceparent:
        return None
    try:
        from opentelemetry.propagators.textmap import DictGetter
        from opentelemetry import propagate
        ctx = propagate.extract(carrier={"traceparent": traceparent}, getter=DictGetter())
        return ctx
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Prompt registry endpoints (issue #111)
# ---------------------------------------------------------------------------

from agentweave import prompts as _prompts_mod  # noqa: E402 — lazy import after app init


@app.get("/v1/prompts", include_in_schema=True)
async def list_prompts():
    """List the latest version of every named prompt in the registry."""
    records = _prompts_mod.list_prompts()
    return {"prompts": [r.to_dict() for r in records]}


@app.get("/v1/prompts/{name}", include_in_schema=True)
async def get_prompt_latest(name: str):
    """Get the latest version of a named prompt."""
    record = _prompts_mod.get_prompt(name)
    if record is None:
        return JSONResponse({"error": f"Prompt '{name}' not found"}, status_code=404)
    return record.to_dict()


@app.get("/v1/prompts/{name}/versions", include_in_schema=True)
async def list_prompt_versions(name: str):
    """List all versions of a named prompt."""
    records = _prompts_mod.list_prompt_versions(name)
    if not records:
        return JSONResponse({"error": f"Prompt '{name}' not found"}, status_code=404)
    return {"name": name, "versions": [r.to_dict() for r in records]}


@app.get("/v1/prompts/{name}/{version}", include_in_schema=True)
async def get_prompt_version(name: str, version: str):
    """Get a specific version of a named prompt."""
    record = _prompts_mod.get_prompt(name, version)
    if record is None:
        return JSONResponse(
            {"error": f"Prompt '{name}' version '{version}' not found"}, status_code=404
        )
    return record.to_dict()


@app.post("/v1/prompts", include_in_schema=True)
async def create_prompt(body: dict):
    """Create or version a prompt.

    Body::

        {
            "name": "system-prompt",
            "content": "You are a helpful assistant...",
            "description": "Main system prompt",  // optional
            "version": "v2"                        // optional; default: SHA-256 hash of content
        }
    """
    name = body.get("name")
    content = body.get("content")
    if not name or not content:
        return JSONResponse({"error": "name and content are required"}, status_code=400)
    description = body.get("description", "")
    version = body.get("version") or None
    try:
        record = _prompts_mod.create_prompt(name, content, description=description, version=version)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
    return JSONResponse(record.to_dict(), status_code=201)


@app.put("/v1/prompts/{name}", include_in_schema=True)
async def update_prompt(name: str, body: dict):
    """Update a prompt by creating a new version.

    Body::

        {
            "content": "New prompt text...",
            "description": "Updated description",  // optional
            "version": "v3"                         // optional; default: SHA-256 hash
        }
    """
    content = body.get("content")
    if not content:
        return JSONResponse({"error": "content is required"}, status_code=400)
    description = body.get("description", None)
    version = body.get("version") or None
    try:
        record = _prompts_mod.update_prompt(name, content, description=description, version=version)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
    return record.to_dict()


@app.delete("/v1/prompts/{name}", include_in_schema=True)
async def delete_prompt(name: str):
    """Delete all versions of a named prompt."""
    deleted = _prompts_mod.delete_prompt(name)
    if deleted == 0:
        return JSONResponse({"error": f"Prompt '{name}' not found"}, status_code=404)
    return {"ok": True, "deleted": deleted, "name": name}


@app.post("/hooks/span", include_in_schema=True)
async def hooks_span(body: dict):
    """Receive a single span from Claude Code hooks (e.g., SubagentStop).

    Creates an OTel span with the provided attributes and exports it.
    Accepts optional ``traceparent`` to link the span to a parent trace.
    """
    tracer = get_tracer("agentweave.hooks")
    span_name = body.get("span_name", "hook.span")
    session_id = body.get("session_id", "")
    attributes = body.get("attributes", {})
    parent_ctx = _extract_parent_context(body.get("traceparent"))

    with tracer.start_as_current_span(span_name, context=parent_ctx) as span:
        span.set_attribute("prov.session.id", session_id)
        span.set_attribute("prov.hook.source", "claude-code")
        for key, value in attributes.items():
            if value is not None:
                span.set_attribute(key, str(value) if not isinstance(value, (int, float, bool)) else value)

    return {"ok": True, "span_name": span_name}


@app.post("/hooks/batch", include_in_schema=True)
async def hooks_batch(body: dict):
    """Receive a batch of buffered events from Claude Code hooks (Stop hook).

    Creates OTel spans for each event in the batch. Accepts optional
    ``traceparent`` so tool-call spans become children of the session's
    root span rather than floating as orphans.
    """
    tracer = get_tracer("agentweave.hooks")
    session_id = body.get("session_id", "")
    events = body.get("events", [])
    parent_ctx = _extract_parent_context(body.get("traceparent"))

    spans_created = 0
    for event in events:
        event_type = event.get("event", "unknown")
        ts = event.get("ts")
        data = event.get("data", {})

        span_name = f"hook.{event_type}"
        with tracer.start_as_current_span(span_name, context=parent_ctx) as span:
            span.set_attribute("prov.session.id", session_id or event.get("session_id", ""))
            span.set_attribute("prov.hook.source", "claude-code")
            span.set_attribute("prov.hook.event_type", event_type)
            if ts:
                span.set_attribute("prov.hook.timestamp_ms", ts)

            # Extract tool use data if present
            tool_name = data.get("tool_name") or data.get("toolName")
            if tool_name:
                span.set_attribute("prov.tool.name", tool_name)
            tool_input = data.get("tool_input") or data.get("toolInput")
            if tool_input and isinstance(tool_input, str):
                span.set_attribute("prov.tool.input_preview", tool_input[:512])
            tool_result = data.get("tool_result") or data.get("toolResult")
            if tool_result and isinstance(tool_result, str):
                span.set_attribute("prov.tool.result_preview", tool_result[:512])

        spans_created += 1

    return {"ok": True, "spans_created": spans_created}


@app.get("/v1/models", include_in_schema=True, response_model=None)
async def list_models(request: Request) -> JSONResponse:
    """Passthrough for GET /v1/models.

    Claude Code CLI calls this endpoint on startup to validate the model name.
    The path ``v1/models`` is also used by OpenAI-compatible clients. We detect
    which upstream to forward to by inspecting the auth headers:

    * ``x-api-key`` header (Anthropic SDK style) → Anthropic models API
    * ``authorization: Bearer sk-ant-*`` → Anthropic models API
    * Otherwise → OpenAI models API

    This ensures Claude Code CLI launched with
    ``ANTHROPIC_BASE_URL=http://<proxy>/v1`` sees the full Anthropic model
    list and can validate model names successfully.
    """
    if (denied := _check_auth(request)) is not None:
        return denied

    query_string = request.url.query

    forward_headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in _SKIP_HEADERS_ALWAYS
    }
    if _PROXY_TOKEN:
        forward_headers.pop("authorization", None)

    # Detect provider from auth headers: Anthropic uses x-api-key or Bearer sk-ant-*
    x_api_key = forward_headers.get("x-api-key", "")
    auth_header = forward_headers.get("authorization", "")
    is_anthropic_caller = bool(
        x_api_key
        or auth_header.startswith("Bearer sk-ant-")
        or auth_header.startswith("Bearer sk-oat-")
        or (_ANTHROPIC_INJECT_KEY and not forward_headers.get("authorization", "").startswith("Bearer sk-"))
    )

    if is_anthropic_caller:
        # Only inject if caller doesn't have a real Anthropic key
        client_key = x_api_key or auth_header
        if _ANTHROPIC_INJECT_KEY and (not client_key or not client_key.startswith(("sk-ant", "Bearer sk-ant", "Bearer sk-oat"))):
            query_string = _inject_anthropic_key(forward_headers, query_string)
        upstream_url = f"{_ANTHROPIC_BASE}/v1/models"
    else:
        client_key = forward_headers.get("authorization", "")
        if _OPENAI_INJECT_KEY and (not client_key or not client_key.startswith("Bearer sk-")):
            forward_headers["authorization"] = f"Bearer {_OPENAI_INJECT_KEY}"
        upstream_url = f"{_OPENAI_BASE}/v1/models"

    if query_string:
        upstream_url += f"?{query_string}"

    logger.debug("→ GET %s (models passthrough, anthropic=%s)", upstream_url, is_anthropic_caller)
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(upstream_url, headers=forward_headers)
        try:
            content = resp.json()
        except (ValueError, UnicodeDecodeError):
            content = {"error": {"type": "upstream_error", "message": resp.text}}
        return JSONResponse(content=content, status_code=resp.status_code)
    except Exception as exc:
        logger.error("models passthrough error: %s", exc)
        return JSONResponse(
            {"error": {"type": "proxy_error", "message": str(exc)}},
            status_code=502,
        )


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"], response_model=None)
async def proxy(path: str, request: Request) -> StreamingResponse | JSONResponse:
    if (denied := _check_auth(request)) is not None:
        return denied

    body_bytes = await request.body()
    body: dict[str, Any] = {}
    if body_bytes:
        try:
            body = json.loads(body_bytes)
        except json.JSONDecodeError:
            pass

    provider = _detect_provider(path)
    model = _extract_model(provider, path, body)
    is_stream = _is_streaming(provider, path, body)
    query_string = request.url.query

    agent_id = (
        request.headers.get("x-agentweave-agent-id")
        or os.getenv("AGENTWEAVE_AGENT_ID")
        or _config_value("agent_id")
        or "unattributed"
    )
    agent_model = (
        request.headers.get("x-agentweave-agent-model")
        or _config_value("agent_model")
        or model
    )

    session_id = (
        request.headers.get("x-agentweave-session-id")
        or os.getenv("AGENTWEAVE_SESSION_ID")
    )
    # Only use explicitly set project — do NOT infer from agent_id prefix.
    # Use AGENTWEAVE_PROJECT env var or X-AgentWeave-Project header.
    project = request.headers.get("x-agentweave-project") or os.getenv("AGENTWEAVE_PROJECT") or None
    turn: int | None = None
    turn_raw = request.headers.get("x-agentweave-turn")
    if turn_raw is not None:
        try:
            turn = int(turn_raw)
        except (ValueError, TypeError):
            logger.warning("x-agentweave-turn is not a valid integer: %r", turn_raw)

    # Deterministic trace ID — allows callers to pin a retry to the same trace
    det_trace_id_raw: str | None = request.headers.get("x-agentweave-trace-id")
    det_trace_id_int: int | None = _normalize_trace_id(det_trace_id_raw) if det_trace_id_raw else None

    # Per-session LLM turn counter supplied by external callers via header
    turn_count: int | None = None
    turn_count_raw = request.headers.get("x-agentweave-turn-count")
    if turn_count_raw is not None:
        try:
            turn_count = int(turn_count_raw)
        except (ValueError, TypeError):
            logger.warning("x-agentweave-turn-count is not a valid integer: %r", turn_count_raw)

    # W3C traceparent passthrough (issue #44)
    # Fall back to AGENTWEAVE_TRACEPARENT env var so LLM spans are linked to
    # the openclaw.turn root span even when no header is present (issue #133).
    traceparent: str | None = (
        request.headers.get("traceparent")
        or os.environ.get("AGENTWEAVE_TRACEPARENT")
    )

    # Sub-agent attribution headers (issue #15)
    parent_session_id: str | None = (
        request.headers.get("x-agentweave-parent-session-id")
        or os.getenv("AGENTWEAVE_PARENT_SESSION_ID")
        or None
    )
    agent_type: str | None = (
        request.headers.get("x-agentweave-agent-type")
        or os.getenv("AGENTWEAVE_AGENT_TYPE")
        or None
    )
    turn_depth: int | None = None
    turn_depth_raw = request.headers.get("x-agentweave-turn-depth")
    if turn_depth_raw is not None:
        try:
            turn_depth = int(turn_depth_raw)
        except (ValueError, TypeError):
            logger.warning("x-agentweave-turn-depth is not a valid integer: %r", turn_depth_raw)

    forward_headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in _SKIP_HEADERS_ALWAYS
    }
    # When proxy auth is enabled, the "authorization" header is the proxy
    # token — strip it and rely on x-api-key / x-goog-api-key for upstream.
    # In open mode (no token), forward authorization so SDKs that send the
    # API key via Bearer token (e.g. OpenClaw) keep working.
    if _PROXY_TOKEN:
        forward_headers.pop("authorization", None)

    # Inject proxy-configured API keys ONLY when the client didn't send a
    # real key.  This preserves pass-through for clients like OpenClaw and
    # Claude Code that send their own OAuth/API keys, while allowing
    # external scripts to call with a placeholder (e.g. x-api-key: dummy).
    if provider == "anthropic" and _ANTHROPIC_INJECT_KEY:
        client_key = (
            forward_headers.get("x-api-key", "")
            or forward_headers.get("authorization", "")
        )
        if not client_key or not client_key.startswith("sk-ant"):
            query_string = _inject_anthropic_key(forward_headers, query_string)
    elif provider == "openai" and _OPENAI_INJECT_KEY:
        client_key = forward_headers.get("authorization", "")
        if not client_key or not client_key.startswith("Bearer sk-"):
            forward_headers["authorization"] = f"Bearer {_OPENAI_INJECT_KEY}"
    elif provider == "google" and _GOOGLE_INJECT_KEY:
        client_key = forward_headers.get("x-goog-api-key", "")
        if not client_key or not client_key.startswith("AIza"):
            forward_headers["x-goog-api-key"] = _GOOGLE_INJECT_KEY
            import urllib.parse
            qs_params = urllib.parse.parse_qs(query_string, keep_blank_values=True)
            qs_params.pop("key", None)
            query_string = urllib.parse.urlencode({k: v[0] for k, v in qs_params.items()})

    upstream = _upstream_url(provider, path, query_string)
    logger.debug("→ %s %s provider=%s model=%s stream=%s",
                 request.method, upstream, provider, model, is_stream)

    kwargs = dict(
        upstream_url=upstream,
        method=request.method,
        headers=forward_headers,
        body=body,
        body_bytes=body_bytes,
        model=model,
        provider=provider,
        agent_id=agent_id,
        agent_model=agent_model,
        path=path,
        session_id=session_id,
        project=project,
        turn=turn,
        det_trace_id_int=det_trace_id_int,
        det_trace_id_raw=det_trace_id_raw,
        turn_count=turn_count,
        parent_session_id=parent_session_id,
        agent_type=agent_type,
        turn_depth=turn_depth,
        traceparent=traceparent,
    )

    try:
        if is_stream:
            media = "application/json" if provider == "google" else "text/event-stream"
            # Peek at upstream status before committing to a 200 StreamingResponse.
            # If upstream returned an error (4xx/5xx), return the error body with the
            # correct status code so callers see the real error instead of an empty
            # SSE stream that looks like a timeout.
            preflight = await _stream_preflight(
                method=kwargs["method"],
                upstream_url=kwargs["upstream_url"],
                headers=kwargs["headers"],
                body_bytes=kwargs["body_bytes"],
            )
            if preflight is not None:
                logger.warning("← upstream error status=%d (short-circuit)", preflight.status_code)
                return preflight
            return StreamingResponse(_stream_and_trace(**kwargs), media_type=media)
        return await _request_and_trace(**kwargs)
    except PIIBlockedError as exc:
        logger.warning("PII blocked: %s", exc)
        return JSONResponse(
            {"error": {"type": "pii_blocked", "message": str(exc)}},
            status_code=400,
        )


# ---------------------------------------------------------------------------
# Streaming preflight — detect upstream errors before committing to HTTP 200
# ---------------------------------------------------------------------------

async def _stream_preflight(
    method: str, upstream_url: str, headers: dict, body_bytes: bytes,
) -> JSONResponse | None:
    """Send a HEAD-style probe using the real request.

    Opens a streaming connection, reads the status code, and if it indicates
    an error (>= 400), consumes the body and returns a JSONResponse with the
    correct status.  Returns None when the upstream is healthy so the caller
    can proceed with the normal streaming path.

    NOTE: This intentionally does NOT consume the body on success — the
    subsequent ``_stream_and_trace`` call opens its own connection.  The
    extra round-trip on errors is negligible compared to the cost of
    silently swallowing a 429 behind a 200.
    """
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            async with client.stream(
                method, upstream_url, headers=headers, content=body_bytes,
            ) as resp:
                if resp.status_code < 400:
                    return None  # healthy — let the real stream handler run
                # Read the error body so we can relay it to the caller.
                body = await resp.aread()
                try:
                    data = json.loads(body)
                except Exception:
                    data = {"type": "error", "error": {"type": "proxy_error",
                            "message": body.decode(errors="replace")[:2000]}}
                return JSONResponse(content=data, status_code=resp.status_code)
    except httpx.TimeoutException:
        return None  # let the real handler deal with timeouts
    except Exception:
        return None  # unexpected error — fall through to normal path


# ---------------------------------------------------------------------------
# Non-streaming handler
# ---------------------------------------------------------------------------

async def _request_and_trace(
    upstream_url: str, method: str, headers: dict, body: dict, body_bytes: bytes,
    model: str, provider: str, agent_id: str, agent_model: str, path: str,
    session_id: str | None = None, project: str | None = None, turn: int | None = None,
    det_trace_id_int: int | None = None, det_trace_id_raw: str | None = None,
    turn_count: int | None = None,
    parent_session_id: str | None = None, agent_type: str | None = None,
    turn_depth: int | None = None,
    traceparent: str | None = None,
) -> JSONResponse:
    tracer = get_tracer()
    _span_ctx = _context_for_trace_id(det_trace_id_int) if det_trace_id_int is not None else None
    with tracer.start_as_current_span(f"{schema.SPAN_PREFIX_LLM}.{model}", context=_span_ctx) as span:
        _set_request_attrs(span, model=model, provider=provider,
                           agent_id=agent_id, agent_model=agent_model,
                           path=path, body=body,
                           session_id=session_id, project=project, turn=turn,
                           det_trace_id_raw=det_trace_id_raw,
                           parent_session_id=parent_session_id,
                           agent_type=agent_type, turn_depth=turn_depth,
                           traceparent=traceparent)
        if turn_count is not None:
            span.set_attribute(schema.AGENT_TURN_COUNT, turn_count)
        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=300) as client:
                resp = await client.request(
                    method, upstream_url, headers=headers, content=body_bytes,
                )
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            data = resp.json()
            _extract_and_set_response(span, data=data, provider=provider, elapsed_ms=elapsed_ms, model=model)
            span.set_status(StatusCode.OK)
            # Record span in health tracker
            cost_attr = span.attributes.get(schema.COST_USD) if hasattr(span, "attributes") else None
            cost_usd = float(cost_attr) if cost_attr is not None else 0.0
            _health_record_span(
                agent_id=agent_id or "unknown",
                session_id=session_id or "",
                duration_ms=float(elapsed_ms),
                is_error=False,
                cost_usd=cost_usd,
            )
            # Record cost in budget tracker
            try:
                _tracker = _get_budget_tracker()
                _tracker.record_cost(agent_id or "unknown", cost_usd, session_id=session_id, tracer=tracer)
            except Exception:
                pass
            return JSONResponse(content=data, status_code=resp.status_code)
        except Exception as exc:
            span.set_status(StatusCode.ERROR, str(exc))
            span.record_exception(exc)
            # Record error span in health tracker
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            _health_record_span(
                agent_id=agent_id or "unknown",
                session_id=session_id or "",
                duration_ms=float(elapsed_ms),
                is_error=True,
                cost_usd=0.0,
            )
            raise


# ---------------------------------------------------------------------------
# Streaming handler
# ---------------------------------------------------------------------------

async def _stream_and_trace(
    upstream_url: str, method: str, headers: dict, body: dict, body_bytes: bytes,
    model: str, provider: str, agent_id: str, agent_model: str, path: str,
    session_id: str | None = None, project: str | None = None, turn: int | None = None,
    det_trace_id_int: int | None = None, det_trace_id_raw: str | None = None,
    turn_count: int | None = None,
    parent_session_id: str | None = None, agent_type: str | None = None,
    turn_depth: int | None = None,
    traceparent: str | None = None,
) -> AsyncIterator[bytes]:
    tracer = get_tracer()
    _span_ctx = _context_for_trace_id(det_trace_id_int) if det_trace_id_int is not None else None
    span = tracer.start_span(f"{schema.SPAN_PREFIX_LLM}.{model}", context=_span_ctx)
    _set_request_attrs(span, model=model, provider=provider,
                       agent_id=agent_id, agent_model=agent_model,
                       path=path, body=body,
                       session_id=session_id, project=project, turn=turn,
                       det_trace_id_raw=det_trace_id_raw,
                       parent_session_id=parent_session_id,
                       agent_type=agent_type, turn_depth=turn_depth,
                       traceparent=traceparent)
    if turn_count is not None:
        span.set_attribute(schema.AGENT_TURN_COUNT, turn_count)

    input_tokens = output_tokens = 0
    cache_read = cache_write = 0  # Anthropic prompt-caching counters
    stop_reason = None
    start = time.perf_counter()

    try:
        async with httpx.AsyncClient(timeout=300) as client:
            async with client.stream(
                method, upstream_url, headers=headers, content=body_bytes,
            ) as resp:
                if resp.status_code >= 400:
                    logger.warning("← upstream error status=%d", resp.status_code)
                try:
                    async for raw_line in resp.aiter_lines():
                        line = raw_line.strip()
                        if not line:
                            yield b"\n"
                            continue
                        yield (line + "\n").encode()

                        if provider == "anthropic":
                            input_tokens, output_tokens, stop_reason = _parse_anthropic_sse(
                                line, input_tokens, output_tokens, stop_reason
                            )
                            cw, cr = _extract_anthropic_cache_tokens(line)
                            if cw > cache_write:
                                cache_write = cw
                            if cr > cache_read:
                                cache_read = cr
                        elif provider == "openai":
                            input_tokens, output_tokens, stop_reason = _parse_openai_sse(
                                line, input_tokens, output_tokens, stop_reason
                            )
                        else:  # google
                            input_tokens, output_tokens, stop_reason = _parse_google_stream(
                                line, input_tokens, output_tokens, stop_reason
                            )
                except httpx.RemoteProtocolError as exc:
                    logger.warning("upstream closed mid-stream: %s", exc)
                    span.set_attribute("agentweave.stream_error", str(exc))
                    stop_reason = stop_reason or "upstream_disconnect"

        elapsed_ms = int((time.perf_counter() - start) * 1000)
        span.set_attribute(schema.PROV_LLM_PROMPT_TOKENS, input_tokens)
        span.set_attribute(schema.PROV_LLM_COMPLETION_TOKENS, output_tokens)
        span.set_attribute(schema.PROV_LLM_TOTAL_TOKENS, input_tokens + output_tokens)
        if stop_reason:
            span.set_attribute(schema.PROV_LLM_STOP_REASON, stop_reason)
        span.set_attribute("agentweave.latency_ms", elapsed_ms)

        # Cache token breakdown — Anthropic-specific; emit zeros for other providers
        # so Grafana queries never encounter missing fields.
        span.set_attribute(schema.TOKENS_CACHE_READ, cache_read)
        span.set_attribute(schema.TOKENS_CACHE_WRITE, cache_write)
        # input_tokens for Anthropic streaming = raw + cache_write + cache_read
        hit_rate = (cache_read / input_tokens) if (provider == "anthropic" and input_tokens > 0) else 0.0
        span.set_attribute(schema.CACHE_HIT_RATE, hit_rate)

        # Cost tracking for streaming responses
        # Pass cache token breakdown so each bucket is priced at the correct rate
        # (cache_read is ~10x cheaper, cache_write slightly more than regular input).
        if input_tokens > 0 or output_tokens > 0:
            span.set_attribute(schema.COST_USD, compute_cost(
                model, input_tokens, output_tokens,
                cache_read_tokens=cache_read,
                cache_write_tokens=cache_write,
            ))

        # Warn when OpenAI streaming completes with no token usage data
        if provider == "openai" and input_tokens == 0 and output_tokens == 0:
            logger.warning(
                "OpenAI streaming response completed with 0 tokens. "
                'Add stream_options={"include_usage": true} to your request '
                "to enable token tracking."
            )

        # OTel gen_ai.* dual-emit for streaming responses
        span.set_attribute(schema.GEN_AI_USAGE_INPUT_TOKENS, input_tokens)
        span.set_attribute(schema.GEN_AI_USAGE_OUTPUT_TOKENS, output_tokens)
        if stop_reason:
            span.set_attribute(schema.GEN_AI_RESPONSE_FINISH_REASONS, [stop_reason])

        span.set_status(StatusCode.OK)

        # Record cost in budget tracker
        try:
            cost_usd = span.attributes.get(schema.COST_USD, 0.0) if hasattr(span, "attributes") else 0.0
            if cost_usd > 0:
                _tracker = _get_budget_tracker()
                _tracker.record_cost(agent_id or "unknown", cost_usd, session_id=session_id, tracer=tracer)
        except Exception:
            pass

    except Exception as exc:
        span.set_status(StatusCode.ERROR, str(exc))
        span.record_exception(exc)
        raise
    finally:
        span.end()
        try:
            _provider.force_flush(timeout_millis=2000)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Provider-specific token parsers
# ---------------------------------------------------------------------------

def _parse_anthropic_sse(
    line: str, input_tokens: int, output_tokens: int, stop_reason: str | None
) -> tuple[int, int, str | None]:
    if not line.startswith("data: "):
        return input_tokens, output_tokens, stop_reason
    payload = line[6:]
    if payload == "[DONE]":
        return input_tokens, output_tokens, stop_reason
    try:
        event = json.loads(payload)
        etype = event.get("type", "")
        if etype == "message_start":
            usage = event.get("message", {}).get("usage", {})
            # Sum all input token categories — OpenClaw uses prompt caching so
            # the bulk of tokens are in cache_read_input_tokens /
            # cache_creation_input_tokens, not bare input_tokens.
            total_input = (
                usage.get("input_tokens", 0)
                + usage.get("cache_creation_input_tokens", 0)
                + usage.get("cache_read_input_tokens", 0)
            )
            input_tokens = total_input if total_input > 0 else input_tokens
        elif etype == "message_delta":
            usage = event.get("usage", {})
            output_tokens = usage.get("output_tokens", output_tokens)
            stop_reason = event.get("delta", {}).get("stop_reason") or stop_reason
    except (json.JSONDecodeError, KeyError):
        pass
    return input_tokens, output_tokens, stop_reason


def _extract_anthropic_cache_tokens(line: str) -> tuple[int, int]:
    """Extract ``(cache_write, cache_read)`` from an Anthropic SSE ``message_start`` line.

    Returns ``(cache_creation_input_tokens, cache_read_input_tokens)`` or ``(0, 0)``
    when the line is not a message_start event or the fields are absent.
    This is Anthropic-specific; callers should gate on ``provider == "anthropic"``.
    """
    if not line.startswith("data: "):
        return 0, 0
    payload = line[6:]
    if payload == "[DONE]":
        return 0, 0
    try:
        event = json.loads(payload)
        if event.get("type") == "message_start":
            usage = event.get("message", {}).get("usage", {})
            return (
                usage.get("cache_creation_input_tokens", 0),
                usage.get("cache_read_input_tokens", 0),
            )
    except (json.JSONDecodeError, KeyError):
        pass
    return 0, 0


def _parse_google_stream(
    line: str, input_tokens: int, output_tokens: int, stop_reason: str | None
) -> tuple[int, int, str | None]:
    # Google streaming: SSE "data: {...}" or bare JSON lines
    payload = line[6:] if line.startswith("data: ") else line
    if not payload or payload == "[DONE]":
        return input_tokens, output_tokens, stop_reason
    try:
        chunk = json.loads(payload)
        usage = chunk.get("usageMetadata", {})
        if usage:
            input_tokens = usage.get("promptTokenCount", input_tokens)
            output_tokens = usage.get("candidatesTokenCount", output_tokens)
        candidates = chunk.get("candidates", [])
        if candidates:
            reason = candidates[0].get("finishReason")
            if reason and reason != "FINISH_REASON_UNSPECIFIED":
                stop_reason = reason
    except (json.JSONDecodeError, KeyError, IndexError):
        pass
    return input_tokens, output_tokens, stop_reason


def _parse_openai_sse(
    line: str, input_tokens: int, output_tokens: int, stop_reason: str | None
) -> tuple[int, int, str | None]:
    if not line.startswith("data: "):
        return input_tokens, output_tokens, stop_reason
    payload = line[6:]
    if payload == "[DONE]":
        return input_tokens, output_tokens, stop_reason
    try:
        chunk = json.loads(payload)
        # Token usage from final chunk (when stream_options.include_usage=true)
        usage = chunk.get("usage")
        if usage:
            # Chat completions: prompt_tokens/completion_tokens
            # Responses API:    input_tokens/output_tokens
            input_tokens = usage.get("prompt_tokens") or usage.get("input_tokens", input_tokens)
            output_tokens = usage.get("completion_tokens") or usage.get("output_tokens", output_tokens)
        choices = chunk.get("choices", [])
        if choices:
            reason = choices[0].get("finish_reason")
            if reason:
                stop_reason = reason
    except (json.JSONDecodeError, KeyError, IndexError):
        pass
    return input_tokens, output_tokens, stop_reason


# ---------------------------------------------------------------------------
# Response attribute extractors
# ---------------------------------------------------------------------------

def _extract_and_set_response(
    span: Any, data: dict, provider: str, elapsed_ms: int, model: str = ""
) -> None:
    if provider == "google":
        _set_google_response_attrs(span, data, elapsed_ms, model=model)
    elif provider == "openai":
        _set_openai_response_attrs(span, data, elapsed_ms, model=model)
    else:
        _set_anthropic_response_attrs(span, data, elapsed_ms, model=model)


def _set_anthropic_response_attrs(span: Any, data: dict, elapsed_ms: int, model: str = "") -> None:
    usage = data.get("usage", {})
    raw_input = usage.get("input_tokens", 0)
    cache_write = usage.get("cache_creation_input_tokens", 0)
    cache_read = usage.get("cache_read_input_tokens", 0)
    # Sum all input token categories to account for prompt caching
    pt = raw_input + cache_write + cache_read
    ct = usage.get("output_tokens", 0)
    span.set_attribute(schema.PROV_LLM_PROMPT_TOKENS, pt)
    span.set_attribute(schema.PROV_LLM_COMPLETION_TOKENS, ct)
    span.set_attribute(schema.PROV_LLM_TOTAL_TOKENS, pt + ct)
    stop = data.get("stop_reason")
    if stop:
        span.set_attribute(schema.PROV_LLM_STOP_REASON, stop)
    span.set_attribute("agentweave.latency_ms", elapsed_ms)
    _maybe_set_response_preview(span, _anthropic_response_text(data))

    # Cache token breakdown (Anthropic-specific)
    span.set_attribute(schema.TOKENS_CACHE_READ, cache_read)
    span.set_attribute(schema.TOKENS_CACHE_WRITE, cache_write)
    denominator = raw_input + cache_write + cache_read
    hit_rate = (cache_read / denominator) if denominator > 0 else 0.0
    span.set_attribute(schema.CACHE_HIT_RATE, hit_rate)

    # Cost tracking — pass cache breakdown so each bucket is priced correctly
    # (cache_read ~10x cheaper, cache_write slightly above regular input rate).
    if model and (pt > 0 or ct > 0):
        span.set_attribute(schema.COST_USD, compute_cost(
            model, pt, ct,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
        ))

    # OTel gen_ai.* dual-emit
    span.set_attribute(schema.GEN_AI_USAGE_INPUT_TOKENS, pt)
    span.set_attribute(schema.GEN_AI_USAGE_OUTPUT_TOKENS, ct)
    if stop:
        span.set_attribute(schema.GEN_AI_RESPONSE_FINISH_REASONS, [stop])


def _set_google_response_attrs(span: Any, data: dict, elapsed_ms: int, model: str = "") -> None:
    usage = data.get("usageMetadata", {})
    pt = usage.get("promptTokenCount", 0)
    ct = usage.get("candidatesTokenCount", 0)
    span.set_attribute(schema.PROV_LLM_PROMPT_TOKENS, pt)
    span.set_attribute(schema.PROV_LLM_COMPLETION_TOKENS, ct)
    span.set_attribute(schema.PROV_LLM_TOTAL_TOKENS, usage.get("totalTokenCount", pt + ct))
    candidates = data.get("candidates", [])
    stop = None
    if candidates:
        stop = candidates[0].get("finishReason")
        if stop:
            span.set_attribute(schema.PROV_LLM_STOP_REASON, stop)
    span.set_attribute("agentweave.latency_ms", elapsed_ms)
    _maybe_set_response_preview(span, _google_response_text(data))

    # Cache tokens not applicable for Google — emit zeros so Grafana queries don't break
    span.set_attribute(schema.TOKENS_CACHE_READ, 0)
    span.set_attribute(schema.TOKENS_CACHE_WRITE, 0)
    span.set_attribute(schema.CACHE_HIT_RATE, 0.0)

    # Cost tracking
    if model and (pt > 0 or ct > 0):
        span.set_attribute(schema.COST_USD, compute_cost(model, pt, ct))

    # OTel gen_ai.* dual-emit
    span.set_attribute(schema.GEN_AI_USAGE_INPUT_TOKENS, pt)
    span.set_attribute(schema.GEN_AI_USAGE_OUTPUT_TOKENS, ct)
    if stop:
        span.set_attribute(schema.GEN_AI_RESPONSE_FINISH_REASONS, [stop])


def _anthropic_response_text(data: dict) -> str:
    content = data.get("content", [])
    if content and isinstance(content, list):
        return content[0].get("text", "") if isinstance(content[0], dict) else ""
    return ""


def _set_openai_response_attrs(span: Any, data: dict, elapsed_ms: int, model: str = "") -> None:
    usage = data.get("usage", {})
    # Chat completions: prompt_tokens/completion_tokens
    # Responses API:    input_tokens/output_tokens
    pt = usage.get("prompt_tokens") or usage.get("input_tokens", 0)
    ct = usage.get("completion_tokens") or usage.get("output_tokens", 0)
    tt = usage.get("total_tokens", pt + ct)
    span.set_attribute(schema.PROV_LLM_PROMPT_TOKENS, pt)
    span.set_attribute(schema.PROV_LLM_COMPLETION_TOKENS, ct)
    span.set_attribute(schema.PROV_LLM_TOTAL_TOKENS, tt)
    choices = data.get("choices", [])
    stop = None
    if choices:
        stop = choices[0].get("finish_reason")
        if stop:
            span.set_attribute(schema.PROV_LLM_STOP_REASON, stop)
    span.set_attribute("agentweave.latency_ms", elapsed_ms)
    _maybe_set_response_preview(span, _openai_response_text(data))

    # Cache tokens not applicable for OpenAI — emit zeros so Grafana queries don't break
    span.set_attribute(schema.TOKENS_CACHE_READ, 0)
    span.set_attribute(schema.TOKENS_CACHE_WRITE, 0)
    span.set_attribute(schema.CACHE_HIT_RATE, 0.0)

    # Cost tracking
    if model and (pt > 0 or ct > 0):
        span.set_attribute(schema.COST_USD, compute_cost(model, pt, ct))

    # OTel gen_ai.* dual-emit
    span.set_attribute(schema.GEN_AI_USAGE_INPUT_TOKENS, pt)
    span.set_attribute(schema.GEN_AI_USAGE_OUTPUT_TOKENS, ct)
    if stop:
        span.set_attribute(schema.GEN_AI_RESPONSE_FINISH_REASONS, [stop])


def _google_response_text(data: dict) -> str:
    try:
        return (
            data["candidates"][0]["content"]["parts"][0].get("text", "")
        )
    except (KeyError, IndexError, TypeError):
        return ""


def _openai_response_text(data: dict) -> str:
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return ""


def _set_pii_attrs(span: Any, matches: list) -> None:
    """Attach PII detection attributes to *span*.

    Sets:
      - ``prov.security.pii_detected`` = "true"
      - ``prov.security.pii_kinds``    = comma-separated unique kinds, e.g. "EMAIL,PHONE"
      - ``prov.security.pii_mode``     = active mode, e.g. "flag" | "redact"
    """
    span.set_attribute(schema.SECURITY_PII_DETECTED, "true")
    kinds = sorted({m.kind for m in matches})
    span.set_attribute(schema.SECURITY_PII_KINDS, ",".join(kinds))
    span.set_attribute(schema.SECURITY_PII_MODE, PIIMode.from_env())


def _maybe_set_response_preview(span: Any, text: str) -> None:
    pii_mode = PIIMode.from_env()
    # Cap input to PII scanner to avoid scanning unbounded LLM responses
    scan_text_cap = text[:4096] if text else ""
    if pii_mode != PIIMode.OFF and scan_text_cap:
        try:
            result = _pii_scan(scan_text_cap, mode=pii_mode)
            if result.is_detected:
                _set_pii_attrs(span, result.matches)
            scan_text_cap = result.cleaned  # may be redacted
        except PIIBlockedError:
            raise  # let upstream handler deal with it
        except Exception:
            logger.debug("PII scan error in response preview", exc_info=True)

    _capture_prompts = os.getenv("AGENTWEAVE_CAPTURE_PROMPTS", "").lower() in ("1", "true", "yes")
    preview = scan_text_cap if pii_mode != PIIMode.OFF else text
    if _capture_prompts and preview:
        span.set_attribute(schema.PROV_LLM_RESPONSE_PREVIEW, preview[:512])


# ---------------------------------------------------------------------------
# Request attributes
# ---------------------------------------------------------------------------

def _set_request_attrs(
    span: Any, model: str, provider: str, agent_id: str, agent_model: str,
    path: str, body: dict,
    session_id: str | None = None, project: str | None = None, turn: int | None = None,
    det_trace_id_raw: str | None = None,
    parent_session_id: str | None = None, agent_type: str | None = None,
    turn_depth: int | None = None,
    traceparent: str | None = None,
) -> None:
    span.set_attribute(schema.PROV_ACTIVITY_TYPE, schema.ACTIVITY_LLM_CALL)
    span.set_attribute(schema.PROV_LLM_PROVIDER, provider)
    span.set_attribute(schema.PROV_LLM_MODEL, model)
    span.set_attribute(schema.PROV_AGENT_ID, agent_id)
    span.set_attribute(schema.PROV_AGENT_MODEL, agent_model)
    span.set_attribute(schema.PROV_WAS_ASSOCIATED_WITH, agent_id)
    span.set_attribute("http.route", f"/{path}")

    if session_id is not None:
        span.set_attribute(schema.SESSION_ID, session_id)
        span.set_attribute(schema.PROV_SESSION_ID, session_id)
    if project is not None:
        span.set_attribute(schema.PROV_PROJECT, project)
    if turn is not None:
        span.set_attribute(schema.PROV_SESSION_TURN, turn)
    if det_trace_id_raw is not None:
        span.set_attribute(schema.AGENTWEAVE_TRACE_ID, det_trace_id_raw)

    # Sub-agent attribution (issue #15)
    if parent_session_id is not None:
        span.set_attribute(schema.PROV_PARENT_SESSION_ID, parent_session_id)
    if agent_type is not None:
        span.set_attribute(schema.PROV_AGENT_TYPE, agent_type)
    if turn_depth is not None:
        span.set_attribute(schema.PROV_SESSION_TURN, turn_depth)

    # W3C traceparent passthrough (issue #44)
    if traceparent is not None:
        span.set_attribute(schema.PROV_TRACE_PARENT, traceparent)

    # OTel gen_ai.* dual-emit
    span.set_attribute(schema.GEN_AI_OPERATION_NAME, schema.GEN_AI_OP_CHAT)
    span.set_attribute(schema.GEN_AI_SYSTEM, provider)
    span.set_attribute(schema.GEN_AI_REQUEST_MODEL, agent_model)
    span.set_attribute(schema.GEN_AI_AGENT_NAME, agent_id)

    # Apply global session context (env-var defaults) — but don't overwrite
    # per-request values that were already set explicitly above.
    _explicit_session_attrs: set[str] = set()
    if session_id is not None:
        _explicit_session_attrs.update({schema.SESSION_ID, schema.PROV_SESSION_ID})
    if project is not None:
        _explicit_session_attrs.add(schema.PROV_PROJECT)
    if turn is not None:
        _explicit_session_attrs.add(schema.PROV_SESSION_TURN)
    if parent_session_id is not None:
        _explicit_session_attrs.add(schema.PROV_PARENT_SESSION_ID)
    if agent_type is not None:
        _explicit_session_attrs.add(schema.PROV_AGENT_TYPE)
    for k, v in _session_context.items():
        if k not in _explicit_session_attrs:
            span.set_attribute(k, v)

    # Only fall back to cfg.agent_id if no per-request agent_id was provided via header
    if not agent_id:
        cfg = AgentWeaveConfig.get_or_none()
        if cfg and cfg.agent_id:
            span.set_attribute(schema.PROV_AGENT_ID, cfg.agent_id)

    # PII scanning on prompt (runs regardless of AGENTWEAVE_CAPTURE_PROMPTS)
    pii_mode = PIIMode.from_env()
    _capture_prompts = os.getenv("AGENTWEAVE_CAPTURE_PROMPTS", "").lower() in ("1", "true", "yes")
    if pii_mode == PIIMode.OFF and not _capture_prompts:
        return
    try:
        if provider == "google":
            parts = body.get("contents", [{}])[-1].get("parts", [{}])
            preview = parts[0].get("text", "")[:512]
        else:
            messages = body.get("messages", [])
            first = messages[0] if messages else {}
            content = first.get("content", "")
            if isinstance(content, list):
                content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
            preview = str(content)[:512]

        if pii_mode != PIIMode.OFF and preview:
            result = _pii_scan(preview, mode=pii_mode)
            if result.is_detected:
                _set_pii_attrs(span, result.matches)
            preview = result.cleaned  # may be redacted

        if _capture_prompts and preview:
            span.set_attribute(schema.PROV_LLM_PROMPT_PREVIEW, preview)
    except PIIBlockedError:
        raise  # propagate so the route handler can return 400
    except Exception:
        logger.debug("PII scan error in request attrs", exc_info=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _config_value(field: str) -> str | None:
    try:
        cfg = AgentWeaveConfig.get_or_none()
        return getattr(cfg, field, None) if cfg else None
    except Exception:
        return None


def run(host: str = "0.0.0.0", port: int = 4000) -> None:
    """Start the proxy server (called from CLI)."""
    import uvicorn
    logger.info(f"AgentWeave proxy listening on {host}:{port}")
    logger.info("Providers: anthropic (/v1/messages), google (/v1beta/models/...), openai (/v1/chat/completions)")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    run()
