from __future__ import annotations

import io
import json
import urllib.error

import pytest

from agentweave.trace_analysis import (
    TempoQueryError,
    TraceQuestionError,
    answer_payload,
    parse_window,
    plan_question,
    query_tempo,
)


def test_plans_session_failure_with_validated_template():
    plan = plan_question("Why did this session fail?", session_id="agent:main:run-42")
    assert plan.family == "failures"
    assert plan.traceql == '{ span.prov.session.id = "agent:main:run-42" && status = error }'


def test_rejects_unsafe_identifier_and_ambiguous_question():
    with pytest.raises(TraceQuestionError, match="session ID"):
        plan_question("Why did it fail?", session_id='x" } || { true')
    with pytest.raises(TraceQuestionError, match="exactly one"):
        plan_question("Which agent errors were slow?")


def test_rejects_unsupported_question_and_out_of_range_window():
    with pytest.raises(TraceQuestionError, match="supported family"):
        plan_question("Tell me something interesting")
    assert parse_window("6h") == 21600
    with pytest.raises(TraceQuestionError, match="between 1m and 7d"):
        parse_window("8d")


def test_query_tempo_returns_only_valid_trace_citations(monkeypatch):
    payload = {
        "traces": [
            {
                "traceID": "a" * 32,
                "rootServiceName": "agentweave-proxy",
                "rootTraceName": "anthropic.messages",
                "durationMs": 12.5,
                "startTimeUnixNano": "123",
                "spanSets": [{"spans": [{"attributes": [{
                    "key": "prov.agent.id", "value": {"stringValue": "nix"}
                }]}]}],
            },
            {"traceID": "not-a-trace"},
        ]
    }

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        return Response(json.dumps(payload).encode())

    monkeypatch.setattr("agentweave.trace_analysis.urllib.request.urlopen", fake_urlopen)
    plan = plan_question("Show the slowest calls")
    citations = query_tempo(
        "http://tempo:3200", plan, window_seconds=3600, limit=5, now=10000
    )
    assert [item.trace_id for item in citations] == ["a" * 32]
    assert citations[0].agent_id == "nix"
    assert "limit=5" in captured["url"]
    assert "start=6400" in captured["url"]


def test_query_tempo_distinguishes_backend_failure(monkeypatch):
    def fail(*args, **kwargs):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr("agentweave.trace_analysis.urllib.request.urlopen", fail)
    with pytest.raises(TempoQueryError, match="offline"):
        query_tempo(
            "http://tempo:3200",
            plan_question("Show expensive calls"),
            window_seconds=3600,
            limit=5,
        )


def test_empty_answer_is_not_reported_as_failure_and_citations_are_complete():
    plan = plan_question("Compare agent error rates")
    empty = answer_payload(plan, [])
    assert empty["summary"].startswith("No matching data")
    assert empty["citations"] == []
    assert empty["traceql"] == plan.traceql
