"""Tests for the normalizer OTLP receiver (issue #249).

The service sits in the telemetry path between Claude Code and the collector.
Its failure modes matter more than its happy path: a silent drop loses spans
with no signal, and a 2xx on a failed forward tells the OTel exporter not to
retry. Both are asserted here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="proxy deps not installed")

FIXTURE = Path(__file__).parent / "fixtures" / "claude_code_otlp_json.json"


@pytest.fixture
def payload() -> dict:
    return json.loads(FIXTURE.read_text())


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from agentweave.normalizer_service import app

    return TestClient(app)


class _Forwarded:
    """Records what the service forwarded upstream."""

    def __init__(self, status: int = 200, raise_exc: Exception | None = None):
        self.status = status
        self.raise_exc = raise_exc
        self.calls: list[tuple[str, dict]] = []

    def install(self, monkeypatch):
        import agentweave.normalizer_service as svc

        async def _fake_forward(url: str, document: dict) -> int:
            self.calls.append((url, document))
            if self.raise_exc is not None:
                raise self.raise_exc
            return self.status

        monkeypatch.setattr(svc, "_forward", _fake_forward)
        return self


class TestTraceIngest:
    def test_forwards_normalized_spans(self, client, payload, monkeypatch):
        sink = _Forwarded().install(monkeypatch)

        response = client.post("/v1/traces", json=payload)

        assert response.status_code == 200
        assert len(sink.calls) == 1
        _, forwarded = sink.calls[0]
        attrs = {
            a["key"]
            for rs in forwarded["resourceSpans"]
            for ss in rs["scopeSpans"]
            for s in ss["spans"]
            for a in s["attributes"]
        }
        assert "prov.activity.type" in attrs
        assert "prov.source" in attrs

    def test_upstream_failure_returns_5xx_so_the_exporter_retries(
        self, client, payload, monkeypatch
    ):
        """A 2xx here would tell the OTel exporter the batch landed. It didn't."""
        _Forwarded(raise_exc=RuntimeError("collector unreachable")).install(monkeypatch)

        response = client.post("/v1/traces", json=payload)

        assert response.status_code >= 500

    def test_upstream_error_status_is_propagated(self, client, payload, monkeypatch):
        _Forwarded(status=503).install(monkeypatch)

        response = client.post("/v1/traces", json=payload)

        assert response.status_code >= 500

    def test_malformed_json_is_rejected_not_forwarded(self, client, monkeypatch):
        sink = _Forwarded().install(monkeypatch)

        response = client.post(
            "/v1/traces",
            content=b"{not json",
            headers={"content-type": "application/json"},
        )

        assert response.status_code == 400
        assert sink.calls == []

    def test_protobuf_payload_is_rejected_with_a_clear_status(self, client, monkeypatch):
        """The service only speaks OTLP-JSON. A client configured for protobuf
        should get an actionable status, not a parse crash."""
        sink = _Forwarded().install(monkeypatch)

        response = client.post(
            "/v1/traces",
            content=b"\x0a\x0b\x08\x01",
            headers={"content-type": "application/x-protobuf"},
        )

        assert response.status_code == 415
        assert sink.calls == []

    def test_empty_payload_is_accepted(self, client, monkeypatch):
        sink = _Forwarded().install(monkeypatch)

        response = client.post("/v1/traces", json={"resourceSpans": []})

        assert response.status_code == 200
        assert len(sink.calls) == 1


class TestHealth:
    def test_health_reports_version_and_collector(self, client):
        response = client.get("/health")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["version"]
        assert body["collector_endpoint"]
