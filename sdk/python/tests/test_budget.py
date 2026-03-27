"""Tests for AgentWeave budget tracking (issue #110)."""

from __future__ import annotations

import json
import os
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from agentweave.budget import (
    AgentBudget,
    BudgetConfig,
    BudgetTracker,
    BUDGET_EVENT_TYPE,
    BUDGET_AGENT_ID,
    BUDGET_LIMIT_USD,
    BUDGET_SPENT_USD,
    BUDGET_PERIOD,
    reset_tracker,
    get_tracker,
)


# ---------------------------------------------------------------------------
# BudgetConfig
# ---------------------------------------------------------------------------

class TestBudgetConfig:
    def test_empty_config_not_configured(self):
        cfg = BudgetConfig()
        assert not cfg.is_configured()

    def test_global_daily_marks_configured(self):
        cfg = BudgetConfig(global_daily=5.00)
        assert cfg.is_configured()

    def test_agents_marks_configured(self):
        cfg = BudgetConfig(agents={"nix-v1": AgentBudget(daily=2.00)})
        assert cfg.is_configured()

    def test_from_env_global_daily(self, monkeypatch):
        monkeypatch.setenv("AGENTWEAVE_BUDGET_GLOBAL_DAILY", "3.50")
        monkeypatch.delenv("AGENTWEAVE_BUDGET_AGENTS", raising=False)
        monkeypatch.delenv("AGENTWEAVE_BUDGET_WEBHOOK_URL", raising=False)
        # Point at non-existent file to skip file loading
        monkeypatch.setenv("AGENTWEAVE_BUDGET_CONFIG_PATH", "/tmp/no_such_file_aw_budget.json")
        cfg = BudgetConfig.from_env_and_file()
        assert cfg.global_daily == 3.50

    def test_from_env_agents(self, monkeypatch):
        monkeypatch.setenv("AGENTWEAVE_BUDGET_AGENTS", "nix-v1=5.00,max-v1=2.50")
        monkeypatch.delenv("AGENTWEAVE_BUDGET_GLOBAL_DAILY", raising=False)
        monkeypatch.delenv("AGENTWEAVE_BUDGET_WEBHOOK_URL", raising=False)
        monkeypatch.setenv("AGENTWEAVE_BUDGET_CONFIG_PATH", "/tmp/no_such_file_aw_budget.json")
        cfg = BudgetConfig.from_env_and_file()
        assert cfg.agents["nix-v1"].daily == 5.00
        assert cfg.agents["max-v1"].daily == 2.50

    def test_from_env_webhook(self, monkeypatch):
        monkeypatch.setenv("AGENTWEAVE_BUDGET_WEBHOOK_URL", "https://hooks.example.com/alert")
        monkeypatch.delenv("AGENTWEAVE_BUDGET_GLOBAL_DAILY", raising=False)
        monkeypatch.delenv("AGENTWEAVE_BUDGET_AGENTS", raising=False)
        monkeypatch.setenv("AGENTWEAVE_BUDGET_CONFIG_PATH", "/tmp/no_such_file_aw_budget.json")
        cfg = BudgetConfig.from_env_and_file()
        assert cfg.webhook_url == "https://hooks.example.com/alert"

    def test_from_json_file(self, tmp_path):
        config_file = tmp_path / "budget.json"
        config_file.write_text(json.dumps({
            "global_daily": 10.00,
            "agents": {"nix-v1": {"daily": 5.00}},
            "webhook_url": "https://example.com/wh",
        }))
        cfg = BudgetConfig.from_env_and_file.__func__(
            BudgetConfig  # type: ignore[attr-defined]
        ) if False else _load_from_file(str(config_file))
        assert cfg.global_daily == 10.00
        assert cfg.agents["nix-v1"].daily == 5.00
        assert cfg.webhook_url == "https://example.com/wh"

    def test_save_and_reload(self, tmp_path):
        path = str(tmp_path / "budget.json")
        cfg = BudgetConfig(
            global_daily=8.00,
            agents={"nix-v1": AgentBudget(daily=4.00)},
            webhook_url="https://wh.example.com",
        )
        cfg.save(path)
        assert os.path.isfile(path)
        with open(path) as fh:
            data = json.load(fh)
        assert data["global_daily"] == 8.00
        assert data["agents"]["nix-v1"]["daily"] == 4.00
        assert data["webhook_url"] == "https://wh.example.com"


