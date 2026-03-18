"""Provider contract tests with recorded response fixtures.

Tests that the proxy correctly parses token usage and stop reasons from
realistic provider response shapes — no live API calls required.
"""

import json
from pathlib import Path

import pytest

# Import parsing helpers directly (no HTTP needed)
from agentweave.proxy import (
    _detect_provider,
    _parse_anthropic_sse,
    _extract_anthropic_cache_tokens,
    _parse_openai_sse,
    _parse_google_stream,
)

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Provider detection
# ---------------------------------------------------------------------------

class TestProviderDetection:
    def test_anthropic_messages(self):
        assert _detect_provider("v1/messages") == "anthropic"

    def test_openai_chat_completions(self):
        assert _detect_provider("v1/chat/completions") == "openai"

    def test_openai_completions(self):
        assert _detect_provider("v1/completions") == "openai"

    def test_openai_embeddings(self):
        assert _detect_provider("v1/embeddings") == "openai"

    def test_google_v1beta(self):
        assert _detect_provider("v1beta/models/gemini-2.0-flash:generateContent") == "google"

    def test_google_v1_models_colon(self):
        assert _detect_provider("v1/models/gemini-2.5-pro:streamGenerateContent") == "google"

    def test_unknown_defaults_to_anthropic(self):
        assert _detect_provider("v1/unknown/path") == "anthropic"


# ---------------------------------------------------------------------------
# Anthropic non-streaming response
# ---------------------------------------------------------------------------

class TestAnthropicNonStream:
    def setup_method(self):
        with open(FIXTURES / "anthropic_nonstream_response.json") as f:
            self.response = json.load(f)

    def test_input_tokens(self):
        assert self.response["usage"]["input_tokens"] == 15

    def test_output_tokens(self):
        assert self.response["usage"]["output_tokens"] == 10

    def test_stop_reason(self):
        assert self.response["stop_reason"] == "end_turn"

    def test_model(self):
        assert self.response["model"] == "claude-3-haiku-20240307"

    def test_no_cache_tokens(self):
        usage = self.response["usage"]
        assert usage.get("cache_creation_input_tokens", 0) == 0
        assert usage.get("cache_read_input_tokens", 0) == 0


# ---------------------------------------------------------------------------
# Anthropic streaming SSE parsing
# ---------------------------------------------------------------------------

class TestAnthropicStream:
    def setup_method(self):
        self.lines = (FIXTURES / "anthropic_stream_response.jsonl").read_text().splitlines()

    def test_extracts_input_tokens(self):
        input_tokens = output_tokens = 0
        stop_reason = None
        for line in self.lines:
            input_tokens, output_tokens, stop_reason = _parse_anthropic_sse(
                line, input_tokens, output_tokens, stop_reason
            )
        assert input_tokens == 15

    def test_extracts_output_tokens(self):
        input_tokens = output_tokens = 0
        stop_reason = None
        for line in self.lines:
            input_tokens, output_tokens, stop_reason = _parse_anthropic_sse(
                line, input_tokens, output_tokens, stop_reason
            )
        assert output_tokens == 3

    def test_extracts_stop_reason(self):
        input_tokens = output_tokens = 0
        stop_reason = None
        for line in self.lines:
            input_tokens, output_tokens, stop_reason = _parse_anthropic_sse(
                line, input_tokens, output_tokens, stop_reason
            )
        assert stop_reason == "end_turn"

    def test_no_cache_tokens_in_fixture(self):
        for line in self.lines:
            cache_write, cache_read = _extract_anthropic_cache_tokens(line)
            assert cache_write == 0
            assert cache_read == 0

    def test_non_data_lines_ignored(self):
        """Lines starting with 'event:' should be ignored."""
        input_tokens, output_tokens, stop_reason = _parse_anthropic_sse(
            "event: message_start", 5, 0, None
        )
        assert input_tokens == 5  # unchanged

    def test_cache_tokens_extracted(self):
        """Fixture with cache tokens should be parsed correctly."""
        line = (
            'data: {"type":"message_start","message":{"usage":{'
            '"input_tokens":10,'
            '"cache_creation_input_tokens":200,'
            '"cache_read_input_tokens":1500}}}'
        )
        cache_write, cache_read = _extract_anthropic_cache_tokens(line)
        assert cache_write == 200
        assert cache_read == 1500

    def test_total_input_includes_cache(self):
        """Input tokens should sum bare + cache_creation + cache_read."""
        line = (
            'data: {"type":"message_start","message":{"usage":{'
            '"input_tokens":10,'
            '"cache_creation_input_tokens":200,'
            '"cache_read_input_tokens":1500}}}'
        )
        input_tokens, output_tokens, stop_reason = _parse_anthropic_sse(line, 0, 0, None)
        assert input_tokens == 1710  # 10 + 200 + 1500


