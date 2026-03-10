"""AgentWeave Anthropic API Proxy.

Intercepts requests to the Anthropic API, emits an OTel span per call with
token counts, model, stop reason, and optional prompt/response previews, then
forwards the response transparently to the caller.

Works for both streaming and non-streaming requests. Any client that supports
``ANTHROPIC_BASE_URL`` (Anthropic Python SDK, Claude Code, OpenClaw, etc.) can
be pointed at this proxy with zero code changes.

Usage::

    # Start the proxy
    agentweave proxy start --port 4000 --endpoint http://tempo-host:4318

    # Or run directly
    python -m agentweave.proxy

    # Then set in your agent/shell environment:
    export ANTHROPIC_BASE_URL=http://localhost:4000

    # Optional: tag which agent is calling (add header in your SDK wrapper)
    # X-AgentWeave-Agent-Id: nix-v1
    # X-AgentWeave-Agent-Model: claude-sonnet-4-6
"""

from __future__ import annotations

import json
import logging
import os
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

ANTHROPIC_BASE = "https://api.anthropic.com"

# Headers forwarded from client → Anthropic (drop hop-by-hop + host)
_SKIP_REQUEST_HEADERS = {
    "host", "content-length", "transfer-encoding", "connection",
    "x-agentweave-agent-id", "x-agentweave-agent-model",
}

app = FastAPI(
    title="AgentWeave Proxy",
    description="Transparent Anthropic API proxy with OTel tracing",
    version="0.1.0",
)


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"], response_model=None)
async def proxy(path: str, request: Request) -> StreamingResponse | JSONResponse:
    body_bytes = await request.body()
    body: dict[str, Any] = {}
    if body_bytes:
        try:
            body = json.loads(body_bytes)
        except json.JSONDecodeError:
            pass

    model = body.get("model", "unknown")
    is_stream = body.get("stream", False)

    # Agent identity — from custom headers or global config
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

    # Forward headers — preserve anthropic-version, auth, etc.
    forward_headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in _SKIP_REQUEST_HEADERS
    }

    upstream_url = f"{ANTHROPIC_BASE}/{path}"

    if is_stream:
        return StreamingResponse(
            _stream_and_trace(
                upstream_url=upstream_url,
                method=request.method,
                headers=forward_headers,
                body=body,
                model=model,
                agent_id=agent_id,
                agent_model=agent_model,
                path=path,
            ),
            media_type="text/event-stream",
        )
    else:
        return await _request_and_trace(
            upstream_url=upstream_url,
            method=request.method,
            headers=forward_headers,
            body=body,
            model=model,
            agent_id=agent_id,
            agent_model=agent_model,
            path=path,
        )


async def _request_and_trace(
    upstream_url: str,
    method: str,
    headers: dict,
    body: dict,
    model: str,
    agent_id: str,
    agent_model: str,
    path: str,
) -> JSONResponse:
    """Handle non-streaming request: forward, parse response, emit span."""
    tracer = get_tracer()
    span_name = f"{schema.SPAN_PREFIX_LLM}.{model}"

    with tracer.start_as_current_span(span_name) as span:
        _set_request_attrs(span, model=model, agent_id=agent_id,
                           agent_model=agent_model, path=path, body=body)
        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.request(
                    method, upstream_url, headers=headers,
                    content=json.dumps(body).encode(),
                )
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            data = resp.json()
            _set_response_attrs(span, data=data, elapsed_ms=elapsed_ms)
            span.set_status(StatusCode.OK)
            return JSONResponse(content=data, status_code=resp.status_code)
        except Exception as exc:
            span.set_status(StatusCode.ERROR, str(exc))
            span.record_exception(exc)
            raise


