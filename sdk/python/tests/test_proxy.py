"""Tests for the AgentWeave proxy — provider detection and OpenAI parsers."""

from agentweave.proxy import (
    _detect_provider,
    _openai_response_text,
    _parse_openai_sse,
    _set_openai_response_attrs,
)


class TestDetectProvider:
    """Provider detection from request path."""

    def test_openai_chat_completions(self):
        assert _detect_provider("v1/chat/completions") == "openai"

    def test_openai_completions(self):
        assert _detect_provider("v1/completions") == "openai"

    def test_openai_embeddings(self):
        assert _detect_provider("v1/embeddings") == "openai"

    def test_anthropic_messages(self):
        assert _detect_provider("v1/messages") == "anthropic"

    def test_google_v1beta(self):
        assert _detect_provider("v1beta/models/gemini-2.5-pro:generateContent") == "google"

    def test_google_v1_models(self):
        assert _detect_provider("v1/models/gemini-2.5-pro:generateContent") == "google"

    def test_anthropic_fallback(self):
        assert _detect_provider("v1/unknown/path") == "anthropic"


class _FakeSpan:
    """Minimal span stub that records set_attribute calls."""

    def __init__(self):
        self.attrs: dict = {}

    def set_attribute(self, key: str, value):
        self.attrs[key] = value


class TestSetOpenaiResponseAttrs:
    """Verify _set_openai_response_attrs populates correct span attributes."""

    def test_basic(self):
        span = _FakeSpan()
        data = {
            "choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        _set_openai_response_attrs(span, data, elapsed_ms=42)
        assert span.attrs["prov.llm.prompt_tokens"] == 10
        assert span.attrs["prov.llm.completion_tokens"] == 5
        assert span.attrs["prov.llm.total_tokens"] == 15
        assert span.attrs["prov.llm.stop_reason"] == "stop"
        assert span.attrs["agentweave.latency_ms"] == 42
        # gen_ai.* dual-emit
        assert span.attrs["gen_ai.usage.input_tokens"] == 10
        assert span.attrs["gen_ai.usage.output_tokens"] == 5
        assert span.attrs["gen_ai.response.finish_reasons"] == ["stop"]

    def test_no_choices(self):
        span = _FakeSpan()
        data = {"usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}}
        _set_openai_response_attrs(span, data, elapsed_ms=10)
        assert span.attrs["prov.llm.prompt_tokens"] == 1
        assert "prov.llm.stop_reason" not in span.attrs


class TestOpenaiResponseText:
    """Verify text extraction from OpenAI response format."""

    def test_normal(self):
        data = {"choices": [{"message": {"content": "Hi there"}}]}
        assert _openai_response_text(data) == "Hi there"

    def test_empty_choices(self):
        assert _openai_response_text({"choices": []}) == ""

    def test_missing_keys(self):
        assert _openai_response_text({}) == ""


class TestParseOpenaiSse:
    """Verify streaming SSE token accumulation and finish_reason."""

    def test_usage_chunk(self):
        line = 'data: {"usage": {"prompt_tokens": 20, "completion_tokens": 8}}'
        inp, out, stop = _parse_openai_sse(line, 0, 0, None)
        assert inp == 20
        assert out == 8
        assert stop is None

    def test_finish_reason(self):
        line = 'data: {"choices": [{"finish_reason": "stop", "delta": {"content": ""}}]}'
        inp, out, stop = _parse_openai_sse(line, 0, 0, None)
        assert stop == "stop"

    def test_done_sentinel(self):
        line = "data: [DONE]"
        inp, out, stop = _parse_openai_sse(line, 5, 3, "stop")
        assert (inp, out, stop) == (5, 3, "stop")

    def test_non_data_line(self):
        line = "event: message"
        inp, out, stop = _parse_openai_sse(line, 1, 2, None)
        assert (inp, out, stop) == (1, 2, None)

    def test_accumulation(self):
        """Simulate a multi-chunk stream."""
        inp, out, stop = 0, 0, None
        lines = [
            'data: {"choices": [{"delta": {"content": "Hi"}, "finish_reason": null}]}',
            'data: {"choices": [{"delta": {"content": " there"}, "finish_reason": "stop"}]}',
            'data: {"usage": {"prompt_tokens": 15, "completion_tokens": 4}}',
            "data: [DONE]",
        ]
        for l in lines:
            inp, out, stop = _parse_openai_sse(l, inp, out, stop)
        assert inp == 15
        assert out == 4
        assert stop == "stop"
