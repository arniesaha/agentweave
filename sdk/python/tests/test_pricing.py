"""Tests for USD cost computation (pricing.py) and cost.usd span attribute emission."""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# pricing.compute_cost unit tests
# ---------------------------------------------------------------------------

class TestComputeCost:
    """Unit tests for the compute_cost function."""

    def test_known_model_exact_match(self):
        from agentweave.pricing import compute_cost
        # claude-sonnet-4-6: $3.00 input / $15.00 output per 1M tokens
        cost = compute_cost("claude-sonnet-4-6", input_tokens=1_000_000, output_tokens=0)
        assert abs(cost - 3.00) < 1e-9

    def test_known_model_output_tokens(self):
        from agentweave.pricing import compute_cost
        cost = compute_cost("claude-sonnet-4-6", input_tokens=0, output_tokens=1_000_000)
        assert abs(cost - 15.00) < 1e-9

    def test_known_model_combined(self):
        from agentweave.pricing import compute_cost
        # 1000 input + 500 output for claude-sonnet-4-6
        # = (1000 * 3.00/1M) + (500 * 15.00/1M)
        # = 0.003 + 0.0075 = 0.0105
        cost = compute_cost("claude-sonnet-4-6", input_tokens=1000, output_tokens=500)
        assert abs(cost - 0.0105) < 1e-9

    def test_provider_prefix_stripped(self):
        from agentweave.pricing import compute_cost
        # "anthropic/claude-haiku-4-5" should resolve to "claude-haiku-4-5"
        cost_prefixed = compute_cost("anthropic/claude-haiku-4-5", input_tokens=1_000_000, output_tokens=0)
        cost_bare = compute_cost("claude-haiku-4-5", input_tokens=1_000_000, output_tokens=0)
        assert abs(cost_prefixed - cost_bare) < 1e-9
        assert abs(cost_prefixed - 0.80) < 1e-9

    def test_case_insensitive(self):
        from agentweave.pricing import compute_cost
        cost_lower = compute_cost("claude-haiku-4-5", input_tokens=1_000_000, output_tokens=0)
        cost_upper = compute_cost("Claude-Haiku-4-5", input_tokens=1_000_000, output_tokens=0)
        assert abs(cost_lower - cost_upper) < 1e-9

    def test_unknown_model_returns_sentinel(self):
        from agentweave.pricing import compute_cost, UNKNOWN_COST
        cost = compute_cost("totally-unknown-model-xyz", input_tokens=100, output_tokens=50)
        assert cost == UNKNOWN_COST

    def test_unknown_cost_is_negative_one(self):
        from agentweave.pricing import UNKNOWN_COST
        assert UNKNOWN_COST == -1.0

    def test_zero_tokens(self):
        from agentweave.pricing import compute_cost
        cost = compute_cost("gpt-4o", input_tokens=0, output_tokens=0)
        assert cost == 0.0

    def test_gpt4o_pricing(self):
        from agentweave.pricing import compute_cost
        # gpt-4o: $2.50 input / $10.00 output per 1M
        cost = compute_cost("gpt-4o", input_tokens=1_000_000, output_tokens=1_000_000)
        assert abs(cost - 12.50) < 1e-9

    def test_gemini_2_5_flash_pricing(self):
        from agentweave.pricing import compute_cost
        # gemini-2.5-flash: $0.075 input / $0.30 output per 1M
        cost = compute_cost("gemini-2.5-flash", input_tokens=1_000_000, output_tokens=0)
        assert abs(cost - 0.075) < 1e-9

    def test_gemini_2_0_flash_pricing(self):
        from agentweave.pricing import compute_cost
        # gemini-2.0-flash: $0.075 input / $0.30 output per 1M
        cost = compute_cost("gemini-2.0-flash", input_tokens=1_000_000, output_tokens=0)
        assert abs(cost - 0.075) < 1e-9

    def test_compute_cost_cache_aware_sonnet(self):
        from agentweave.pricing import compute_cost
        # claude-sonnet-4-6: $0.30/1M cache_read, $3.75/1M cache_write, $3.00/1M uncached input, $15/1M output
        # 1M cache_read + 0 write + 0 uncached + 0 output = $0.30
        cost = compute_cost(
            "claude-sonnet-4-6",
            input_tokens=1_000_000,
            output_tokens=0,
            cache_read_tokens=1_000_000,
        )
        assert abs(cost - 0.30) < 1e-9

    def test_compute_cost_cache_write_sonnet(self):
        from agentweave.pricing import compute_cost
        # 1M cache_write + 0 read + 0 uncached = $3.75
        cost = compute_cost(
            "claude-sonnet-4-6",
            input_tokens=1_000_000,
            output_tokens=0,
            cache_write_tokens=1_000_000,
        )
        assert abs(cost - 3.75) < 1e-9

    def test_compute_cost_full_cache_breakdown(self):
        from agentweave.pricing import compute_cost
        # 100k cache_read + 10k cache_write + 40k uncached input + 20k output
        # total prompt = 150k → uncached = 150k - 100k - 10k = 40k
        cost = compute_cost(
            "claude-sonnet-4-6",
            input_tokens=150_000,
            output_tokens=20_000,
            cache_read_tokens=100_000,
            cache_write_tokens=10_000,
        )
        expected = (
            100_000 * 0.30  / 1_000_000   # cache_read
            + 10_000 * 3.75  / 1_000_000  # cache_write
            + 40_000 * 3.00  / 1_000_000  # uncached input
            + 20_000 * 15.00 / 1_000_000  # output
        )
        assert abs(cost - expected) < 1e-9

    def test_partial_match_versioned_model(self):
        """A versioned model name like 'claude-sonnet-4-6-20250101' should partially match."""
        from agentweave.pricing import compute_cost
        cost = compute_cost("claude-sonnet-4-6-20250101", input_tokens=1_000_000, output_tokens=0)
        # Should partially match 'claude-sonnet-4-6' (not return unknown)
        assert cost != -1.0
        assert abs(cost - 3.00) < 1e-9

    def test_openai_prefix_stripped(self):
        from agentweave.pricing import compute_cost
        cost = compute_cost("openai/gpt-4o-mini", input_tokens=1_000_000, output_tokens=0)
        assert abs(cost - 0.15) < 1e-9


