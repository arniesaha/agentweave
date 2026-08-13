"""Constrained, read-only Tempo queries for ``agentweave trace ask``."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from typing import Any


class TraceQuestionError(ValueError):
    """Raised when a question cannot be mapped safely to an allowlisted query."""


class TempoQueryError(RuntimeError):
    """Raised when Tempo cannot execute a query."""


_TRACE_ID = re.compile(r"^[0-9a-fA-F]{32}$")
_SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")
_WINDOW = re.compile(r"^(\d+)([mhd])$")
_WINDOW_MULTIPLIERS = {"m": 60, "h": 3600, "d": 86400}
MAX_WINDOW_SECONDS = 7 * 86400
MAX_RESULTS = 100


@dataclass(frozen=True)
class QueryPlan:
    family: str
    traceql: str
    explanation: str


@dataclass(frozen=True)
class TraceCitation:
    trace_id: str
    root_service: str
    root_span: str
    duration_ms: float | None
    start_time_unix_nano: str | None
    agent_id: str | None = None
    cost_usd: float | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def parse_window(value: str) -> int:
    match = _WINDOW.fullmatch(value.strip().lower())
    if not match:
        raise TraceQuestionError("window must look like 15m, 6h, or 1d")
    seconds = int(match.group(1)) * _WINDOW_MULTIPLIERS[match.group(2)]
    if seconds < 60 or seconds > MAX_WINDOW_SECONDS:
        raise TraceQuestionError("window must be between 1m and 7d")
    return seconds


def validate_limit(value: int) -> int:
    if value < 1 or value > MAX_RESULTS:
        raise TraceQuestionError(f"limit must be between 1 and {MAX_RESULTS}")
    return value


def _validate_trace_id(value: str) -> str:
    if not _TRACE_ID.fullmatch(value):
        raise TraceQuestionError("trace ID must be exactly 32 hexadecimal characters")
    return value.lower()


def _validate_session_id(value: str) -> str:
    if not _SESSION_ID.fullmatch(value):
        raise TraceQuestionError(
            "session ID must be 1-128 characters using letters, numbers, '.', '_', ':', '/', or '-'"
        )
    return value


def plan_question(
    question: str,
    *,
    session_id: str | None = None,
    trace_id: str | None = None,
) -> QueryPlan:
    """Map a question to exactly one allowlisted TraceQL template."""

    normalized = " ".join(question.lower().split())
    families: list[str] = []
    if any(word in normalized for word in ("fail", "error", "why did")) and (
        session_id or trace_id
    ):
        families.append("failures")
    if "agent" in normalized and any(word in normalized for word in ("fail", "error")):
        families.append("agent-errors")
    if any(word in normalized for word in ("slow", "latency", "duration")):
        families.append("slow-calls")
    if any(word in normalized for word in ("expensive", "cost", "spend")):
        families.append("expensive-calls")

    families = list(dict.fromkeys(families))
    if len(families) != 1:
        raise TraceQuestionError(
            "question must match exactly one supported family: failures for --session/--trace-id, "
            "agent error comparison, slow calls, or expensive calls"
        )

    family = families[0]
    if family == "failures":
        if trace_id:
            value = _validate_trace_id(trace_id)
            return QueryPlan(family, f'{{ trace:id = "{value}" && status = error }}', "errors in trace")
        value = _validate_session_id(session_id or "")
        return QueryPlan(
            family,
            f'{{ span.prov.session.id = "{value}" && status = error }}',
            "errors in session",
        )
    if session_id or trace_id:
        raise TraceQuestionError("--session and --trace-id are only valid for failure questions")
    if family == "agent-errors":
        return QueryPlan(
            family,
            "{ status = error && span.prov.agent.id != nil } | select(span.prov.agent.id)",
            "error traces grouped for comparison by agent",
        )
    if family == "slow-calls":
        return QueryPlan(
            family,
            "{ duration > 1s }",
            "traces containing spans slower than one second",
        )
    return QueryPlan(
        family,
        "{ span.cost.usd > 0 } | select(span.cost.usd, span.prov.agent.id)",
        "LLM calls with recorded cost",
    )


def _citation(item: dict[str, Any]) -> TraceCitation | None:
    trace_id = str(item.get("traceID") or "")
    if not _TRACE_ID.fullmatch(trace_id):
        return None
    duration = item.get("durationMs")
    try:
        duration_ms = float(duration) if duration is not None else None
    except (TypeError, ValueError):
        duration_ms = None
    attributes: dict[str, object] = {}
    for span_set in item.get("spanSets") or [item.get("spanSet") or {}]:
        for span in span_set.get("spans") or []:
            for attribute in span.get("attributes") or []:
                value = attribute.get("value") or {}
                scalar = next(
                    (value[key] for key in ("stringValue", "doubleValue", "intValue") if key in value),
                    None,
                )
                attributes[str(attribute.get("key") or "")] = scalar
    raw_cost = attributes.get("cost.usd")
    try:
        cost_usd = float(raw_cost) if raw_cost is not None else None
    except (TypeError, ValueError):
        cost_usd = None
    return TraceCitation(
        trace_id=trace_id.lower(),
        root_service=str(item.get("rootServiceName") or "unknown"),
        root_span=str(item.get("rootTraceName") or "unknown"),
        duration_ms=duration_ms,
        start_time_unix_nano=(
            str(item["startTimeUnixNano"]) if item.get("startTimeUnixNano") is not None else None
        ),
        agent_id=str(attributes["prov.agent.id"]) if attributes.get("prov.agent.id") else None,
        cost_usd=cost_usd,
    )


def query_tempo(
    tempo_url: str,
    plan: QueryPlan,
    *,
    window_seconds: int,
    limit: int,
    timeout_seconds: float = 10.0,
    now: int | None = None,
) -> list[TraceCitation]:
    """Execute a bounded Tempo search and return validated trace citations."""

    validate_limit(limit)
    if window_seconds < 60 or window_seconds > MAX_WINDOW_SECONDS:
        raise TraceQuestionError("window must be between 1m and 7d")
    end = int(time.time()) if now is None else now
    query = urllib.parse.urlencode(
        {
            "q": plan.traceql,
            "start": str(end - window_seconds),
            "end": str(end),
            "limit": str(limit),
        }
    )
    request = urllib.request.Request(
        f"{tempo_url.rstrip('/')}/api/search?{query}",
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise TempoQueryError(f"Tempo query failed: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("traces", []), list):
        raise TempoQueryError("Tempo returned an unexpected search response")
    return [citation for item in payload.get("traces", []) if (citation := _citation(item))]


def answer_payload(plan: QueryPlan, citations: list[TraceCitation]) -> dict[str, object]:
    if citations:
        if plan.family == "agent-errors":
            counts: dict[str, int] = {}
            for citation in citations:
                agent = citation.agent_id or "unknown"
                counts[agent] = counts.get(agent, 0) + 1
            comparison = ", ".join(
                f"{agent}: {count}" for agent, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
            )
            summary = f"Found {len(citations)} error trace(s) by agent ({comparison})."
        else:
            summary = f"Found {len(citations)} matching trace(s) for {plan.explanation}."
    else:
        summary = f"No matching data found for {plan.explanation}."
    return {
        "family": plan.family,
        "summary": summary,
        "traceql": plan.traceql,
        "citations": [citation.to_dict() for citation in citations],
    }