async def _stream_and_trace(
    upstream_url: str,
    method: str,
    headers: dict,
    body: dict,
    model: str,
    agent_id: str,
    agent_model: str,
    path: str,
) -> AsyncIterator[bytes]:
    """Handle streaming SSE request: forward events, emit span on completion."""
    tracer = get_tracer()
    span_name = f"{schema.SPAN_PREFIX_LLM}.{model}"
    span = tracer.start_span(span_name)
    _set_request_attrs(span, model=model, agent_id=agent_id,
                       agent_model=agent_model, path=path, body=body)

    input_tokens = 0
    output_tokens = 0
    stop_reason = None
    start = time.perf_counter()

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream(
                method, upstream_url,
                headers=headers,
                content=json.dumps(body).encode(),
            ) as resp:
                async for raw_line in resp.aiter_lines():
                    line = raw_line.strip()
                    if not line:
                        yield b"\n"
                        continue

                    yield (line + "\n").encode()

                    # Parse SSE events for token accounting
                    if line.startswith("data: "):
                        payload = line[6:]
                        if payload == "[DONE]":
                            continue
                        try:
                            event = json.loads(payload)
                            etype = event.get("type", "")

                            if etype == "message_start":
                                usage = event.get("message", {}).get("usage", {})
                                input_tokens = usage.get("input_tokens", 0)

                            elif etype == "message_delta":
                                usage = event.get("usage", {})
                                output_tokens = usage.get("output_tokens", 0)
                                stop_reason = event.get("delta", {}).get("stop_reason")

                        except (json.JSONDecodeError, KeyError):
                            pass

        elapsed_ms = int((time.perf_counter() - start) * 1000)
        _set_stream_end_attrs(
            span,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            stop_reason=stop_reason,
            elapsed_ms=elapsed_ms,
        )
        span.set_status(StatusCode.OK)

    except Exception as exc:
        span.set_status(StatusCode.ERROR, str(exc))
        span.record_exception(exc)
        raise
    finally:
        span.end()
        # Flush so traces aren't lost on long-idle connections
        try:
            _provider.force_flush(timeout_millis=2000)
        except Exception:
            pass


# --- Attribute helpers ---

def _set_request_attrs(
    span: Any,
    model: str,
    agent_id: str,
    agent_model: str,
    path: str,
    body: dict,
) -> None:
    span.set_attribute(schema.PROV_ACTIVITY_TYPE, schema.ACTIVITY_LLM_CALL)
    span.set_attribute(schema.PROV_LLM_PROVIDER, "anthropic")
    span.set_attribute(schema.PROV_LLM_MODEL, model)
    span.set_attribute(schema.PROV_AGENT_ID, agent_id)
    span.set_attribute(schema.PROV_AGENT_MODEL, agent_model)
    span.set_attribute(schema.PROV_WAS_ASSOCIATED_WITH, agent_id)
    span.set_attribute("http.route", f"/{path}")

    # Prompt preview from first user message (opt-in via env var)
    if os.getenv("AGENTWEAVE_CAPTURE_PROMPTS", "").lower() in ("1", "true", "yes"):
        messages = body.get("messages", [])
        if messages:
            first = messages[0]
            content = first.get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    c.get("text", "") for c in content if isinstance(c, dict)
                )
            span.set_attribute(schema.PROV_LLM_PROMPT_PREVIEW, str(content)[:512])


def _set_response_attrs(span: Any, data: dict, elapsed_ms: int) -> None:
    usage = data.get("usage", {})
    pt = usage.get("input_tokens", 0)
    ct = usage.get("output_tokens", 0)
    span.set_attribute(schema.PROV_LLM_PROMPT_TOKENS, pt)
    span.set_attribute(schema.PROV_LLM_COMPLETION_TOKENS, ct)
    span.set_attribute(schema.PROV_LLM_TOTAL_TOKENS, pt + ct)

    stop = data.get("stop_reason")
    if stop:
        span.set_attribute(schema.PROV_LLM_STOP_REASON, stop)

    span.set_attribute("agentweave.latency_ms", elapsed_ms)

    if os.getenv("AGENTWEAVE_CAPTURE_PROMPTS", "").lower() in ("1", "true", "yes"):
        content = data.get("content", [])
        if content and isinstance(content, list):
            text = content[0].get("text", "") if isinstance(content[0], dict) else ""
            span.set_attribute(schema.PROV_LLM_RESPONSE_PREVIEW, str(text)[:512])


def _set_stream_end_attrs(
    span: Any,
    input_tokens: int,
    output_tokens: int,
    stop_reason: str | None,
    elapsed_ms: int,
) -> None:
    span.set_attribute(schema.PROV_LLM_PROMPT_TOKENS, input_tokens)
    span.set_attribute(schema.PROV_LLM_COMPLETION_TOKENS, output_tokens)
    span.set_attribute(schema.PROV_LLM_TOTAL_TOKENS, input_tokens + output_tokens)
    if stop_reason:
        span.set_attribute(schema.PROV_LLM_STOP_REASON, stop_reason)
    span.set_attribute("agentweave.latency_ms", elapsed_ms)


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
    logger.info(f"Set ANTHROPIC_BASE_URL=http://<your-host>:{port} in your agents")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    run()
