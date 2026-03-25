"""AgentWeave Budget Tracking — per-agent spend limits with webhook alerting.

Tracks cumulative cost per agent-id per day.  When a limit is crossed, emits
a ``budget.exceeded`` span and (optionally) POSTs to a configurable webhook.

Configuration via environment variables or a JSON file
(``AGENTWEAVE_BUDGET_CONFIG_PATH``, default ``~/.agentweave/budget.json``).

Environment variable quick-start::

    # Global daily limit of $1.00 (all agents combined)
    AGENTWEAVE_BUDGET_GLOBAL_DAILY=1.00

    # Per-agent limits (comma-separated key=value pairs)
    AGENTWEAVE_BUDGET_AGENTS=nix-v1=5.00,max-v1=2.50

    # Webhook to call when a limit is exceeded
    AGENTWEAVE_BUDGET_WEBHOOK_URL=https://hooks.example.com/budget-alert

Budget config file (JSON)::

    {
      "global_daily": 10.00,
      "agents": {
        "nix-v1": {"daily": 5.00},
        "max-v1": {"daily": 2.50}
      },
      "webhook_url": "https://hooks.example.com/budget-alert"
    }
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, Optional

logger = logging.getLogger("agentweave.budget")

# ---------------------------------------------------------------------------
# Schema constants (also added to schema.py)
# ---------------------------------------------------------------------------

BUDGET_EVENT_TYPE = "budget.exceeded"
BUDGET_AGENT_ID = "budget.agent_id"
BUDGET_LIMIT_USD = "budget.limit_usd"
BUDGET_SPENT_USD = "budget.spent_usd"
BUDGET_PERIOD = "budget.period"
BUDGET_WEBHOOK_URL = "budget.webhook_url"


# ---------------------------------------------------------------------------
# Config dataclasses
# ---------------------------------------------------------------------------

@dataclass
class AgentBudget:
    """Per-agent budget limits."""
    daily: Optional[float] = None  # USD per day; None = no limit


@dataclass
class BudgetConfig:
    """Global budget configuration loaded once at startup."""
    global_daily: Optional[float] = None          # USD/day across all agents
    agents: Dict[str, AgentBudget] = field(default_factory=dict)
    webhook_url: Optional[str] = None             # POST target for alerts

    @classmethod
    def from_env_and_file(cls) -> "BudgetConfig":
        """Load config from env vars + optional JSON file.

        File is loaded first; env vars override on a field-by-field basis.
        """
        cfg = cls()

        # --- JSON file ---
        config_path = os.getenv(
            "AGENTWEAVE_BUDGET_CONFIG_PATH",
            os.path.expanduser("~/.agentweave/budget.json"),
        )
        if os.path.isfile(config_path):
            try:
                with open(config_path) as fh:
                    data = json.load(fh)
                if "global_daily" in data:
                    cfg.global_daily = float(data["global_daily"])
                if "agents" in data:
                    for agent_id, limits in data["agents"].items():
                        cfg.agents[agent_id] = AgentBudget(
                            daily=float(limits["daily"]) if "daily" in limits else None,
                        )
                if "webhook_url" in data:
                    cfg.webhook_url = str(data["webhook_url"])
                logger.info("Budget config loaded from %s", config_path)
            except Exception as exc:
                logger.warning("Failed to load budget config from %s: %s", config_path, exc)

        # --- Env var overrides ---
        env_global = os.getenv("AGENTWEAVE_BUDGET_GLOBAL_DAILY")
        if env_global:
            try:
                cfg.global_daily = float(env_global)
            except ValueError:
                logger.warning("Invalid AGENTWEAVE_BUDGET_GLOBAL_DAILY: %r", env_global)

        env_agents = os.getenv("AGENTWEAVE_BUDGET_AGENTS")
        if env_agents:
            # Format: "agent1=2.50,agent2=1.00"
            for pair in env_agents.split(","):
                pair = pair.strip()
                if "=" in pair:
                    agent_id, val = pair.split("=", 1)
                    try:
                        cfg.agents[agent_id.strip()] = AgentBudget(daily=float(val.strip()))
                    except ValueError:
                        logger.warning("Invalid agent budget pair: %r", pair)

        env_webhook = os.getenv("AGENTWEAVE_BUDGET_WEBHOOK_URL")
        if env_webhook:
            cfg.webhook_url = env_webhook

        return cfg

    def is_configured(self) -> bool:
        """Return True if any limit is configured."""
        return self.global_daily is not None or bool(self.agents)

    def save(self, path: Optional[str] = None) -> None:
        """Persist budget config to a JSON file."""
        if path is None:
            path = os.getenv(
                "AGENTWEAVE_BUDGET_CONFIG_PATH",
                os.path.expanduser("~/.agentweave/budget.json"),
            )
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        data: dict = {}
        if self.global_daily is not None:
            data["global_daily"] = self.global_daily
        if self.agents:
            data["agents"] = {
                aid: {"daily": ab.daily}
                for aid, ab in self.agents.items()
                if ab.daily is not None
            }
        if self.webhook_url:
            data["webhook_url"] = self.webhook_url
        with open(path, "w") as fh:
            json.dump(data, fh, indent=2)
        logger.info("Budget config saved to %s", path)


# ---------------------------------------------------------------------------
# Spend tracker (in-memory, daily reset)
# ---------------------------------------------------------------------------

class BudgetTracker:
    """Thread-safe, in-memory daily spend tracker.

    Accumulates cost per agent-id.  Resets all counters at UTC midnight.
    Emits ``budget.exceeded`` OTel spans and calls the configured webhook
    when a limit is crossed.
    """

    def __init__(self, config: BudgetConfig):
        self._cfg = config
        self._lock = threading.Lock()
        self._date: date = date.today()
        # Keyed by agent_id.  "_global_" is the global accumulator.
        self._daily_spend: Dict[str, float] = {}
        # Track which (agent_id, period) pairs have already fired alerts today
        # so we emit at most one alert per limit per day.
        self._alerted: set = set()

    def _check_date_reset(self) -> None:
        """Reset counters if the UTC day has changed.  Must be called under lock."""
        today = date.today()
        if today != self._date:
            self._date = today
            self._daily_spend = {}
            self._alerted = set()
            logger.debug("Budget counters reset for %s", today)

    def record_cost(
        self,
        agent_id: str,
        cost_usd: float,
        session_id: Optional[str] = None,
        tracer=None,
    ) -> None:
        """Record a new LLM call cost and check thresholds.

        If any threshold is exceeded for the first time today, emits a
        ``budget.exceeded`` OTel span and fires the webhook (if configured).

        Args:
            agent_id: The agent that made the LLM call.
            cost_usd: Cost in USD for this single call.
            session_id: Optional session ID for span attribution.
            tracer: OTel tracer instance (from ``get_tracer()``).
        """
        if cost_usd <= 0 or not self._cfg.is_configured():
            return

        with self._lock:
            self._check_date_reset()

            # Update agent-level and global counters
            self._daily_spend[agent_id] = self._daily_spend.get(agent_id, 0.0) + cost_usd
            self._daily_spend["_global_"] = self._daily_spend.get("_global_", 0.0) + cost_usd

            agent_spent = self._daily_spend[agent_id]
            global_spent = self._daily_spend["_global_"]

            # Collect threshold violations (at most one fire per (agent, period) per day)
            violations: list[tuple[str, float, float, str]] = []

            # Per-agent daily limit
            agent_budget = self._cfg.agents.get(agent_id)
            if agent_budget and agent_budget.daily is not None:
                key = (agent_id, "daily")
                if agent_spent >= agent_budget.daily and key not in self._alerted:
                    self._alerted.add(key)
                    violations.append((agent_id, agent_budget.daily, agent_spent, "daily"))

            # Global daily limit
            if self._cfg.global_daily is not None:
                key = ("_global_", "daily")
                if global_spent >= self._cfg.global_daily and key not in self._alerted:
                    self._alerted.add(key)
                    violations.append(("_global_", self._cfg.global_daily, global_spent, "daily"))

        # Fire alerts outside the lock so webhook I/O doesn't block the proxy
        for vid, limit, spent, period in violations:
            self._fire_alert(
                agent_id=vid,
                limit_usd=limit,
                spent_usd=spent,
                period=period,
                session_id=session_id,
                tracer=tracer,
            )

    def get_spent(self, agent_id: Optional[str] = None) -> float:
        """Return cumulative spend today for an agent (or global if None)."""
        with self._lock:
            self._check_date_reset()
            if agent_id is None:
                return self._daily_spend.get("_global_", 0.0)
            return self._daily_spend.get(agent_id, 0.0)

    def _fire_alert(
        self,
        agent_id: str,
        limit_usd: float,
        spent_usd: float,
        period: str,
        session_id: Optional[str],
        tracer=None,
    ) -> None:
        """Emit a budget.exceeded OTel span and call the webhook."""
        display_id = "global" if agent_id == "_global_" else agent_id
        logger.warning(
            "Budget exceeded: agent=%s period=%s spent=%.4f limit=%.4f",
            display_id, period, spent_usd, limit_usd,
        )

        # ── OTel span ────────────────────────────────────────────────────────
        if tracer is not None:
            try:
                from opentelemetry.trace import StatusCode
                with tracer.start_as_current_span(BUDGET_EVENT_TYPE) as span:
                    span.set_attribute(BUDGET_AGENT_ID, display_id)
                    span.set_attribute(BUDGET_LIMIT_USD, limit_usd)
                    span.set_attribute(BUDGET_SPENT_USD, spent_usd)
                    span.set_attribute(BUDGET_PERIOD, period)
                    if session_id:
                        span.set_attribute("prov.session.id", session_id)
                    if self._cfg.webhook_url:
                        span.set_attribute(BUDGET_WEBHOOK_URL, self._cfg.webhook_url)
                    span.set_status(StatusCode.OK)
            except Exception as exc:
                logger.warning("Failed to emit budget.exceeded span: %s", exc)

        # ── Webhook ───────────────────────────────────────────────────────────
        if self._cfg.webhook_url:
            self._call_webhook(
                url=self._cfg.webhook_url,
                agent_id=display_id,
                limit_usd=limit_usd,
                spent_usd=spent_usd,
                period=period,
                session_id=session_id,
            )

    def _call_webhook(
        self,
        url: str,
        agent_id: str,
        limit_usd: float,
        spent_usd: float,
        period: str,
        session_id: Optional[str],
    ) -> None:
        """POST a JSON alert payload to the configured webhook URL.

        Runs in a daemon thread so it never blocks the proxy response.
        """
        payload = {
            "event": BUDGET_EVENT_TYPE,
            "agent_id": agent_id,
            "limit_usd": limit_usd,
            "spent_usd": spent_usd,
            "period": period,
            "timestamp": time.time(),
        }
        if session_id:
            payload["session_id"] = session_id

        def _post():
            try:
                import urllib.request
                data = json.dumps(payload).encode()
                req = urllib.request.Request(
                    url,
                    data=data,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    logger.info("Budget webhook response: %s", resp.status)
            except Exception as exc:
                logger.warning("Budget webhook call failed: %s", exc)

        t = threading.Thread(target=_post, daemon=True)
        t.start()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_tracker: Optional[BudgetTracker] = None
_tracker_lock = threading.Lock()


def get_tracker() -> BudgetTracker:
    """Return the module-level BudgetTracker, initialising it on first call."""
    global _tracker
    if _tracker is None:
        with _tracker_lock:
            if _tracker is None:
                config = BudgetConfig.from_env_and_file()
                _tracker = BudgetTracker(config)
                if config.is_configured():
                    logger.info(
                        "Budget tracking enabled: global_daily=%s agents=%s webhook=%s",
                        config.global_daily,
                        {k: v.daily for k, v in config.agents.items()},
                        config.webhook_url,
                    )
    return _tracker


def reset_tracker(config: Optional[BudgetConfig] = None) -> None:
    """Reset the singleton tracker (useful in tests)."""
    global _tracker
    with _tracker_lock:
        _tracker = BudgetTracker(config or BudgetConfig()) if config is not None else None
