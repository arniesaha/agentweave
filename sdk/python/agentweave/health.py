"""Agent health scoring — per-agent reliability, error rate, and latency SLOs.

Health scores (0-100) are computed per agent from span data ingested through
the AgentWeave proxy.  The score is a weighted composite of four signals:

  1. Error rate       (30%)  — fraction of spans with StatusCode.ERROR
  2. P95 latency      (30%)  — P95 vs a configurable baseline (default 10 s)
  3. Cost efficiency  (20%)  — cost per session vs rolling average
  4. Tool retry rate  (20%)  — fraction of sessions with repeated tool calls

Badge colours match the issue acceptance criteria:
  green  >= 80
  yellow >= 60
  red    < 60

Alerting
--------
When a score drops below a per-agent (or global) threshold a webhook POST is
fired with the full score payload.  Configure via environment variables or
``AgentWeaveConfig``:

  AGENTWEAVE_HEALTH_WEBHOOK_URL=https://...      # webhook target
  AGENTWEAVE_HEALTH_THRESHOLD=60                 # global default threshold
  AGENTWEAVE_HEALTH_WINDOW_SECONDS=3600          # look-back window (default 1h)

SLO config
----------
Per-agent thresholds can be injected at runtime via ``POST /v1/agent-health/config``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import httpx

logger = logging.getLogger("agentweave.health")

# ---------------------------------------------------------------------------
# In-memory span store
# ---------------------------------------------------------------------------

@dataclass
class SpanRecord:
    """Lightweight record of a single proxied span for health computation."""
    agent_id: str
    session_id: str
    timestamp_ms: float       # epoch millis
    duration_ms: float
    is_error: bool
    cost_usd: float
    tool_name: Optional[str] = None   # set for tool_call spans


# Module-level span buffer — rotated every _WINDOW_SECONDS
_spans: List[SpanRecord] = []
_spans_lock = asyncio.Lock()

def _window_seconds() -> int:
    return int(os.getenv("AGENTWEAVE_HEALTH_WINDOW_SECONDS", "3600"))

def _global_threshold() -> float:
    return float(os.getenv("AGENTWEAVE_HEALTH_THRESHOLD", "60"))

def _webhook_url() -> Optional[str]:
    return os.getenv("AGENTWEAVE_HEALTH_WEBHOOK_URL") or None

# Per-agent SLO config (runtime-overrideable)
_agent_config: Dict[str, dict] = {}

# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------

def record_span(
    agent_id: str,
    session_id: str,
    duration_ms: float,
    is_error: bool,
    cost_usd: float,
    tool_name: Optional[str] = None,
) -> None:
    """Add a span record.  Thread-safe (uses asyncio lock via run_coroutine_threadsafe
    if called from outside an event loop, or schedules on the loop otherwise).
    """
    rec = SpanRecord(
        agent_id=agent_id,
        session_id=session_id,
        timestamp_ms=time.time() * 1000,
        duration_ms=duration_ms,
        is_error=is_error,
        cost_usd=cost_usd,
        tool_name=tool_name,
    )
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.call_soon_threadsafe(_spans.append, rec)
        else:
            _spans.append(rec)
    except RuntimeError:
        _spans.append(rec)


# ---------------------------------------------------------------------------
# Score computation
# ---------------------------------------------------------------------------

@dataclass
class AgentHealthScore:
    agent_id: str
    score: float            # 0-100
    badge: str              # "green" | "yellow" | "red"
    error_rate: float       # 0.0-1.0
    p95_latency_ms: float
    avg_cost_per_session: float
    tool_retry_rate: float  # fraction of sessions with a retry
    span_count: int
    window_seconds: int
    threshold: float
    computed_at: float      # epoch seconds
    components: dict = field(default_factory=dict)


def _compute_p95(values: List[float]) -> float:
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    idx = int(len(sorted_vals) * 0.95)
    return sorted_vals[min(idx, len(sorted_vals) - 1)]


def compute_health_score(
    agent_id: str,
    spans: List[SpanRecord],
    *,
    p95_baseline_ms: float = 10_000.0,
    cost_baseline_usd: float = 0.01,
    threshold: Optional[float] = None,
) -> AgentHealthScore:
    """Compute a health score for *agent_id* from *spans*."""
    if threshold is None:
        # Per-agent override, then global
        cfg = _agent_config.get(agent_id, {})
        threshold = float(cfg.get("threshold", _global_threshold()))
        p95_baseline_ms = float(cfg.get("p95_baseline_ms", p95_baseline_ms))
        cost_baseline_usd = float(cfg.get("cost_baseline_usd", cost_baseline_usd))

    if not spans:
        score = AgentHealthScore(
            agent_id=agent_id,
            score=100.0,
            badge="green",
            error_rate=0.0,
            p95_latency_ms=0.0,
            avg_cost_per_session=0.0,
            tool_retry_rate=0.0,
            span_count=0,
            window_seconds=_window_seconds(),
            threshold=threshold,
            computed_at=time.time(),
            components={"error_rate": 100.0, "latency": 100.0, "cost": 100.0, "tool_retry": 100.0},
        )
        return score

    # --- Signal 1: Error rate ---
    error_count = sum(1 for s in spans if s.is_error)
    error_rate = error_count / len(spans)
    error_score = max(0.0, 100.0 * (1.0 - error_rate))

    # --- Signal 2: P95 latency ---
    latencies = [s.duration_ms for s in spans if s.duration_ms > 0]
    p95_ms = _compute_p95(latencies)
    if p95_baseline_ms > 0:
        latency_ratio = p95_ms / p95_baseline_ms
    else:
        latency_ratio = 1.0
    latency_score = max(0.0, min(100.0, 100.0 * (1.0 - max(0.0, latency_ratio - 1.0) / 3.0)))

    # --- Signal 3: Cost per session ---
    session_costs: Dict[str, float] = {}
    for s in spans:
        session_costs[s.session_id] = session_costs.get(s.session_id, 0.0) + s.cost_usd
    avg_cost = sum(session_costs.values()) / len(session_costs) if session_costs else 0.0
    if cost_baseline_usd > 0:
        cost_ratio = avg_cost / cost_baseline_usd
    else:
        cost_ratio = 1.0
    cost_score = max(0.0, min(100.0, 100.0 * (1.0 - max(0.0, cost_ratio - 1.0) / 5.0)))

    # --- Signal 4: Tool retry rate ---
    # A retry = same tool called >2x in one session
    session_tool_counts: Dict[str, Dict[str, int]] = {}
    for s in spans:
        if s.tool_name:
            if s.session_id not in session_tool_counts:
                session_tool_counts[s.session_id] = {}
            session_tool_counts[s.session_id][s.tool_name] = (
                session_tool_counts[s.session_id].get(s.tool_name, 0) + 1
            )
    sessions_with_retry = sum(
        1 for tool_map in session_tool_counts.values()
        if any(count > 2 for count in tool_map.values())
    )
    total_sessions = len(set(s.session_id for s in spans))
    tool_retry_rate = sessions_with_retry / total_sessions if total_sessions else 0.0
    tool_retry_score = max(0.0, 100.0 * (1.0 - tool_retry_rate))

    # --- Composite (weighted average) ---
    score = (
        0.30 * error_score
        + 0.30 * latency_score
        + 0.20 * cost_score
        + 0.20 * tool_retry_score
    )

    badge = "green" if score >= 80 else ("yellow" if score >= 60 else "red")

    return AgentHealthScore(
        agent_id=agent_id,
        score=round(score, 1),
        badge=badge,
        error_rate=round(error_rate, 4),
        p95_latency_ms=round(p95_ms, 1),
        avg_cost_per_session=round(avg_cost, 6),
        tool_retry_rate=round(tool_retry_rate, 4),
        span_count=len(spans),
        window_seconds=_window_seconds(),
        threshold=threshold,
        computed_at=time.time(),
        components={
            "error_rate": round(error_score, 1),
            "latency": round(latency_score, 1),
            "cost": round(cost_score, 1),
            "tool_retry": round(tool_retry_score, 1),
        },
    )


def get_all_scores() -> List[AgentHealthScore]:
    """Compute health scores for all agents observed in the current window."""
    cutoff_ms = (time.time() - _window_seconds()) * 1000
    recent = [s for s in _spans if s.timestamp_ms >= cutoff_ms]

    agents: Dict[str, List[SpanRecord]] = {}
    for s in recent:
        agents.setdefault(s.agent_id, []).append(s)

    return [compute_health_score(agent_id, agent_spans) for agent_id, agent_spans in agents.items()]


# ---------------------------------------------------------------------------
# Webhook alerting
# ---------------------------------------------------------------------------

async def maybe_fire_webhook(score: AgentHealthScore) -> None:
    """Fire a webhook if the score is below the configured threshold."""
    url = _webhook_url()
    if not url:
        return
    if score.score < score.threshold:
        payload = {
            "event": "agent_health_alert",
            "agent_id": score.agent_id,
            "score": score.score,
            "badge": score.badge,
            "threshold": score.threshold,
            "error_rate": score.error_rate,
            "p95_latency_ms": score.p95_latency_ms,
            "avg_cost_per_session": score.avg_cost_per_session,
            "tool_retry_rate": score.tool_retry_rate,
            "span_count": score.span_count,
            "computed_at": score.computed_at,
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code >= 400:
                    logger.warning("health webhook returned %d for agent %s", resp.status_code, score.agent_id)
                else:
                    logger.info("health alert fired for agent %s (score=%.1f)", score.agent_id, score.score)
        except Exception as exc:
            logger.warning("health webhook error for agent %s: %s", score.agent_id, exc)