# ---------------------------------------------------------------------------
# OpenAI non-streaming
# ---------------------------------------------------------------------------

class TestOpenAINonStream:
    def setup_method(self):
        with open(FIXTURES / "openai_nonstream_response.json") as f:
            self.response = json.load(f)

    def test_prompt_tokens(self):
        assert self.response["usage"]["prompt_tokens"] == 12

    def test_completion_tokens(self):
        assert self.response["usage"]["completion_tokens"] == 8

    def test_stop_reason(self):
        assert self.response["choices"][0]["finish_reason"] == "stop"

    def test_model(self):
        assert self.response["model"] == "gpt-4o"


# ---------------------------------------------------------------------------
# OpenAI streaming SSE parsing
# ---------------------------------------------------------------------------

class TestOpenAIStream:
    def setup_method(self):
        self.lines = (FIXTURES / "openai_stream_response.jsonl").read_text().splitlines()

    def test_extracts_input_tokens(self):
        input_tokens = output_tokens = 0
        stop_reason = None
        for line in self.lines:
            input_tokens, output_tokens, stop_reason = _parse_openai_sse(
                line, input_tokens, output_tokens, stop_reason
            )
        assert input_tokens == 12

    def test_extracts_output_tokens(self):
        input_tokens = output_tokens = 0
        stop_reason = None
        for line in self.lines:
            input_tokens, output_tokens, stop_reason = _parse_openai_sse(
                line, input_tokens, output_tokens, stop_reason
            )
        assert output_tokens == 5

    def test_extracts_stop_reason(self):
        input_tokens = output_tokens = 0
        stop_reason = None
        for line in self.lines:
            input_tokens, output_tokens, stop_reason = _parse_openai_sse(
                line, input_tokens, output_tokens, stop_reason
            )
        assert stop_reason == "stop"

    def test_done_sentinel_ignored(self):
        """data: [DONE] should not raise."""
        input_tokens, output_tokens, stop_reason = _parse_openai_sse(
            "data: [DONE]", 5, 3, "stop"
        )
        assert input_tokens == 5
        assert output_tokens == 3


# ---------------------------------------------------------------------------
# Google non-streaming
# ---------------------------------------------------------------------------

class TestGoogleNonStream:
    def setup_method(self):
        with open(FIXTURES / "google_nonstream_response.json") as f:
            self.response = json.load(f)

    def test_prompt_tokens(self):
        assert self.response["usageMetadata"]["promptTokenCount"] == 10

    def test_output_tokens(self):
        assert self.response["usageMetadata"]["candidatesTokenCount"] == 8

    def test_finish_reason(self):
        assert self.response["candidates"][0]["finishReason"] == "STOP"

    def test_google_stream_parser_on_full_response(self):
        """Google stream parser should handle a full response chunk (used in streaming)."""
        line = json.dumps(self.response)
        input_tokens, output_tokens, stop_reason = _parse_google_stream(line, 0, 0, None)
        assert input_tokens == 10
        assert output_tokens == 8
        assert stop_reason == "STOP"

    def test_google_stream_parser_with_data_prefix(self):
        """Google stream sometimes uses 'data: ' prefix."""
        line = "data: " + json.dumps(self.response)
        input_tokens, output_tokens, stop_reason = _parse_google_stream(line, 0, 0, None)
        assert input_tokens == 10
        assert output_tokens == 8

    def test_empty_line_ignored(self):
        input_tokens, output_tokens, stop_reason = _parse_google_stream("", 7, 3, "STOP")
        assert input_tokens == 7
        assert output_tokens == 3
