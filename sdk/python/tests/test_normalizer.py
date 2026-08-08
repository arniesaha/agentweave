"""Tests for claude_code.* -> prov.* normalization (issue #249).

Asserts on the emitted span attributes, never on a status code — the defect
class behind #246/#247/#248 was tests that checked HTTP responses and never
looked at what actually landed in a span.

The fixture is a real OTLP-JSON payload captured from a Claude Code session on
the NAS (2026-08-07), with account PII redacted.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "claude_code_otlp_json.json"


@pytest.fixture
def payload() -> dict:
    return json.loads(FIXTURE.read_text())


@pytest.fixture
def normalized(payload):
    """Normalized payload keyed by span name for convenient assertions."""
    from agentweave.normalizer import normalize_payload

    out = normalize_payload(payload)
    by_name: dict[str, list[dict]] = {}
    for rs in out["resourceSpans"]:
        for ss in rs["scopeSpans"]:
            for span in ss["spans"]:
                attrs = {
                    a["key"]: list(a["value"].values())[0] for a in span["attributes"]
                }
                by_name.setdefault(span["name"], []).append(attrs)
    return by_name


class TestAttributeDecoding:
    """OTLP-JSON encodes int64 as a string in strict protobuf-JSON, but Claude
    Code's exporter emits a number. Both must decode."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ({"stringValue": "hello"}, "hello"),
            ({"intValue": 42}, 42),
            ({"intValue": "42"}, 42),
            ({"boolValue": True}, True),
            ({"doubleValue": 1.5}, 1.5),
        ],
    )
    def test_decodes_value_shapes(self, raw, expected):
        from agentweave.normalizer import _attr_value

        assert _attr_value(raw) == expected

    def test_unknown_shape_returns_none(self):
        from agentweave.normalizer import _attr_value

        assert _attr_value({"bytesValue": "unsupported"}) is None


class TestSpanMapping:
    """One assertion group per row of the design's mapping table."""

    def test_interaction_becomes_agent_turn(self, normalized):
        attrs = normalized["claude_code.interaction"][0]
        assert attrs["prov.activity.type"] == "agent_turn"
        assert attrs["prov.session.id"] == attrs["session.id"]

    def test_tool_becomes_tool_call(self, normalized):
        attrs = normalized["claude_code.tool"][0]
        assert attrs["prov.activity.type"] == "tool_call"
        assert attrs["prov.tool.name"] == attrs["tool_name"]
        assert attrs["prov.tool.call_id"] == attrs["tool_use_id"]

    def test_tool_execution_becomes_tool_execution(self, normalized):
        attrs = normalized["claude_code.tool.execution"][0]
        assert attrs["prov.activity.type"] == "tool_execution"
        assert attrs["outcome"] == "completed"

    def test_blocked_on_user_becomes_permission_wait(self, normalized):
        attrs = normalized["claude_code.tool.blocked_on_user"][0]
        assert attrs["prov.activity.type"] == "permission_wait"

    def test_every_span_carries_harness_and_source(self, normalized):
        for name, spans in normalized.items():
            for attrs in spans:
                assert attrs["prov.harness"] == "claude-code", name
                assert attrs["prov.source"] == "native", name


class TestLLMRequestMapping:
    def test_llm_request_is_not_marked_as_llm_call(self, normalized):
        """The proxy owns llm_call. Marking native as llm_call too would double
        every token and dollar in the dashboard's Overview aggregation."""
        for attrs in normalized["claude_code.llm_request"]:
            assert attrs.get("prov.activity.type") != "llm_call"

    def test_prompt_tokens_sums_uncached_input_and_cache_reads(self, normalized):
        """Native splits input; the proxy reports the sum. Mapping input_tokens
        straight across under-reports by orders of magnitude on a cached turn."""
        attrs = normalized["claude_code.llm_request"][0]
        assert attrs["prov.llm.prompt_tokens"] == (
            attrs["input_tokens"] + attrs["cache_read_tokens"]
        )
        # Guard against the fixture degenerating into a no-cache case, which
        # would make this test pass without exercising the sum.
        assert attrs["cache_read_tokens"] > 0

    def test_token_and_model_fields_carried(self, normalized):
        attrs = normalized["claude_code.llm_request"][0]
        assert attrs["prov.llm.completion_tokens"] == attrs["output_tokens"]
        assert attrs["tokens.cache_read"] == attrs["cache_read_tokens"]
        assert attrs["tokens.cache_write"] == attrs["cache_creation_tokens"]
        assert attrs["prov.llm.model"] == attrs["gen_ai.request.model"]
        assert attrs["prov.llm.provider"] == "anthropic"

    def test_cost_is_computed_not_unknown(self, normalized):
        from agentweave.pricing import UNKNOWN_COST

        attrs = normalized["claude_code.llm_request"][0]
        assert attrs["cost.usd"] != UNKNOWN_COST
        assert attrs["cost.usd"] > 0

    def test_cost_prices_cache_reads_at_the_cache_rate(self, normalized):
        """claude-opus-5: $5.00 input, $25.00 output, $0.50 cache read per 1M.

        Pricing cache reads as full input would inflate a cached turn ~10x.
        """
        attrs = normalized["claude_code.llm_request"][0]
        expected = (
            attrs["input_tokens"] * 5.00
            + attrs["cache_read_tokens"] * 0.50
            + attrs["cache_creation_tokens"] * 6.25
            + attrs["output_tokens"] * 25.00
        ) / 1_000_000
        assert abs(attrs["cost.usd"] - expected) < 1e-9


