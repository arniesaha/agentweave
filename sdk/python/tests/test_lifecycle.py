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
