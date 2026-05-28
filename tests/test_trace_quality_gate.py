from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "trace_quality_gate.py"
SPEC = importlib.util.spec_from_file_location("trace_quality_gate", SCRIPT)
trace_quality_gate = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = trace_quality_gate
SPEC.loader.exec_module(trace_quality_gate)


SpanRecord = trace_quality_gate.SpanRecord


def test_gate_fails_unattributed_missing_and_suspicious_llm_spans():
    report = trace_quality_gate.evaluate(
        [
            SpanRecord(
                source="fixture",
                service="agentweave-proxy",
                activity_type="llm_call",
                model="claude-sonnet-4-6",
                agent_id="unattributed",
                prompt_tokens="10",
                completion_tokens="2",
                cost_usd="0.01",
            ),
            SpanRecord(
                source="fixture",
                service="agentweave-proxy",
                activity_type="llm_call",
                model="",
                agent_id="nix-v1",
                prompt_tokens="10",
                completion_tokens="2",
                cost_usd="0.01",
            ),
            SpanRecord(
                source="fixture",
                service="agentweave-proxy",
                activity_type="llm_call",
                model="claude-opus-4-7[1m]",
                agent_id="nix-v1",
                prompt_tokens="10",
                completion_tokens="2",
                cost_usd="0.01",
            ),
        ]
    )

    assert report["status"] == "fail"
    assert {item["code"] for item in report["failures"]} == {
        "unattributed_llm_span",
        "missing_llm_model",
        "suspicious_model_label",
    }


def test_blank_model_lifecycle_span_is_info_not_failure():
    report = trace_quality_gate.evaluate(
        [
            SpanRecord(
                source="fixture",
                service="agentweave-proxy",
                span_name="hook.post_tool_use",
                activity_type="hook",
                model="",
                agent_id="nix-v1",
            )
        ]
    )

    assert report["status"] == "pass"
    assert report["summary"]["info"] == 1
    assert report["info"][0]["code"] == "blank_model_lifecycle_span"


def test_tempo_fixture_parsing_preserves_attributes():
    payload = {
        "traces": [
            {
                "traceID": "abc123",
                "rootServiceName": "agentweave-proxy",
                "rootTraceName": "llm.claude-sonnet-4-6",
                "spanSets": [
                    {
                        "spans": [
                            {
                                "attributes": [
                                    {"key": "prov.activity.type", "value": {"stringValue": "llm_call"}},
                                    {"key": "prov.llm.model", "value": {"stringValue": "claude-sonnet-4-6"}},
                                    {"key": "prov.agent.id", "value": {"stringValue": "nix-v1"}},
                                    {"key": "prov.project", "value": {"stringValue": "agentweave"}},
                                    {"key": "prov.llm.prompt_tokens", "value": {"intValue": "4"}},
                                    {"key": "prov.llm.completion_tokens", "value": {"intValue": "1"}},
                                    {"key": "cost.usd", "value": {"doubleValue": 0.001}},
                                ]
                            }
                        ]
                    }
                ],
            }
        ]
    }

    records = trace_quality_gate.parse_tempo_response("fixture", payload)
    assert len(records) == 1
    assert records[0].trace_id == "abc123"
    assert records[0].is_llm()
    assert trace_quality_gate.evaluate(records)["status"] == "pass"


def test_prometheus_fixture_parsing_reads_aggregate_labels():
    payload = {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [
                {
                    "metric": {
                        "service": "agentweave-proxy",
                        "prov_activity_type": "llm_call",
                        "prov_llm_model": "claude-sonnet-4-6",
                        "prov_agent_id": "nix-v1",
                        "prov_project": "agentweave",
                    },
                    "value": [1760000000, "3"],
                }
            ],
        },
    }

    records = trace_quality_gate.parse_prometheus_response("span_inventory", payload)
    assert len(records) == 1
    assert records[0].count == 3
    assert records[0].is_llm()