class TestPricingEnvOverride:
    """Test AGENTWEAVE_PRICING_OVERRIDE env variable."""

    def test_env_override_adds_model(self, monkeypatch):
        import json
        from agentweave.pricing import compute_cost
        override = {"my-custom-llm": [1.00, 5.00]}
        monkeypatch.setenv("AGENTWEAVE_PRICING_OVERRIDE", json.dumps(override))
        cost = compute_cost("my-custom-llm", input_tokens=1_000_000, output_tokens=0)
        assert abs(cost - 1.00) < 1e-9

    def test_env_override_replaces_existing(self, monkeypatch):
        import json
        from agentweave.pricing import compute_cost
        # Override gpt-4o with cheaper price
        override = {"gpt-4o": [0.50, 2.00]}
        monkeypatch.setenv("AGENTWEAVE_PRICING_OVERRIDE", json.dumps(override))
        cost = compute_cost("gpt-4o", input_tokens=1_000_000, output_tokens=0)
        assert abs(cost - 0.50) < 1e-9

    def test_malformed_override_ignored(self, monkeypatch):
        from agentweave.pricing import compute_cost
        monkeypatch.setenv("AGENTWEAVE_PRICING_OVERRIDE", "not-valid-json!!!")
        # Should not raise, just use defaults
        cost = compute_cost("gpt-4o", input_tokens=1_000_000, output_tokens=0)
        assert abs(cost - 2.50) < 1e-9

    def test_empty_override_uses_defaults(self, monkeypatch):
        from agentweave.pricing import compute_cost
        monkeypatch.setenv("AGENTWEAVE_PRICING_OVERRIDE", "")
        cost = compute_cost("gpt-4o", input_tokens=1_000_000, output_tokens=0)
        assert abs(cost - 2.50) < 1e-9


# ---------------------------------------------------------------------------
# Proxy span attribute tests — cost.usd emitted by _set_*_response_attrs
# ---------------------------------------------------------------------------

pytest.importorskip("fastapi", reason="proxy deps not installed — install with agentweave[proxy]")

pytestmark = pytest.mark.proxy


class _FakeSpan:
    def __init__(self):
        self.attrs: dict = {}

    def set_attribute(self, key, value):
        self.attrs[key] = value


