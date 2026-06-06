"""Tests for agentweave openclaw install/uninstall."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("typer", reason="CLI deps not installed")


def test_default_plugin_dir_is_user_plugins_sibling():
    from agentweave.openclaw_install import default_plugin_dir

    config_path = Path("/home/x/.openclaw/openclaw.json")
    assert default_plugin_dir(config_path) == Path(
        "/home/x/.openclaw/user-plugins/agentweave-bridge"
    )


def test_resolve_config_values_precedence_flag_over_env():
    from agentweave.openclaw_install import resolve_config_values

    env = {
        "AGENTWEAVE_PROXY_URL": "http://env-proxy:4000",
        "AGENTWEAVE_OTLP_ENDPOINT": "http://env-otlp:4318",
        "AGENTWEAVE_AGENT_ID": "env-agent",
        "AGENTWEAVE_PROJECT": "env-project",
    }
    values = resolve_config_values(
        env,
        proxy_url="http://flag-proxy:4000",
        otlp_endpoint=None,
        agent_id=None,
        project=None,
    )
    assert values == {
        "proxyUrl": "http://flag-proxy:4000",
        "otlpEndpoint": "http://env-otlp:4318",
        "agentId": "env-agent",
        "project": "env-project",
    }


def test_resolve_config_values_omits_unset_keys():
    from agentweave.openclaw_install import resolve_config_values

    values = resolve_config_values(
        {}, proxy_url=None, otlp_endpoint=None, agent_id=None, project=None
    )
    assert values == {}


def test_resolve_config_path_explicit_wins_over_env():
    from agentweave.openclaw_install import resolve_config_path

    result = resolve_config_path(
        {"OPENCLAW_CONFIG_PATH": "/env/openclaw.json"}, "/explicit/openclaw.json"
    )
    assert result == Path("/explicit/openclaw.json")


def test_resolve_config_path_blank_explicit_falls_through_to_env():
    from agentweave.openclaw_install import resolve_config_path

    result = resolve_config_path({"OPENCLAW_CONFIG_PATH": "/env/openclaw.json"}, "   ")
    assert result == Path("/env/openclaw.json")


def test_resolve_config_path_raises_when_unresolvable():
    from agentweave.openclaw_install import OpenClawInstallError, resolve_config_path

    with pytest.raises(OpenClawInstallError):
        resolve_config_path({}, None)


def _write_config(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def test_install_writes_files_and_entry(tmp_path):
    from agentweave import openclaw_install as oi

    config_path = tmp_path / ".openclaw" / "openclaw.json"
    config_path.parent.mkdir(parents=True)
    _write_config(config_path, {"plugins": {"entries": {}}})

    # Fake the packaged dist so the test does not depend on a built wheel.
    dist = tmp_path / "dist"
    dist.mkdir()
    for name in oi.BUNDLE_FILES:
        (dist / name).write_text(f"// {name}", encoding="utf-8")

    result = oi.install(
        {},
        dist_dir=dist,
        config_path=str(config_path),
        proxy_url="http://p:4000",
        otlp_endpoint="http://o:4318",
        agent_id="host-a",
        project="proj-a",
    )

    assert result.created_entry is True
    plugin_dir = result.plugin_dir
    assert (plugin_dir / "index.js").exists()
    assert (plugin_dir / "openclaw.plugin.json").exists()
    assert (plugin_dir / "package.json").exists()

    config = json.loads(config_path.read_text())
    entry = config["plugins"]["entries"]["agentweave-bridge"]
    assert entry["path"] == str(plugin_dir)
    assert entry["config"] == {
        "proxyUrl": "http://p:4000",
        "otlpEndpoint": "http://o:4318",
        "agentId": "host-a",
        "project": "proj-a",
        "enabled": True,
    }


def test_install_is_idempotent_and_backs_up(tmp_path):
    from agentweave import openclaw_install as oi

    config_path = tmp_path / "openclaw.json"
    _write_config(config_path, {"plugins": {"entries": {}}, "other": "keep"})
    dist = tmp_path / "dist"
    dist.mkdir()
    for name in oi.BUNDLE_FILES:
        (dist / name).write_text("x", encoding="utf-8")

    oi.install({}, dist_dir=dist, config_path=str(config_path), agent_id="a")
    result2 = oi.install({}, dist_dir=dist, config_path=str(config_path), agent_id="a")

    config = json.loads(config_path.read_text())
    assert config["other"] == "keep"
    assert list(config["plugins"]["entries"].keys()) == ["agentweave-bridge"]
    assert result2.created_entry is False
    assert (config_path.parent / "openclaw.json.bak").exists()


def test_install_preserves_user_config_unless_force(tmp_path):
    from agentweave import openclaw_install as oi

    config_path = tmp_path / "openclaw.json"
    _write_config(
        config_path,
        {
            "plugins": {
                "entries": {
                    "agentweave-bridge": {
                        "path": "/old",
                        "config": {"agentId": "hand-set", "extra": "keep"},
                    }
                }
            }
        },
    )
    dist = tmp_path / "dist"
    dist.mkdir()
    for name in oi.BUNDLE_FILES:
        (dist / name).write_text("x", encoding="utf-8")

    oi.install({}, dist_dir=dist, config_path=str(config_path), agent_id="new")
    entry = json.loads(config_path.read_text())["plugins"]["entries"]["agentweave-bridge"]
    assert entry["config"]["agentId"] == "hand-set"
    assert entry["config"]["extra"] == "keep"
    assert entry["path"] != "/old"

    oi.install({}, dist_dir=dist, config_path=str(config_path), agent_id="new", force=True)
    entry = json.loads(config_path.read_text())["plugins"]["entries"]["agentweave-bridge"]
    assert entry["config"]["agentId"] == "new"
    assert entry["config"]["extra"] == "keep"


def test_install_missing_config_errors(tmp_path):
    from agentweave import openclaw_install as oi

    dist = tmp_path / "dist"
    dist.mkdir()
    for name in oi.BUNDLE_FILES:
        (dist / name).write_text("x", encoding="utf-8")

    with pytest.raises(oi.OpenClawInstallError):
        oi.install(
            {}, dist_dir=dist, config_path=str(tmp_path / "nope.json"), agent_id="a"
        )


def test_install_enabled_false_overrides_existing(tmp_path):
    from agentweave import openclaw_install as oi

    config_path = tmp_path / "openclaw.json"
    dist = tmp_path / "dist"
    dist.mkdir()
    for name in oi.BUNDLE_FILES:
        (dist / name).write_text("x", encoding="utf-8")
    _write_config(config_path, {"plugins": {"entries": {}}})

    oi.install({}, dist_dir=dist, config_path=str(config_path), agent_id="a", enabled=True)
    oi.install({}, dist_dir=dist, config_path=str(config_path), agent_id="a", enabled=False)

    entry = json.loads(config_path.read_text())["plugins"]["entries"]["agentweave-bridge"]
    assert entry["config"]["enabled"] is False


def test_install_raises_on_malformed_entries(tmp_path):
    from agentweave import openclaw_install as oi

    config_path = tmp_path / "openclaw.json"
    dist = tmp_path / "dist"
    dist.mkdir()
    for name in oi.BUNDLE_FILES:
        (dist / name).write_text("x", encoding="utf-8")
    _write_config(config_path, {"plugins": {"entries": "not-a-dict"}})

    with pytest.raises(oi.OpenClawInstallError):
        oi.install({}, dist_dir=dist, config_path=str(config_path), agent_id="a")


def test_uninstall_removes_entry_and_optionally_purges(tmp_path):
    from agentweave import openclaw_install as oi

    config_path = tmp_path / "openclaw.json"
    dist = tmp_path / "dist"
    dist.mkdir()
    for name in oi.BUNDLE_FILES:
        (dist / name).write_text("x", encoding="utf-8")
    _write_config(config_path, {"plugins": {"entries": {}}, "keep": 1})

    installed = oi.install({}, dist_dir=dist, config_path=str(config_path), agent_id="a")
    plugin_dir = installed.plugin_dir

    result = oi.uninstall({}, config_path=str(config_path), purge=True)

    config = json.loads(config_path.read_text())
    assert "agentweave-bridge" not in config["plugins"]["entries"]
    assert config["keep"] == 1
    assert result.removed_entry is True
    assert not plugin_dir.exists()  # purged


def test_uninstall_missing_entry_is_noop(tmp_path):
    from agentweave import openclaw_install as oi

    config_path = tmp_path / "openclaw.json"
    _write_config(config_path, {"plugins": {"entries": {}}})
    mtime_before = config_path.stat().st_mtime

    result = oi.uninstall({}, config_path=str(config_path))

    assert result.removed_entry is False
    assert config_path.stat().st_mtime == mtime_before
    assert not config_path.with_suffix(".json.bak").exists()


def test_uninstall_purge_refuses_unexpected_basename(tmp_path):
    from agentweave import openclaw_install as oi

    config_path = tmp_path / "openclaw.json"
    danger = tmp_path / "not-the-bridge"
    danger.mkdir()
    (danger / "important.txt").write_text("keep me", encoding="utf-8")
    _write_config(
        config_path,
        {"plugins": {"entries": {"agentweave-bridge": {"path": str(danger), "config": {}}}}},
    )

    with pytest.raises(oi.OpenClawInstallError):
        oi.uninstall({}, config_path=str(config_path), purge=True)

    # The config was not mutated and the directory was not deleted.
    config = json.loads(config_path.read_text())
    assert "agentweave-bridge" in config["plugins"]["entries"]
    assert danger.exists()
    assert (danger / "important.txt").exists()


def test_cli_install_reports_and_writes_entry(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from agentweave import openclaw_install as oi
    from agentweave.cli import app

    config_path = tmp_path / "openclaw.json"
    _write_config(config_path, {"plugins": {"entries": {}}})
    dist = tmp_path / "dist"
    dist.mkdir()
    for name in oi.BUNDLE_FILES:
        (dist / name).write_text("x", encoding="utf-8")
    monkeypatch.setattr(oi, "resolve_packaged_dist", lambda: dist)

    result = CliRunner().invoke(
        app,
        [
            "openclaw", "install",
            "--openclaw-config", str(config_path),
            "--agent-id", "cli-host",
            "--no-restart",
        ],
    )
    assert result.exit_code == 0, result.output
    entry = json.loads(config_path.read_text())["plugins"]["entries"]["agentweave-bridge"]
    assert entry["config"]["agentId"] == "cli-host"
    assert "openclaw gateway restart" in result.output


def test_cli_install_missing_config_exits_nonzero(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from agentweave import openclaw_install as oi
    from agentweave.cli import app

    dist = tmp_path / "dist"
    dist.mkdir()
    for name in oi.BUNDLE_FILES:
        (dist / name).write_text("x", encoding="utf-8")
    monkeypatch.setattr(oi, "resolve_packaged_dist", lambda: dist)

    result = CliRunner().invoke(
        app, ["openclaw", "install", "--openclaw-config", str(tmp_path / "nope.json")]
    )
    assert result.exit_code != 0
    assert "not found" in result.output.lower()


def test_cli_uninstall_noop_when_absent(tmp_path):
    from typer.testing import CliRunner
    from agentweave.cli import app

    config_path = tmp_path / "openclaw.json"
    _write_config(config_path, {"plugins": {"entries": {}}})

    result = CliRunner().invoke(
        app, ["openclaw", "uninstall", "--openclaw-config", str(config_path)]
    )
    assert result.exit_code == 0, result.output


def test_cli_install_restart_handles_missing_openclaw(tmp_path, monkeypatch):
    import subprocess

    from typer.testing import CliRunner
    from agentweave import openclaw_install as oi
    from agentweave.cli import app

    config_path = tmp_path / "openclaw.json"
    _write_config(config_path, {"plugins": {"entries": {}}})
    dist = tmp_path / "dist"
    dist.mkdir()
    for name in oi.BUNDLE_FILES:
        (dist / name).write_text("x", encoding="utf-8")
    monkeypatch.setattr(oi, "resolve_packaged_dist", lambda: dist)

    def _boom(*args, **kwargs):
        raise FileNotFoundError("openclaw")

    monkeypatch.setattr(subprocess, "run", _boom)

    result = CliRunner().invoke(
        app,
        [
            "openclaw", "install",
            "--openclaw-config", str(config_path),
            "--agent-id", "j",
            "--restart",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "not found on PATH" in result.output
