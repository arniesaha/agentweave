from __future__ import annotations

import asyncio

from agentweave.analysis import analysis_payload, detect_anomalies, suggest_optimizations
from agentweave.health import SpanRecord


def span(*, timestamp, duration=100, error=False, cost=0.001, tool=None, session="s1"):
    return SpanRecord("nix", session, timestamp, duration, error, cost, tool)


def test_detects_window_over_window_latency_error_and_cost_anomalies():
    now = 4_000_000.0
    records = [span(timestamp=1_000_000 + index) for index in range(4)]
    records += [
        span(timestamp=3_000_000 + index, duration=1000, error=True, cost=0.01, session="hot")
        for index in range(4)
    ]
    findings = detect_anomalies(records, now_ms=now, current_window_seconds=2000)
    assert {finding.signal for finding in findings} == {
        "error_rate",
        "p95_latency_ms",
        "avg_cost_usd",
    }
    assert all(finding.event == "diagnostic.anomaly" for finding in findings)
    assert all(finding.session_ids == ("hot",) for finding in findings)


def test_requires_enough_baseline_and_current_samples():
    assert detect_anomalies([span(timestamp=1000)], now_ms=2000) == []


def test_suggests_retry_loop_and_cost_spike_with_session_evidence():
    records = [
        span(timestamp=1, session="normal-1", cost=0.001),
        span(timestamp=1, session="normal-2", cost=0.001),
        span(timestamp=1, session="normal-3", cost=0.001),
    ]
    records += [
        span(timestamp=2 + index, session="hot", cost=0.002, tool="bash")
        for index in range(4)
    ]
    findings = suggest_optimizations(records)
    assert {finding.signal for finding in findings} == {"tool_retry_loop", "session_cost_spike"}
    assert all(finding.session_ids == ("hot",) for finding in findings)
    assert all(finding.event == "diagnostic.optimization" for finding in findings)


def test_payload_is_explicitly_read_only():
    payload = analysis_payload([])
    assert payload == {"read_only": True, "anomalies": [], "suggestions": [], "span_count": 0}


def test_proxy_analysis_endpoint_returns_diagnostic_payload(monkeypatch):
    import agentweave.health as health
    from agentweave.proxy import get_trace_analysis

    monkeypatch.setattr(health, "_spans", [])
    payload = asyncio.run(get_trace_analysis())
    assert payload["read_only"] is True
    assert payload["anomalies"] == []
    assert payload["suggestions"] == []