def _load_from_file(path: str) -> BudgetConfig:
    """Helper — load BudgetConfig from a specific file path."""
    cfg = BudgetConfig()
    with open(path) as fh:
        data = json.load(fh)
    if "global_daily" in data:
        cfg.global_daily = float(data["global_daily"])
    if "agents" in data:
        for aid, limits in data["agents"].items():
            cfg.agents[aid] = AgentBudget(daily=float(limits.get("daily", 0)) or None)
    if "webhook_url" in data:
        cfg.webhook_url = str(data["webhook_url"])
    return cfg


# ---------------------------------------------------------------------------
# BudgetTracker — spend accumulation and threshold detection
# ---------------------------------------------------------------------------

class TestBudgetTracker:
    def _make_tracker(self, global_daily=None, agents=None, webhook_url=None):
        cfg = BudgetConfig(
            global_daily=global_daily,
            agents=agents or {},
            webhook_url=webhook_url,
        )
        return BudgetTracker(cfg)

    def test_no_limits_no_alert(self):
        tracker = self._make_tracker()
        alerts = []
        with patch.object(tracker, "_fire_alert", side_effect=lambda **kw: alerts.append(kw)):
            tracker.record_cost("nix-v1", 100.00)
        assert alerts == []

    def test_zero_cost_ignored(self):
        tracker = self._make_tracker(global_daily=1.00)
        alerts = []
        with patch.object(tracker, "_fire_alert", side_effect=lambda **kw: alerts.append(kw)):
            tracker.record_cost("nix-v1", 0.0)
        assert alerts == []

    def test_agent_daily_limit_fires_on_first_breach(self):
        tracker = self._make_tracker(agents={"nix-v1": AgentBudget(daily=1.00)})
        alerts = []
        with patch.object(tracker, "_fire_alert", side_effect=lambda **kw: alerts.append(kw)):
            tracker.record_cost("nix-v1", 0.50)
            assert alerts == []  # under limit
            tracker.record_cost("nix-v1", 0.60)  # cumulative 1.10 > 1.00
            assert len(alerts) == 1
            assert alerts[0]["agent_id"] == "nix-v1"
            assert alerts[0]["period"] == "daily"
            assert alerts[0]["spent_usd"] >= 1.00

    def test_agent_daily_limit_fires_only_once(self):
        tracker = self._make_tracker(agents={"nix-v1": AgentBudget(daily=1.00)})
        alerts = []
        with patch.object(tracker, "_fire_alert", side_effect=lambda **kw: alerts.append(kw)):
            tracker.record_cost("nix-v1", 2.00)  # exceeds limit on first call
            tracker.record_cost("nix-v1", 2.00)  # already alerted, no second fire
            assert len(alerts) == 1

    def test_global_daily_limit(self):
        tracker = self._make_tracker(global_daily=1.00)
        alerts = []
        with patch.object(tracker, "_fire_alert", side_effect=lambda **kw: alerts.append(kw)):
            tracker.record_cost("nix-v1", 0.50)
            tracker.record_cost("max-v1", 0.60)  # total 1.10 > 1.00
            assert len(alerts) == 1
            assert alerts[0]["agent_id"] == "_global_"

    def test_both_limits_can_fire_independently(self):
        tracker = self._make_tracker(
            global_daily=2.00,
            agents={"nix-v1": AgentBudget(daily=1.00)},
        )
        alerts = []
        with patch.object(tracker, "_fire_alert", side_effect=lambda **kw: alerts.append(kw)):
            tracker.record_cost("nix-v1", 1.50)  # agent limit exceeded (1.50 > 1.00)
            tracker.record_cost("nix-v1", 1.00)  # global limit exceeded (2.50 > 2.00)
            assert len(alerts) == 2
            agent_ids = {a["agent_id"] for a in alerts}
            assert "nix-v1" in agent_ids
            assert "_global_" in agent_ids

    def test_get_spent(self):
        tracker = self._make_tracker(global_daily=100.00)
        tracker.record_cost("nix-v1", 1.50)
        tracker.record_cost("max-v1", 0.25)
        assert tracker.get_spent("nix-v1") == pytest.approx(1.50)
        assert tracker.get_spent("max-v1") == pytest.approx(0.25)
        assert tracker.get_spent() == pytest.approx(1.75)  # global

    def test_multiple_agents_independent(self):
        tracker = self._make_tracker(agents={
            "agent-a": AgentBudget(daily=1.00),
            "agent-b": AgentBudget(daily=1.00),
        })
        alerts = []
        with patch.object(tracker, "_fire_alert", side_effect=lambda **kw: alerts.append(kw)):
            tracker.record_cost("agent-a", 1.50)  # agent-a fires
            assert len(alerts) == 1
            tracker.record_cost("agent-b", 0.50)  # agent-b still under limit
            assert len(alerts) == 1
            tracker.record_cost("agent-b", 1.00)  # agent-b now over limit
            assert len(alerts) == 2