class TestCacheWritePricing:
    """The captured fixture has cache_creation_tokens == 0, so it cannot
    discriminate how cache writes are priced. Cover that path explicitly."""

    def _llm_span(self, uncached: int, cache_read: int, cache_write: int, out: int) -> dict:
        from agentweave.normalizer import normalize_payload

        doc = {
            "resourceSpans": [{
                "resource": {"attributes": []},
                "scopeSpans": [{"spans": [{
                    "name": "claude_code.llm_request",
                    "attributes": [
                        {"key": "gen_ai.request.model", "value": {"stringValue": "claude-opus-5"}},
                        {"key": "gen_ai.system", "value": {"stringValue": "anthropic"}},
                        {"key": "input_tokens", "value": {"intValue": uncached}},
                        {"key": "cache_read_tokens", "value": {"intValue": cache_read}},
                        {"key": "cache_creation_tokens", "value": {"intValue": cache_write}},
                        {"key": "output_tokens", "value": {"intValue": out}},
                    ],
                }]}],
            }]
        }
        span = normalize_payload(doc)["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
        return {a["key"]: list(a["value"].values())[0] for a in span["attributes"]}

    def test_cache_writes_priced_at_the_write_rate(self):
        """claude-opus-5 cache write is $6.25/M — 1.25x the $5.00 input rate."""
        attrs = self._llm_span(uncached=1000, cache_read=0, cache_write=1_000_000, out=0)

        expected = (1000 * 5.00 + 1_000_000 * 6.25) / 1_000_000
        assert abs(attrs["cost.usd"] - expected) < 1e-9

    def test_uncached_input_still_charged_alongside_both_cache_buckets(self):
        """Regression: passing the uncached portion as compute_cost's
        input_tokens floors it to 0 and silently drops the input charge."""
        attrs = self._llm_span(uncached=2, cache_read=50_000, cache_write=1_000, out=10)

        expected = (2 * 5.00 + 50_000 * 0.50 + 1_000 * 6.25 + 10 * 25.00) / 1_000_000
        assert abs(attrs["cost.usd"] - expected) < 1e-9

    def test_prompt_tokens_excludes_cache_writes(self):
        """prov.llm.prompt_tokens follows the proxy convention: uncached +
        cache reads, not cache writes."""
        attrs = self._llm_span(uncached=2, cache_read=50_000, cache_write=1_000, out=10)

        assert attrs["prov.llm.prompt_tokens"] == 50_002
        assert attrs["tokens.cache_write"] == 1_000


class TestNonNativeSpansUntouched:
    def test_foreign_spans_pass_through_unchanged(self):
        from agentweave.normalizer import normalize_payload

        doc = {
            "resourceSpans": [
                {
                    "resource": {"attributes": []},
                    "scopeSpans": [
                        {
                            "spans": [
                                {
                                    "name": "llm.claude-opus-5",
                                    "attributes": [
                                        {"key": "prov.activity.type",
                                         "value": {"stringValue": "llm_call"}}
                                    ],
                                }
                            ]
                        }
                    ],
                }
            ]
        }

        out = normalize_payload(doc)
        attrs = out["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["attributes"]
        keys = {a["key"] for a in attrs}

        assert keys == {"prov.activity.type"}, "proxy spans must not be rewritten"

    def test_input_payload_is_not_mutated(self, payload):
        from agentweave.normalizer import normalize_payload

        before = json.dumps(payload, sort_keys=True)
        normalize_payload(payload)

        assert json.dumps(payload, sort_keys=True) == before
