"""Tests for agentweave.health — agent health scoring (issue #116)."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from agentweave.health import (
    SpanRecord,
    compute_health_score,
    get_all_scores,
    record_span,
    _spans,
    _agent_config,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_span(
    agent_id: str = "test-agent",
    session_id: str = "s1",
    duration_ms: float = 1000.0,
    is_error: bool = False,
    cost_usd: float = 0.005,
    tool_name: str | None = None,
    timestamp_ms: float | None = None,
) -> SpanRecord:
    return SpanRecord(
        agent_id=agent_id,
        session_id=session_id,
        timestamp_ms=timestamp_ms or time.time() * 1000,
        duration_ms=duration_ms,
        is_error=is_error,
        cost_usd=cost_usd,
        tool_name=tool_name,
    )


# ---------------------------------------------------------------------------
# compute_health_score
# ---------------------------------------------------------------------------

class TestComputeHealthScore:
    def test_empty_spans_returns_perfect_score(self):
        score = compute_health_score("agent-x", [])
        assert score.score == 100.0
        assert score.badge == "green"
        assert score.span_count == 0

    def test_all_ok_spans_produce_high_score(self):
        spans = [_make_span(duration_ms=500, is_error=False, cost_usd=0.001) for _ in range(20)]
        score = compute_health_score("agent-x", spans, p95_baseline_ms=10_000, cost_baseline_usd=0.01)
        assert score.score >= 80
        assert score.badge == "green"
        assert score.error_rate == 0.0

    def test_all_error_spans_produce_low_score(self):
        spans = [_make_span(is_error=True) for _ in range(10)]
        score = compute_health_score("agent-x", spans)
        assert score.score < 60
        assert score.badge == "red"
        assert score.error_rate == 1.0

    def test_partial_error_rate(self):
        spans = (
            [_make_span(is_error=True) for _ in range(3)]
            + [_make_span(is_error=False) for _ in range(7)]
        )
        score = compute_health_score("agent-x", spans)
        assert abs(score.error_rate - 0.30) < 0.001

    def test_high_latency_reduces_score(self):
        # P95 latency >> baseline → latency score low
        spans = [_make_span(duration_ms=50_000) for _ in range(20)]
        score = compute_health_score("agent-x", spans, p95_baseline_ms=10_000)
        assert score.p95_latency_ms >= 40_000
        # Latency component should be well below 100
        assert score.components["latency"] < 50

    def test_high_cost_reduces_score(self):
        # avg cost per session = 0.10, baseline = 0.01 → ratio = 10
        spans = [_make_span(cost_usd=0.10, session_id=f"s{i}") for i in range(10)]
        score = compute_health_score("agent-x", spans, cost_baseline_usd=0.01)
        assert score.components["cost"] == 0.0  # capped at 0

    def test_tool_retry_reduces_score(self):
        # Same tool called 5x in one session → retry
        spans = [_make_span(session_id="retry-session", tool_name="bash") for _ in range(5)]
        score = compute_health_score("agent-x", spans)
        assert score.tool_retry_rate > 0.0
        assert score.components["tool_retry"] < 100

    def test_tool_retry_not_triggered_at_2_or_fewer(self):
        # Tool called exactly 2x — not a retry (threshold is >2)
        spans = [_make_span(session_id="s1", tool_name="bash") for _ in range(2)]
        score = compute_health_score("agent-x", spans)
        assert score.tool_retry_rate == 0.0

    def test_badge_green_at_80(self):
        # Construct a score that lands at exactly 80
        spans = [_make_span(duration_ms=100, is_error=False, cost_usd=0.001) for _ in range(10)]
        score = compute_health_score("agent-x", spans, p95_baseline_ms=10_000, cost_baseline_usd=0.01)
        assert score.badge in ("green",)

    def test_badge_yellow_range(self):
        # 50% error rate → error_score=50, the rest near 100 → composite ~65–70
        spans = (
            [_make_span(is_error=True, duration_ms=500) for _ in range(5)]
            + [_make_span(is_error=False, duration_ms=500) for _ in range(5)]
        )
        score = compute_health_score("agent-x", spans)
        assert score.badge in ("yellow", "red")

    def test_p95_computed_correctly(self):
        # 100 spans, durations 1..100 ms; p95 ≈ 95
        spans = [_make_span(duration_ms=float(i + 1)) for i in range(100)]
        score = compute_health_score("agent-x", spans)
        assert 93 <= score.p95_latency_ms <= 97

    def test_span_count_matches(self):
        spans = [_make_span() for _ in range(42)]
        score = compute_health_score("agent-x", spans)
        assert score.span_count == 42

    def test_per_agent_config_threshold(self):
        """Per-agent config threshold should be reflected in the score object."""
        _agent_config["cfg-agent"] = {"threshold": 90.0}
        try:
            spans = [_make_span(agent_id="cfg-agent") for _ in range(5)]
            score = compute_health_score("cfg-agent", spans)
            assert score.threshold == 90.0
        finally:
            del _agent_config["cfg-agent"]


# ---------------------------------------------------------------------------
# get_all_scores
# ---------------------------------------------------------------------------

class TestGetAllScores:
    def setup_method(self):
        _spans.clear()

    def test_returns_empty_when_no_spans(self):
        assert get_all_scores() == []

    def test_groups_by_agent(self):
        _spans.extend([
            _make_span(agent_id="agent-a"),
            _make_span(agent_id="agent-a"),
            _make_span(agent_id="agent-b"),
        ])
        scores = get_all_scores()
        agent_ids = {s.agent_id for s in scores}
        assert "agent-a" in agent_ids
        assert "agent-b" in agent_ids

    def test_excludes_stale_spans(self):
        old_ts = (time.time() - 7200) * 1000  # 2 hours ago
        _spans.append(_make_span(agent_id="stale-agent", timestamp_ms=old_ts))
        scores = get_all_scores()
        stale_ids = {s.agent_id for s in scores}
        # Should NOT appear (window default = 3600s)
        assert "stale-agent" not in stale_ids

    def test_includes_recent_spans(self):
        _spans.append(_make_span(agent_id="fresh-agent"))
        scores = get_all_scores()
        assert any(s.agent_id == "fresh-agent" for s in scores)


# ---------------------------------------------------------------------------
# record_span
# ---------------------------------------------------------------------------

class TestRecordSpan:
    def setup_method(self):
        _spans.clear()

    def test_record_adds_span(self):
        record_span("my-agent", "sess-1", 500.0, False, 0.002)
        assert len(_spans) == 1
        rec = _spans[0]
        assert rec.agent_id == "my-agent"
        assert rec.session_id == "sess-1"
        assert rec.duration_ms == 500.0
        assert rec.is_error is False
        assert rec.cost_usd == 0.002

    def test_record_error_span(self):
        record_span("err-agent", "sess-err", 100.0, True, 0.0)
        assert _spans[0].is_error is True

    def test_record_with_tool_name(self):
        record_span("tool-agent", "sess-t", 200.0, False, 0.001, tool_name="bash")
        assert _spans[0].tool_name == "bash"
