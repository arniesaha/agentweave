#!/usr/bin/env python3
"""Probe the native OpenClaw collector adapter with harmless synthetic OTLP."""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterable


OTLP_ENDPOINT = os.environ.get("AGENTWEAVE_OTLP_ENDPOINT", "http://10.43.221.47:4318").rstrip("/")
TEMPO_URL = os.environ.get("AGENTWEAVE_TEMPO_URL", "http://192.168.1.70:31989").rstrip("/")
CONTENT_MARKER = "openclaw.content.input_messages"
PROBE_CASE = "agentweave.probe.case"
FORBIDDEN_PREFIXES = ("prov.session.",)
FORBIDDEN_KEYS = {"cost.usd", "prov.activity.type", CONTENT_MARKER}


def attribute(key: str, value: str | int) -> dict:
    value_key = "intValue" if isinstance(value, int) else "stringValue"
    return {"key": key, "value": {value_key: str(value)}}


def _value(attribute_value: dict) -> str | int | bool | float:
    for key, value in attribute_value.items():
        if key == "intValue":
            return int(value)
        if key == "doubleValue":
            return float(value)
        if key == "boolValue":
            return bool(value)
        if key == "stringValue":
            return value
    raise AssertionError("Tempo returned an unsupported attribute value")


def _attributes(span: dict) -> dict[str, str | int | bool | float]:
    return {entry["key"]: _value(entry["value"]) for entry in span.get("attributes", [])}


def _trace_spans(trace: dict) -> dict[str, tuple[str, dict, dict[str, str | int | bool | float]]]:
    spans: dict[str, tuple[str, dict, dict[str, str | int | bool | float]]] = {}
    for batch in trace.get("batches", []):
        resource = _attributes(batch.get("resource", {}))
        service = resource.get("service.name")
        for scoped in batch.get("scopeSpans", []):
            for span in scoped.get("spans", []):
                attributes = _attributes(span)
                case = attributes.get(PROBE_CASE)
                assert isinstance(case, str), "synthetic span is missing its safe case label"
                spans[case] = (str(service), span, attributes)
    return spans


def _require_exact(attributes: dict, expected: dict) -> None:
    for key, value in expected.items():
        assert attributes.get(key) == value, f"{key} was not {value!r}"


def _assert_no_forbidden(attributes: dict) -> None:
    forbidden = {key for key in attributes if key in FORBIDDEN_KEYS or key.startswith(FORBIDDEN_PREFIXES)}
    assert not forbidden, f"forbidden fields reached Tempo: {sorted(forbidden)}"


def assert_mapped_trace(trace: dict, expected_trace_id: str) -> None:
    """Assert the exact safe adapter behavior in a Tempo trace response."""
    spans = _trace_spans(trace)
    tempo_trace_id = base64.b64encode(bytes.fromhex(expected_trace_id)).decode("ascii")
    expected_names = {
        "cached",
        "no-cache",
        "existing-target",
        "native-usage",
        "unrelated",
    }
    assert expected_names <= spans.keys(), "synthetic spans did not appear together in Tempo"
    for name in expected_names:
        service, span, _ = spans[name]
        assert span.get("traceId") in (expected_trace_id, tempo_trace_id), f"{name} has a different trace ID"
        expected_service = "other-runtime" if name == "unrelated" else "openclaw"
        assert service == expected_service, f"{name} has service {service!r}"

    cached = spans["cached"][2]
    _require_exact(
        cached,
        {
            "openclaw.provider": "cached-provider",
            PROBE_CASE: "cached",
            "gen_ai.request.model": "cached-model",
            "gen_ai.usage.input_tokens": 100,
            "gen_ai.usage.output_tokens": 25,
            "gen_ai.usage.cache_read.input_tokens": 50,
            "gen_ai.usage.cache_creation.input_tokens": 8,
            "prov.harness": "openclaw",
            "prov.source": "native",
            "prov.llm.provider": "cached-provider",
            "prov.llm.model": "cached-model",
            "prov.llm.prompt_tokens": 100,
            "prov.llm.completion_tokens": 25,
            "tokens.cache_read": 50,
            "tokens.cache_write": 8,
        },
    )
    _assert_no_forbidden(cached)

    no_cache = spans["no-cache"][2]
    _require_exact(
        no_cache,
        {
            "openclaw.provider": "no-cache-provider",
            PROBE_CASE: "no-cache",
            "openclaw.model": "fallback-model",
            "gen_ai.usage.input_tokens": 12,
            "gen_ai.usage.output_tokens": 3,
            "prov.harness": "openclaw",
            "prov.source": "native",
            "prov.llm.provider": "no-cache-provider",
            "prov.llm.model": "fallback-model",
            "prov.llm.prompt_tokens": 12,
            "prov.llm.completion_tokens": 3,
        },
    )
    assert "tokens.cache_read" not in no_cache and "tokens.cache_write" not in no_cache
    _assert_no_forbidden(no_cache)

    existing = spans["existing-target"][2]
    _require_exact(
        existing,
        {
            "openclaw.provider": "existing-provider",
            PROBE_CASE: "existing-target",
            "gen_ai.request.model": "replacement-must-not-win",
            "gen_ai.usage.input_tokens": 9,
            "gen_ai.usage.output_tokens": 2,
            "prov.llm.model": "existing-model",
            "prov.harness": "openclaw",
            "prov.source": "native",
            "prov.llm.provider": "existing-provider",
            "prov.llm.prompt_tokens": 9,
            "prov.llm.completion_tokens": 2,
        },
    )
    _assert_no_forbidden(existing)

    usage = spans["native-usage"][2]
    assert usage == {
        "gen_ai.usage.input_tokens": 77,
        "openclaw.provider": "usage-provider",
        PROBE_CASE: "native-usage",
    }

    unrelated = spans["unrelated"][2]
    assert unrelated == {
        "openclaw.provider": "other-provider",
        "gen_ai.request.model": "other-model",
        "gen_ai.usage.input_tokens": 44,
        "gen_ai.usage.output_tokens": 4,
        CONTENT_MARKER: "harmless-content-marker",
        PROBE_CASE: "unrelated",
    }


