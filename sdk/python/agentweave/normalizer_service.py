"""OTLP-JSON receiver that normalizes Claude Code spans and forwards them on.

Sits between Claude Code and the OTel collector:

    Claude Code --OTLP/JSON--> normalizer --OTLP--> collector --> Tempo

Deliberately a separate service rather than an endpoint on the proxy: the proxy
is on the request hot path, restarts frequently, and a normalization bug there
would present as a proxy bug. See the design doc for the full rationale —
docs/superpowers/specs/2026-08-07-native-otel-normalization-design.md

Configuration (environment):

    AGENTWEAVE_COLLECTOR_ENDPOINT  OTLP HTTP base URL of the collector
                                   (default http://10.43.221.47:4318)
    AGENTWEAVE_NORMALIZER_PORT     listen port (default 4318)

The collector keeps doing PII stripping downstream, so this service does not
duplicate that responsibility.
"""

from __future__ import annotations

import json
import logging
import os

import httpx
from fastapi import Request
from fastapi.applications import FastAPI
from fastapi.responses import JSONResponse

from agentweave.normalizer import normalize_payload

__version__ = "0.3.7"

logger = logging.getLogger("agentweave.normalizer")

COLLECTOR_ENDPOINT = os.getenv(
    "AGENTWEAVE_COLLECTOR_ENDPOINT", "http://10.43.221.47:4318"
).rstrip("/")

_FORWARD_TIMEOUT_SECONDS = 30

app = FastAPI(
    title="AgentWeave Normalizer",
    description="Normalizes Claude Code native OTel spans into the prov.* schema",
    version=__version__,
)


async def _forward(url: str, document: dict) -> int:
    """POST a normalized payload to the collector. Returns its status code."""
    async with httpx.AsyncClient(timeout=_FORWARD_TIMEOUT_SECONDS) as client:
        response = await client.post(
            url, json=document, headers={"content-type": "application/json"}
        )
        return response.status_code


@app.get("/health", include_in_schema=True)
async def health() -> dict:
    """Liveness probe. Reports the collector it forwards to, so a misconfigured
    endpoint is visible without exec'ing into the pod."""
    return {
        "status": "ok",
        "version": __version__,
        "collector_endpoint": COLLECTOR_ENDPOINT,
    }


@app.post("/v1/traces", include_in_schema=True, response_model=None)
async def ingest_traces(request: Request) -> JSONResponse:
    """Receive an OTLP-JSON trace export, normalize it, forward it on.

    Status codes matter here: the OTel exporter retries on 5xx and drops on
    2xx. Anything that means "the batch did not land" must be a 5xx, or spans
    are lost silently.
    """
    content_type = request.headers.get("content-type", "")
    if "protobuf" in content_type:
        # Configured for the wrong protocol — say so rather than failing to
        # parse binary as JSON and returning a confusing 400.
        return JSONResponse(
            {
                "error": "this endpoint accepts OTLP-JSON only; set "
                "OTEL_EXPORTER_OTLP_PROTOCOL=http/json"
            },
            status_code=415,
        )

    raw = await request.body()
    try:
        document = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        # Malformed input is the client's bug; a retry won't fix it, so 4xx.
        logger.warning("rejecting unparseable OTLP-JSON payload: %s", exc)
        return JSONResponse({"error": f"invalid JSON: {exc}"}, status_code=400)

    try:
        normalized = normalize_payload(document)
    except Exception:
        # Never drop a batch because normalization tripped: forward it
        # unchanged so the spans still reach Tempo, and make the bug loud.
        logger.exception("normalization failed; forwarding payload unchanged")
        normalized = document

    url = f"{COLLECTOR_ENDPOINT}/v1/traces"
    try:
        status = await _forward(url, normalized)
    except Exception as exc:
        logger.error("forward to %s failed: %s", url, exc)
        return JSONResponse(
            {"error": f"upstream collector unreachable: {exc}"}, status_code=502
        )

    if status >= 400:
        logger.error("collector rejected batch with %s", status)
        return JSONResponse(
            {"error": f"collector returned {status}"}, status_code=502
        )

    # OTLP/HTTP expects an ExportTraceServiceResponse. An empty object is the
    # valid "everything accepted" form; a bespoke body risks an exporter
    # warning or rejecting, which would be silent in a telemetry path.
    return JSONResponse({}, status_code=200)