class TestProxyCostAttrs:
    """Verify that cost.usd is emitted on proxy spans with correct values."""

    def test_anthropic_cost_emitted(self):
        from agentweave.proxy import _set_anthropic_response_attrs
        span = _FakeSpan()
        data = {
            "usage": {"input_tokens": 1_000_000, "output_tokens": 0},
            "stop_reason": "end_turn",
        }
        _set_anthropic_response_attrs(span, data, elapsed_ms=50, model="claude-sonnet-4-6")
        assert "cost.usd" in span.attrs
        assert abs(span.attrs["cost.usd"] - 3.00) < 1e-9

    def test_anthropic_cost_with_cache_tokens(self):
        from agentweave.proxy import _set_anthropic_response_attrs
        span = _FakeSpan()
        data = {
            "usage": {
                "input_tokens": 0,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 1_000_000,
                "output_tokens": 0,
            },
        }
        _set_anthropic_response_attrs(span, data, elapsed_ms=10, model="claude-sonnet-4-6")
        # 1M cache_read tokens at $0.30/MTok = $0.30 (not $3.00 which was the buggy behavior)
        assert abs(span.attrs["cost.usd"] - 0.30) < 1e-9

    def test_openai_cost_emitted(self):
        from agentweave.proxy import _set_openai_response_attrs
        span = _FakeSpan()
        data = {
            "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1_000_000, "completion_tokens": 0, "total_tokens": 1_000_000},
        }
        _set_openai_response_attrs(span, data, elapsed_ms=30, model="gpt-4o")
        assert "cost.usd" in span.attrs
        assert abs(span.attrs["cost.usd"] - 2.50) < 1e-9

    def test_google_cost_emitted(self):
        from agentweave.proxy import _set_google_response_attrs
        span = _FakeSpan()
        data = {
            "usageMetadata": {
                "promptTokenCount": 1_000_000,
                "candidatesTokenCount": 0,
                "totalTokenCount": 1_000_000,
            }
        }
        _set_google_response_attrs(span, data, elapsed_ms=20, model="gemini-2.5-flash")
        assert "cost.usd" in span.attrs
        # gemini-2.5-flash corrected price: $0.075/1M input
        assert abs(span.attrs["cost.usd"] - 0.075) < 1e-9

    def test_unknown_model_cost_is_sentinel(self):
        from agentweave.proxy import _set_anthropic_response_attrs
        from agentweave.pricing import UNKNOWN_COST
        span = _FakeSpan()
        data = {"usage": {"input_tokens": 100, "output_tokens": 50}}
        _set_anthropic_response_attrs(span, data, elapsed_ms=10, model="my-unknown-model-xyz")
        assert span.attrs["cost.usd"] == UNKNOWN_COST

    def test_no_cost_when_no_tokens(self):
        """cost.usd should not be set when token counts are zero."""
        from agentweave.proxy import _set_anthropic_response_attrs
        span = _FakeSpan()
        data = {"usage": {"input_tokens": 0, "output_tokens": 0}}
        _set_anthropic_response_attrs(span, data, elapsed_ms=5, model="claude-sonnet-4-6")
        assert "cost.usd" not in span.attrs

    def test_no_cost_when_no_model(self):
        """cost.usd should not be set when model is empty."""
        from agentweave.proxy import _set_openai_response_attrs
        span = _FakeSpan()
        data = {
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
        }
        _set_openai_response_attrs(span, data, elapsed_ms=5, model="")
        assert "cost.usd" not in span.attrs

    def test_provider_prefix_in_model_name(self):
        """Proxy model names like 'anthropic/claude-haiku-4-5' should compute cost correctly."""
        from agentweave.proxy import _set_anthropic_response_attrs
        span = _FakeSpan()
        data = {"usage": {"input_tokens": 1_000_000, "output_tokens": 0}}
        _set_anthropic_response_attrs(span, data, elapsed_ms=10, model="anthropic/claude-haiku-4-5")
        assert abs(span.attrs["cost.usd"] - 0.80) < 1e-9


# ---------------------------------------------------------------------------
# Decorator cost tests
# ---------------------------------------------------------------------------

class _MockAnthropicUsage:
    def __init__(self, input_tokens, output_tokens):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _MockAnthropicResponse:
    def __init__(self, input_tokens, output_tokens, stop_reason="end_turn"):
        self.usage = _MockAnthropicUsage(input_tokens, output_tokens)
        self.stop_reason = stop_reason
        self.content = []


class TestDecoratorCost:
    """Verify cost.usd is emitted by trace_llm decorator."""

    def test_trace_llm_computes_cost(self, monkeypatch):
        from agentweave.decorators import _extract_llm_attrs
        resp = _MockAnthropicResponse(input_tokens=1_000_000, output_tokens=0)
        attrs = _extract_llm_attrs(resp, captures_output=False, model="claude-sonnet-4-6")
        assert "cost.usd" in attrs
        assert abs(attrs["cost.usd"] - 3.00) < 1e-9

    def test_trace_llm_cost_override(self):
        from agentweave.decorators import _extract_llm_attrs
        resp = _MockAnthropicResponse(input_tokens=1_000_000, output_tokens=0)
        attrs = _extract_llm_attrs(resp, captures_output=False, model="claude-sonnet-4-6", cost_usd_override=0.042)
        assert abs(attrs["cost.usd"] - 0.042) < 1e-9

    def test_trace_llm_unknown_model_sentinel(self):
        from agentweave.decorators import _extract_llm_attrs
        from agentweave.pricing import UNKNOWN_COST
        resp = _MockAnthropicResponse(input_tokens=100, output_tokens=50)
        attrs = _extract_llm_attrs(resp, captures_output=False, model="mystery-model-999")
        assert attrs["cost.usd"] == UNKNOWN_COST

    def test_trace_llm_no_model_no_cost(self):
        """When model is empty (not passed), cost.usd should not be set."""
        from agentweave.decorators import _extract_llm_attrs
        resp = _MockAnthropicResponse(input_tokens=100, output_tokens=50)
        attrs = _extract_llm_attrs(resp, captures_output=False)
        assert "cost.usd" not in attrs
