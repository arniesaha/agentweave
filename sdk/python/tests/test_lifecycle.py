"""Tests for local proxy lifecycle helpers and CLI commands."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("typer", reason="CLI deps not installed")


def test_start_proxy_process_writes_state(monkeypatch, tmp_path):
    import agentweave.lifecycle as lifecycle

    launched = {}

    class FakeProcess:
        pid = 12345

    def fake_popen(command, **kwargs):
        launched["command"] = command
        launched["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setenv(lifecycle.STATE_ENV_VAR, str(tmp_path))
    monkeypatch.setattr(lifecycle.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(lifecycle, "is_process_running", lambda pid: False)

    state = lifecycle.start_proxy_process(
        host="127.0.0.1",
        port=4100,
        endpoint="http://localhost:4318",
        agent_id="local-dev",
        capture_prompts=True,
        auth_token="secret",
    )

    assert state.pid == 12345
    assert state.url == "http://localhost:4100"
    assert launched["command"][:4] == [
        lifecycle.sys.executable,
        "-m",
        "agentweave.cli",
        "proxy",
    ]
    assert launched["command"][4:] == [
        "start",
        "--host",
        "127.0.0.1",
        "--port",
        "4100",
        "--endpoint",
        "http://localhost:4318",
        "--agent-id",
        "local-dev",
        "--capture-prompts",
        "--auth-token",
        "secret",
    ]
    assert lifecycle.state_file().exists()

    payload = json.loads(lifecycle.state_file().read_text())
    assert payload["pid"] == 12345
    assert payload["port"] == 4100
    assert payload["log_file"].endswith("proxy.log")


def test_start_proxy_process_refuses_running_state(monkeypatch, tmp_path):
    import agentweave.lifecycle as lifecycle

    monkeypatch.setenv(lifecycle.STATE_ENV_VAR, str(tmp_path))
    lifecycle.write_state(
        lifecycle.ProxyState(
            pid=999,
            host="127.0.0.1",
            port=4000,
            url="http://localhost:4000",
            command=["agentweave", "proxy", "start"],
            log_file=str(tmp_path / "proxy.log"),
            started_at=1.0,
        )
    )
    monkeypatch.setattr(lifecycle, "is_process_running", lambda pid: True)

    with pytest.raises(RuntimeError, match="already running"):
        lifecycle.start_proxy_process(host="127.0.0.1", port=4000)


def test_stop_proxy_process_clears_stale_state(monkeypatch, tmp_path):
    import agentweave.lifecycle as lifecycle

    monkeypatch.setenv(lifecycle.STATE_ENV_VAR, str(tmp_path))
    lifecycle.write_state(
        lifecycle.ProxyState(
            pid=999,
            host="127.0.0.1",
            port=4000,
            url="http://localhost:4000",
            command=["agentweave", "proxy", "start"],
            log_file=str(tmp_path / "proxy.log"),
            started_at=1.0,
        )
    )
    monkeypatch.setattr(lifecycle, "is_process_running", lambda pid: False)

    result, state = lifecycle.stop_proxy_process()

    assert result == "stale"
    assert state is not None
    assert state.pid == 999
    assert not lifecycle.state_file().exists()


def test_status_cli_json_uses_state_dir(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    import agentweave.lifecycle as lifecycle
    from agentweave.cli import app

    monkeypatch.setenv(lifecycle.STATE_ENV_VAR, str(tmp_path))
    lifecycle.write_state(
        lifecycle.ProxyState(
            pid=123,
            host="127.0.0.1",
            port=4000,
            url="http://localhost:4000",
            command=["agentweave", "proxy", "start"],
            log_file=str(tmp_path / "proxy.log"),
            started_at=1.0,
        )
    )
    monkeypatch.setattr(lifecycle, "is_process_running", lambda pid: True)

    result = CliRunner().invoke(app, ["status", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["status"] == "running"
    assert payload["proxy"]["pid"] == 123


def test_init_cli_starts_proxy_and_prints_next_steps(monkeypatch):
    from typer.testing import CliRunner

    import agentweave.lifecycle as lifecycle
    from agentweave.cli import app

    state = lifecycle.ProxyState(
        pid=123,
        host="127.0.0.1",
        port=4100,
        url="http://localhost:4100",
        command=["agentweave", "proxy", "start"],
        log_file="/tmp/agentweave.log",
        started_at=1.0,
    )
    started = {}
    monkeypatch.setattr(lifecycle, "current_status", lambda: ("stopped", None))

    def fake_start_proxy_process(**kwargs):
        started.update(kwargs)
        return state

    monkeypatch.setattr(lifecycle, "start_proxy_process", fake_start_proxy_process)

    result = CliRunner().invoke(
        app,
        ["init", "--port", "4100", "--endpoint", "http://tempo:4318"],
    )

    assert result.exit_code == 0
    assert started["port"] == 4100
    assert started["endpoint"] == "http://tempo:4318"
    assert "AgentWeave initialized" in result.output
    assert "http://localhost:4100" in result.output
    assert "native OpenTelemetry" in result.output


def test_init_cli_is_idempotent_when_proxy_is_running(monkeypatch):
    from typer.testing import CliRunner

    import agentweave.lifecycle as lifecycle
    from agentweave.cli import app

    state = lifecycle.ProxyState(
        pid=123,
        host="127.0.0.1",
        port=4000,
        url="http://localhost:4000",
        command=["agentweave", "proxy", "start"],
        log_file="/tmp/agentweave.log",
        started_at=1.0,
    )
    monkeypatch.setattr(lifecycle, "current_status", lambda: ("running", state))
    monkeypatch.setattr(
        lifecycle,
        "start_proxy_process",
        lambda **kwargs: pytest.fail("init should reuse the running proxy"),
    )

    result = CliRunner().invoke(app, ["init"])

    assert result.exit_code == 0
    assert "proxy already running" in result.output
