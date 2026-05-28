"""Tests for agentweave doctor diagnostics."""

from __future__ import annotations

import importlib.metadata
import json
from urllib.error import URLError

import pytest

pytest.importorskip("typer", reason="CLI deps not installed")


def _healthy_env() -> dict[str, str]:
    return {
        "ANTHROPIC_BASE_URL": "http://localhost:4000",
        "AGENTWEAVE_OTLP_ENDPOINT": "http://localhost:4318",
        "AGENTWEAVE_AGENT_ID": "test-agent",
        "AGENTWEAVE_PROJECT": "test-project",
        "AGENTWEAVE_PROXY_TOKEN": "secret",
    }


def test_doctor_warning_only_has_zero_exit(monkeypatch):
    from typer.testing import CliRunner
    import agentweave.doctor as doctor_module
    from agentweave.cli import app

    monkeypatch.setattr(importlib.metadata, "version", lambda name: "0.3.0")
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("GOOGLE_BASE_URL", raising=False)
    monkeypatch.delenv("GOOGLE_GENAI_BASE_URL", raising=False)
    monkeypatch.delenv("AGENTWEAVE_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("AGENTWEAVE_AGENT_ID", raising=False)
    monkeypatch.delenv("AGENTWEAVE_PROJECT", raising=False)
    monkeypatch.delenv("AGENTWEAVE_PROXY_TOKEN", raising=False)
    monkeypatch.delenv("AGENTWEAVE_PROXY_URL", raising=False)

    checks = doctor_module.run_doctor()
    assert any(check.status == "warn" for check in checks)
    assert not doctor_module.has_failures(checks)

    result = CliRunner().invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "No hard failures found" in result.output


def test_doctor_fails_for_invalid_urls(monkeypatch):
    import agentweave.doctor as doctor_module

    monkeypatch.setattr(importlib.metadata, "version", lambda name: "0.3.0")
    checks = doctor_module.run_doctor(
        env={
            **_healthy_env(),
            "ANTHROPIC_BASE_URL": "localhost:4000",
            "AGENTWEAVE_OTLP_ENDPOINT": "not-a-url",
        }
    )

    failures = [check for check in checks if check.status == "fail"]
    assert {check.name for check in failures} == {
        "provider.anthropic_base_url",
        "otel.endpoint",
    }
    assert doctor_module.has_failures(checks)


def test_doctor_json_exit_nonzero_on_hard_failure(monkeypatch):
    from typer.testing import CliRunner
    from agentweave.cli import app

    monkeypatch.setattr(importlib.metadata, "version", lambda name: "0.3.0")

    result = CliRunner().invoke(
        app,
        ["doctor", "--json"],
        env={
            **_healthy_env(),
            "AGENTWEAVE_OTLP_ENDPOINT": "ftp://collector",
        },
    )

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert payload["summary"]["fail"] == 1
    assert any(check["name"] == "otel.endpoint" for check in payload["checks"])


def test_doctor_healthy_with_proxy_check(monkeypatch):
    import agentweave.doctor as doctor_module

    seen_urls = []

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, limit):
            return b'{"ok": true}'

    monkeypatch.setattr(importlib.metadata, "version", lambda name: "0.3.0")
    def fake_urlopen(url, timeout):
        seen_urls.append(url)
        return FakeResponse()

    monkeypatch.setattr(doctor_module, "urlopen", fake_urlopen)

    checks = doctor_module.run_doctor(
        env=_healthy_env(), check_proxy=True, proxy_url="http://localhost:4000/v1"
    )

    assert {check.status for check in checks} == {"pass"}
    assert not doctor_module.has_failures(checks)
    assert seen_urls == ["http://localhost:4000/health"]


def test_doctor_unreachable_proxy_is_warning(monkeypatch):
    import agentweave.doctor as doctor_module

    monkeypatch.setattr(importlib.metadata, "version", lambda name: "0.3.0")
    monkeypatch.setattr(doctor_module, "urlopen", lambda url, timeout: (_ for _ in ()).throw(URLError("refused")))

    checks = doctor_module.run_doctor(env=_healthy_env(), check_proxy=True)
    proxy_check = next(check for check in checks if check.name == "proxy.health")

    assert proxy_check.status == "warn"
    assert not doctor_module.has_failures(checks)
