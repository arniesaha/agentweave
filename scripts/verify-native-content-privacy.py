#!/usr/bin/env python3
"""Probe collector-side OpenClaw content stripping with harmless OTLP spans."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
import uuid


OTLP_ENDPOINT = os.environ.get("AGENTWEAVE_OTLP_ENDPOINT", "http://10.43.221.47:4318").rstrip("/")
TEMPO_URL = os.environ.get("AGENTWEAVE_TEMPO_URL", "http://192.168.1.70:31989").rstrip("/")
FORBIDDEN = {
    "gen_ai.input.messages",
    "gen_ai.output.messages",
    "gen_ai.tool.definitions",
    "gen_ai.tool.call.arguments",
    "gen_ai.tool.call.result",
    "openclaw.content.input_messages",
    "openclaw.content.output_messages",
    "openclaw.content.tool_input",
    "openclaw.content.tool_output",
    "input.value",
    "output.value",
}
RETAINED = {"openclaw.model", "gen_ai.usage.input_tokens"}


def attribute(key: str, value: str) -> dict:
    return {"key": key, "value": {"stringValue": value}}


def resource_spans(service: str, span_name: str, trace_id: str, now_ns: int) -> dict:
    attributes = [attribute(key, "synthetic-marker") for key in sorted(FORBIDDEN)]
    attributes.extend(
        [
            attribute("openclaw.model", "synthetic-model"),
            {"key": "gen_ai.usage.input_tokens", "value": {"intValue": "7"}},
        ]
    )
    return {
        "resource": {"attributes": [attribute("service.name", service)]},
        "scopeSpans": [
            {
                "scope": {"name": "agentweave-privacy-probe"},
                "spans": [
                    {
                        "traceId": trace_id,
                        "spanId": uuid.uuid4().hex[:16],
                        "name": span_name,
                        "kind": 1,
                        "startTimeUnixNano": str(now_ns),
                        "endTimeUnixNano": str(now_ns + 1_000_000),
                        "attributes": attributes,
                    }
                ],
            }
        ],
    }


def fetch_span_keys(trace_id: str) -> dict[str, set[str]]:
    url = f"{TEMPO_URL}/api/traces/{trace_id}"
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=15) as response:
                trace = json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
            time.sleep(1)
            continue
        except TimeoutError:
            time.sleep(1)
            continue
        spans = {
            span["name"]: {attr["key"] for attr in span.get("attributes", [])}
            for batch in trace.get("batches", [])
            for scoped in batch.get("scopeSpans", [])
            for span in scoped.get("spans", [])
        }
        if {"privacy-probe.openclaw", "privacy-probe.other"} <= spans.keys():
            return spans
        time.sleep(1)
    raise RuntimeError("synthetic spans did not appear together in Tempo within 60 seconds")


def main() -> None:
    trace_id = uuid.uuid4().hex
    now_ns = time.time_ns()
    payload = {
        "resourceSpans": [
            resource_spans("openclaw", "privacy-probe.openclaw", trace_id, now_ns),
            resource_spans("privacy-probe", "privacy-probe.other", trace_id, now_ns),
        ]
    }
    request = urllib.request.Request(
        f"{OTLP_ENDPOINT}/v1/traces",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10):
        pass

    spans = fetch_span_keys(trace_id)
    native = spans["privacy-probe.openclaw"]
    other = spans["privacy-probe.other"]
    leaked = FORBIDDEN & native
    if leaked:
        raise AssertionError(f"native content attributes reached Tempo: {sorted(leaked)}")
    missing = RETAINED - native
    if missing:
        raise AssertionError(f"native model/usage metadata was stripped: {sorted(missing)}")
    changed_other = FORBIDDEN - other
    if changed_other:
        raise AssertionError(f"unrelated service lost attributes: {sorted(changed_other)}")
    print(f"PASS native content stripped; model/usage retained; other service unchanged; synthetic trace {trace_id}")


if __name__ == "__main__":
    main()