# ---------------------------------------------------------------------------
# BudgetTracker._fire_alert — OTel span emission
# ---------------------------------------------------------------------------

class TestBudgetAlert:
    def test_fire_alert_emits_otel_span(self):
        cfg = BudgetConfig(agents={"nix-v1": AgentBudget(daily=1.00)})
        tracker = BudgetTracker(cfg)

        mock_span = MagicMock()
        mock_span.__enter__ = MagicMock(return_value=mock_span)
        mock_span.__exit__ = MagicMock(return_value=False)

        mock_tracer = MagicMock()
        mock_tracer.start_as_current_span.return_value = mock_span

        tracker._fire_alert(
            agent_id="nix-v1",
            limit_usd=1.00,
            spent_usd=1.50,
            period="daily",
            session_id="sess-123",
            tracer=mock_tracer,
        )

        mock_tracer.start_as_current_span.assert_called_once_with(BUDGET_EVENT_TYPE)
        mock_span.set_attribute.assert_any_call(BUDGET_AGENT_ID, "nix-v1")
        mock_span.set_attribute.assert_any_call(BUDGET_LIMIT_USD, 1.00)
        mock_span.set_attribute.assert_any_call(BUDGET_SPENT_USD, 1.50)
        mock_span.set_attribute.assert_any_call(BUDGET_PERIOD, "daily")
        mock_span.set_attribute.assert_any_call("prov.session.id", "sess-123")

    def test_fire_alert_no_tracer_is_safe(self):
        cfg = BudgetConfig(agents={"nix-v1": AgentBudget(daily=1.00)})
        tracker = BudgetTracker(cfg)
        # Should not raise
        tracker._fire_alert(
            agent_id="nix-v1",
            limit_usd=1.00,
            spent_usd=1.50,
            period="daily",
            session_id=None,
            tracer=None,
        )

    def test_webhook_called_in_background_thread(self):
        cfg = BudgetConfig(
            agents={"nix-v1": AgentBudget(daily=1.00)},
            webhook_url="https://hooks.example.com/alert",
        )
        tracker = BudgetTracker(cfg)
        call_log = []

        def mock_post():
            call_log.append(True)

        with patch.object(tracker, "_call_webhook") as mock_webhook:
            tracker._fire_alert(
                agent_id="nix-v1",
                limit_usd=1.00,
                spent_usd=1.50,
                period="daily",
                session_id=None,
                tracer=None,
            )
            # _call_webhook should be called
            assert mock_webhook.called
            kwargs = mock_webhook.call_args[1]
            assert kwargs["url"] == "https://hooks.example.com/alert"
            assert kwargs["agent_id"] == "nix-v1"
            assert kwargs["limit_usd"] == 1.00
            assert kwargs["spent_usd"] == 1.50


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

class TestSingleton:
    def test_get_tracker_returns_same_instance(self):
        reset_tracker(BudgetConfig())
        t1 = get_tracker()
        t2 = get_tracker()
        assert t1 is t2

    def test_reset_tracker(self):
        cfg = BudgetConfig(global_daily=5.00)
        reset_tracker(cfg)
        tracker = get_tracker()
        assert tracker._cfg.global_daily == 5.00
        # Reset without config → fresh None tracker on next call
        reset_tracker()
        # After reset, get_tracker re-initialises from env
        new_tracker = get_tracker()
        assert new_tracker is not tracker
