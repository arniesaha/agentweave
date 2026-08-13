"""Deterministic anomaly detection and session optimization suggestions."""

from __future__ import annotations

import statistics
import time
from dataclasses import asdict, dataclass
from typing import Iterable

from agentweave.health import SpanRecord


@dataclass(frozen=True)
class Diagnostic:
    event: str
    signal: str
    severity: str
    message: str
    agent_id: str
    session_ids: tuple[str, ...]
    current: float
    baseline: float

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["session_ids"] = list(self.session_ids)
        return payload


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(int(len(ordered) * 0.95), len(ordered) - 1)]


def _error_rate(spans: list[SpanRecord]) -> float:
    return sum(span.is_error for span in spans) / len(spans) if spans else 0.0


def _average_cost(spans: list[SpanRecord]) -> float:
    return sum(span.cost_usd for span in spans) / len(spans) if spans else 0.0


def _severity(ratio: float) -> str:
    return "critical" if ratio >= 3 else "warning"


def detect_anomalies(
    spans: Iterable[SpanRecord],
    *,
    now_ms: float | None = None,
    current_window_seconds: int = 1800,
    minimum_samples: int = 3,
) -> list[Diagnostic]:
    """Compare the current window with the immediately preceding baseline."""

    now = time.time() * 1000 if now_ms is None else now_ms
    split = now - current_window_seconds * 1000
    start = split - current_window_seconds * 1000
    by_agent: dict[str, list[SpanRecord]] = {}
    for span in spans:
        if span.timestamp_ms >= start:
            by_agent.setdefault(span.agent_id, []).append(span)

    findings: list[Diagnostic] = []
    for agent_id, agent_spans in by_agent.items():
        baseline = [span for span in agent_spans if start <= span.timestamp_ms < split]
        current = [span for span in agent_spans if span.timestamp_ms >= split]
        if len(baseline) < minimum_samples or len(current) < minimum_samples:
            continue
        session_ids = tuple(sorted({span.session_id for span in current})[:20])
        metrics = (
            ("error_rate", _error_rate(current), _error_rate(baseline), 0.10),
            (
                "p95_latency_ms",
                _p95([span.duration_ms for span in current]),
                _p95([span.duration_ms for span in baseline]),
                100.0,
            ),
            ("avg_cost_usd", _average_cost(current), _average_cost(baseline), 0.000001),
        )
        for signal, current_value, baseline_value, floor in metrics:
            if current_value <= baseline_value or current_value - baseline_value < floor:
                continue
            ratio = current_value / max(baseline_value, floor)
            if ratio < 1.5:
                continue
            findings.append(
                Diagnostic(
                    event="diagnostic.anomaly",
                    signal=signal,
                    severity=_severity(ratio),
                    message=(
                        f"{agent_id} {signal} is {ratio:.1f}x its preceding-window baseline"
                    ),
                    agent_id=agent_id,
                    session_ids=session_ids,
                    current=round(current_value, 6),
                    baseline=round(baseline_value, 6),
                )
            )
    return findings


def suggest_optimizations(spans: Iterable[SpanRecord]) -> list[Diagnostic]:
    """Return evidence-backed suggestions for retry loops and session cost spikes."""

    sessions: dict[tuple[str, str], list[SpanRecord]] = {}
    for span in spans:
        sessions.setdefault((span.agent_id, span.session_id), []).append(span)
    session_costs = [sum(span.cost_usd for span in values) for values in sessions.values()]
    median_cost = statistics.median(session_costs) if session_costs else 0.0
    findings: list[Diagnostic] = []
    for (agent_id, session_id), values in sessions.items():
        tool_counts: dict[str, int] = {}
        for span in values:
            if span.tool_name:
                tool_counts[span.tool_name] = tool_counts.get(span.tool_name, 0) + 1
        repeated = sorted((name, count) for name, count in tool_counts.items() if count > 2)
        if repeated:
            tool, count = max(repeated, key=lambda item: item[1])
            findings.append(
                Diagnostic(
                    event="diagnostic.optimization",
                    signal="tool_retry_loop",
                    severity="warning",
                    message=f"Session {session_id} called {tool} {count} times; inspect retry termination",
                    agent_id=agent_id,
                    session_ids=(session_id,),
                    current=float(count),
                    baseline=2.0,
                )
            )
        cost = sum(span.cost_usd for span in values)
        if median_cost > 0 and cost >= median_cost * 2 and cost - median_cost >= 0.001:
            findings.append(
                Diagnostic(
                    event="diagnostic.optimization",
                    signal="session_cost_spike",
                    severity="warning",
                    message=f"Session {session_id} cost is {cost / median_cost:.1f}x the median session",
                    agent_id=agent_id,
                    session_ids=(session_id,),
                    current=round(cost, 6),
                    baseline=round(median_cost, 6),
                )
            )
    return findings


def analysis_payload(spans: Iterable[SpanRecord], *, now_ms: float | None = None) -> dict[str, object]:
    records = list(spans)
    anomalies = detect_anomalies(records, now_ms=now_ms)
    suggestions = suggest_optimizations(records)
    return {
        "read_only": True,
        "anomalies": [finding.to_dict() for finding in anomalies],
        "suggestions": [finding.to_dict() for finding in suggestions],
        "span_count": len(records),
    }
