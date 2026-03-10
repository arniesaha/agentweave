"""AgentWeave Multi-Provider AI Proxy.

Intercepts requests to Anthropic and Google Gemini APIs, emits an OTel span
per call with token counts, model, stop reason, and latency, then forwards
the response transparently to the caller.

Provider is detected automatically from the request path:
  /v1/messages              → Anthropic  (api.anthropic.com)
  /v1beta/models/...        → Google     (generativelanguage.googleapis.com)
  /v1/models/...            → Google     (generativelanguage.googleapis.com)

Works for both streaming and non-streaming requests. Zero code changes needed
in calling agents — just point ANTHROPIC_BASE_URL / GOOGLE_GENAI_BASE_URL at
this proxy.

Usage::

    agentweave proxy start --port 4000 --endpoint http://tempo-host:4318

    # Anthropic agents
    export ANTHROPIC_BASE_URL=http://localhost:4000

    # Google / Gemini agents (pi-mono / Max)
    export GOOGLE_GENAI_BASE_URL=http://localhost:4000
    # or set in Google SDK: genai.configure(client_options={"api_endpoint": "localhost:4000"})

    # Tag calls by agent
    # X-AgentWeave-Agent-Id: max-v1
"""

from __future__ import annotations

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
from opentelemetry.trace import StatusCode

from agentweave import schema
from agentweave.config import AgentWeaveConfig
from agentweave.exporter import get_tracer, _provider

logger = logging.getLogger("agentweave.proxy")

# --- Upstream base URLs ---
_ANTHROPIC_BASE = "https://api.anthropic.com"
_GOOGLE_BASE = "https://generativelanguage.googleapis.com"

# Headers always stripped before forwarding (hop-by-hop + proxy-specific)
_SKIP_HEADERS_ALWAYS = {
    "host", "content-length", "transfer-encoding", "connection",
    "x-agentweave-agent-id",
    "x-agentweave-agent-model",
}

# Runtime auth token. Set AGENTWEAVE_PROXY_TOKEN or --auth-token.
# Empty = open mode (dev/localhost only).
_PROXY_TOKEN: str | None = os.getenv("AGENTWEAVE_PROXY_TOKEN") or None

# Gemini model name from URL, e.g. /v1beta/models/gemini-2.5-pro:generateContent
_GEMINI_MODEL_RE = re.compile(r"/models/([^/:]+)")

app = FastAPI(
    title="AgentWeave Proxy",
    description="Multi-provider AI observability proxy (Anthropic + Google Gemini)",
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

def _detect_provider(path: str) -> str:
    """Return 'google' or 'anthropic' based on the request path."""
    if path.startswith("v1beta/") or (
        path.startswith("v1/") and "/models/" in path
    ):
        return "google"
    return "anthropic"


def _upstream_url(provider: str, path: str, query_string: str) -> str:
    base = _GOOGLE_BASE if provider == "google" else _ANTHROPIC_BASE
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
    )

    if is_stream:
        media = "text/event-stream" if provider == "anthropic" else "application/json"
        return StreamingResponse(_stream_and_trace(**kwargs), media_type=media)
    return await _request_and_trace(**kwargs)


# ---------------------------------------------------------------------------
# Non-streaming handler
# ---------------------------------------------------------------------------

async def _request_and_trace(
    upstream_url: str, method: str, headers: dict, body: dict, body_bytes: bytes,
    model: str, provider: str, agent_id: str, agent_model: str, path: str,
) -> JSONResponse:
    tracer = get_tracer()
    with tracer.start_as_current_span(f"{schema.SPAN_PREFIX_LLM}.{model}") as span:
        _set_request_attrs(span, model=model, provider=provider,
                           agent_id=agent_id, agent_model=agent_model,
                           path=path, body=body)
        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=300) as client:
                resp = await client.request(
                    method, upstream_url, headers=headers, content=body_bytes,
                )
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            data = resp.json()
            _extract_and_set_response(span, data=data, provider=provider, elapsed_ms=elapsed_ms)
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
) -> AsyncIterator[bytes]:
    tracer = get_tracer()
    span = tracer.start_span(f"{schema.SPAN_PREFIX_LLM}.{model}")
    _set_request_attrs(span, model=model, provider=provider,
                       agent_id=agent_id, agent_model=agent_model,
                       path=path, body=body)

    input_tokens = output_tokens = 0
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


# ---------------------------------------------------------------------------
# Response attribute extractors
# ---------------------------------------------------------------------------

def _extract_and_set_response(
    span: Any, data: dict, provider: str, elapsed_ms: int
) -> None:
    if provider == "google":
        _set_google_response_attrs(span, data, elapsed_ms)
    else:
        _set_anthropic_response_attrs(span, data, elapsed_ms)


def _set_anthropic_response_attrs(span: Any, data: dict, elapsed_ms: int) -> None:
    usage = data.get("usage", {})
    # Sum all input token categories to account for prompt caching
    pt = (
        usage.get("input_tokens", 0)
        + usage.get("cache_creation_input_tokens", 0)
        + usage.get("cache_read_input_tokens", 0)
    )
    ct = usage.get("output_tokens", 0)
    span.set_attribute(schema.PROV_LLM_PROMPT_TOKENS, pt)
    span.set_attribute(schema.PROV_LLM_COMPLETION_TOKENS, ct)
    span.set_attribute(schema.PROV_LLM_TOTAL_TOKENS, pt + ct)
    stop = data.get("stop_reason")
    if stop:
        span.set_attribute(schema.PROV_LLM_STOP_REASON, stop)
    span.set_attribute("agentweave.latency_ms", elapsed_ms)
    _maybe_set_response_preview(span, _anthropic_response_text(data))


def _set_google_response_attrs(span: Any, data: dict, elapsed_ms: int) -> None:
    usage = data.get("usageMetadata", {})
    pt = usage.get("promptTokenCount", 0)
    ct = usage.get("candidatesTokenCount", 0)
    span.set_attribute(schema.PROV_LLM_PROMPT_TOKENS, pt)
    span.set_attribute(schema.PROV_LLM_COMPLETION_TOKENS, ct)
    span.set_attribute(schema.PROV_LLM_TOTAL_TOKENS, usage.get("totalTokenCount", pt + ct))
    candidates = data.get("candidates", [])
    if candidates:
        stop = candidates[0].get("finishReason")
        if stop:
            span.set_attribute(schema.PROV_LLM_STOP_REASON, stop)
    span.set_attribute("agentweave.latency_ms", elapsed_ms)
    _maybe_set_response_preview(span, _google_response_text(data))


def _anthropic_response_text(data: dict) -> str:
    content = data.get("content", [])
    if content and isinstance(content, list):
        return content[0].get("text", "") if isinstance(content[0], dict) else ""
    return ""


def _google_response_text(data: dict) -> str:
    try:
        return (
            data["candidates"][0]["content"]["parts"][0].get("text", "")
        )
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
) -> None:
    span.set_attribute(schema.PROV_ACTIVITY_TYPE, schema.ACTIVITY_LLM_CALL)
    span.set_attribute(schema.PROV_LLM_PROVIDER, provider)
    span.set_attribute(schema.PROV_LLM_MODEL, model)
    span.set_attribute(schema.PROV_AGENT_ID, agent_id)
    span.set_attribute(schema.PROV_AGENT_MODEL, agent_model)
    span.set_attribute(schema.PROV_WAS_ASSOCIATED_WITH, agent_id)
    span.set_attribute("http.route", f"/{path}")

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
    logger.info("Providers: anthropic (/v1/messages), google (/v1beta/models/...)")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    run()