def _span(name: str, trace_id: str, now_ns: int, attributes: dict[str, str | int]) -> dict:
    return {
        "traceId": trace_id,
        "spanId": uuid.uuid4().hex[:16],
        "name": name,
        "kind": 1,
        "startTimeUnixNano": str(now_ns),
        "endTimeUnixNano": str(now_ns + 1_000_000),
        "attributes": [attribute(key, value) for key, value in attributes.items()],
    }


def _resource_spans(service: str, spans: Iterable[dict]) -> dict:
    return {
        "resource": {"attributes": [attribute("service.name", service)]},
        "scopeSpans": [{"scope": {"name": "agentweave-native-mapping-probe"}, "spans": list(spans)}],
    }


def build_payload(trace_id: str, now_ns: int) -> dict:
    """Build a content-safe OTLP fixture covering every mapping boundary."""
    content = {CONTENT_MARKER: "harmless-content-marker"}
    return {
        "resourceSpans": [
            _resource_spans(
                "openclaw",
                [
                    _span("openclaw.model.call", trace_id, now_ns, {
                        "openclaw.provider": "cached-provider",
                        "gen_ai.request.model": "cached-model",
                        "gen_ai.usage.input_tokens": 100,
                        "gen_ai.usage.output_tokens": 25,
                        "gen_ai.usage.cache_read.input_tokens": 50,
                        "gen_ai.usage.cache_creation.input_tokens": 8,
                        PROBE_CASE: "cached",
                        **content,
                    }),
                    _span("openclaw.model.call", trace_id, now_ns, {
                        "openclaw.provider": "no-cache-provider",
                        "openclaw.model": "fallback-model",
                        "gen_ai.usage.input_tokens": 12,
                        "gen_ai.usage.output_tokens": 3,
                        PROBE_CASE: "no-cache",
                        **content,
                    }),
                    _span("openclaw.model.call", trace_id, now_ns, {
                        "openclaw.provider": "existing-provider",
                        "gen_ai.request.model": "replacement-must-not-win",
                        "gen_ai.usage.input_tokens": 9,
                        "gen_ai.usage.output_tokens": 2,
                        "prov.llm.model": "existing-model",
                        PROBE_CASE: "existing-target",
                        **content,
                    }),
                    _span("openclaw.model.usage", trace_id, now_ns, {
                        "gen_ai.usage.input_tokens": 77,
                        "openclaw.provider": "usage-provider",
                        PROBE_CASE: "native-usage",
                        **content,
                    }),
                ],
            ),
            _resource_spans(
                "other-runtime",
                [_span("openclaw.model.call", trace_id, now_ns, {
                    "openclaw.provider": "other-provider",
                    "gen_ai.request.model": "other-model",
                    "gen_ai.usage.input_tokens": 44,
                    "gen_ai.usage.output_tokens": 4,
                    PROBE_CASE: "unrelated",
                    **content,
                })],
            ),
        ]
    }


def fetch_trace(trace_id: str) -> dict:
    """Poll Tempo only until every synthetic span is queryable."""
    url = f"{TEMPO_URL}/api/traces/{trace_id}"
    deadline = time.monotonic() + 60
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            with urllib.request.urlopen(url, timeout=min(15, remaining)) as response:
                trace = json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
        except TimeoutError:
            pass
        else:
            if len(_trace_spans(trace)) >= 5:
                return trace
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(1, remaining))
    raise RuntimeError("synthetic spans did not appear together in Tempo within 60 seconds")


def main() -> None:
    trace_id = uuid.uuid4().hex
    request = urllib.request.Request(
        f"{OTLP_ENDPOINT}/v1/traces",
        data=json.dumps(build_payload(trace_id, time.time_ns())).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10):
            pass
        assert_mapped_trace(fetch_trace(trace_id), trace_id)
    except Exception:
        print(f"FAIL native collector mapping; synthetic trace {trace_id}")
        raise SystemExit(1) from None
    print(f"PASS native collector mapping; synthetic trace {trace_id}")


if __name__ == "__main__":
    main()
