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
