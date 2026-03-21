"""AgentWeave Multi-Provider AI Proxy.

Intercepts requests to Anthropic, Google Gemini, and OpenAI APIs, emits an
OTel span per call with token counts, model, stop reason, and latency, then
forwards the response transparently to the caller.

Provider is detected automatically from the request path:
  /v1/messages              → Anthropic  (api.anthropic.com)
  /v1beta/models/...        → Google     (generativelanguage.googleapis.com)
  /v1/models/...            → Google     (generativelanguage.googleapis.com)
  /v1/chat/completions      → OpenAI     (api.openai.com)
  /v1/completions           → OpenAI     (api.openai.com)
  /v1/embeddings            → OpenAI     (api.openai.com)
  /v1/responses             → OpenAI     (api.openai.com)  [Responses API]

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
from agentweave.exporter import get_tracer, _provider
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
_ANTHROPIC_INJECT_KEY: str | None = os.getenv("AGENTWEAVE_ANTHROPIC_API_KEY") or None
_GOOGLE_INJECT_KEY: str | None = os.getenv("AGENTWEAVE_GOOGLE_API_KEY") or None
_OPENAI_INJECT_KEY: str | None = os.getenv("AGENTWEAVE_OPENAI_API_KEY") or None

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
    return {"status": "ok", "version": app.version}


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
        or _config_value("agent_id")
        or "unknown"
    )
    agent_model = (
        request.headers.get("x-agentweave-agent-model")
        or _config_value("agent_model")
        or model
    )

    session_id = request.headers.get("x-agentweave-session-id")
    project = request.headers.get("x-agentweave-project")
    # Infer project from agent_id prefix when not explicitly set
    if not project and agent_id and agent_id != "unknown":
        prefix = agent_id.split("-")[0]  # nix-v1→nix, max-v1→max, claude-code-mac→claude
        if prefix:
            project = prefix
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
    traceparent: str | None = request.headers.get("traceparent")

    # Sub-agent attribution headers (issue #15)
    parent_session_id: str | None = request.headers.get("x-agentweave-parent-session-id")
    agent_type: str | None = request.headers.get("x-agentweave-agent-type")
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

    # Inject proxy-configured API keys, overriding whatever the caller sent
    # (including placeholder values like ANTHROPIC_API_KEY=dummy).
    if provider == "anthropic" and _ANTHROPIC_INJECT_KEY:
        if _ANTHROPIC_INJECT_KEY.startswith("sk-ant-oat"):
            # OAuth tokens must use Bearer auth + oauth beta header + ?beta=true query
            forward_headers["authorization"] = f"Bearer {_ANTHROPIC_INJECT_KEY}"
            forward_headers.pop("x-api-key", None)
            existing_beta = forward_headers.get("anthropic-beta", "")
            oauth_beta = "oauth-2025-04-20"
            claude_code_beta = "claude-code-20250219"
            betas_to_add = [b for b in [oauth_beta, claude_code_beta] if b not in existing_beta]
            if betas_to_add:
                new_beta = ",".join(filter(None, [existing_beta] + betas_to_add))
                forward_headers["anthropic-beta"] = new_beta
            # Append ?beta=true — required for OAuth tokens to access non-Haiku models
            if "beta=true" not in query_string:
                query_string = f"{query_string}&beta=true" if query_string else "beta=true"
        else:
            forward_headers["x-api-key"] = _ANTHROPIC_INJECT_KEY
    elif provider == "openai" and _OPENAI_INJECT_KEY:
        forward_headers["authorization"] = f"Bearer {_OPENAI_INJECT_KEY}"
    elif provider == "google" and _GOOGLE_INJECT_KEY:
        forward_headers["x-goog-api-key"] = _GOOGLE_INJECT_KEY
        # Also strip key from query string (Google also accepts ?key=...)
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

    if is_stream:
        media = "application/json" if provider == "google" else "text/event-stream"
        return StreamingResponse(_stream_and_trace(**kwargs), media_type=media)
    return await _request_and_trace(**kwargs)


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
            return JSONResponse(content=data, status_code=resp.status_code)
        except Exception as exc:
            span.set_status(StatusCode.ERROR, str(exc))
            span.record_exception(exc)
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


def _maybe_set_response_preview(span: Any, text: str) -> None:
    if os.getenv("AGENTWEAVE_CAPTURE_PROMPTS", "").lower() in ("1", "true", "yes") and text:
        span.set_attribute(schema.PROV_LLM_RESPONSE_PREVIEW, text[:512])


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

    # Apply global session context (env-var defaults overrideable via POST /session)
    for k, v in _session_context.items():
        span.set_attribute(k, v)

    # Only fall back to cfg.agent_id if no per-request agent_id was provided via header
    if not agent_id:
        cfg = AgentWeaveConfig.get_or_none()
        if cfg and cfg.agent_id:
            span.set_attribute(schema.PROV_AGENT_ID, cfg.agent_id)

    if os.getenv("AGENTWEAVE_CAPTURE_PROMPTS", "").lower() not in ("1", "true", "yes"):
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
        if preview:
            span.set_attribute(schema.PROV_LLM_PROMPT_PREVIEW, preview)
    except Exception:
        pass


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
