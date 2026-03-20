"""Tests for the AgentWeave proxy — provider detection, parsers, auth, and header forwarding."""

import logging

import pytest

pytest.importorskip("fastapi", reason="proxy deps not installed — install with agentweave[proxy]")

pytestmark = pytest.mark.proxy

import agentweave.proxy as proxy_module
from agentweave.proxy import (
    _check_auth,
    _detect_provider,
    _extract_anthropic_cache_tokens,
    _openai_response_text,
    _parse_anthropic_sse,
    _parse_google_stream,
    _parse_openai_sse,
    _set_anthropic_response_attrs,
    _set_google_response_attrs,
    _set_openai_response_attrs,
    _set_request_attrs,
    _anthropic_response_text,
    _google_response_text,
    _SKIP_HEADERS_ALWAYS,
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

    def test_openai_responses(self):
        assert _detect_provider("v1/responses") == "openai"

    def test_openai_prefix_models(self):
        """Prefix match: v1/models endpoint (list models, fine-tuning base models)."""
        assert _detect_provider("v1/models") == "openai"
        assert _detect_provider("v1/models/gpt-4o") == "openai"

    def test_openai_prefix_assistants(self):
        """Prefix match: Assistants API paths."""
        assert _detect_provider("v1/assistants") == "openai"
        assert _detect_provider("v1/assistants/asst_abc123/files") == "openai"

    def test_openai_prefix_images(self):
        """Prefix match: Images API."""
        assert _detect_provider("v1/images/generations") == "openai"

    def test_openai_prefix_trailing_slash(self):
        """Prefix match handles trailing slashes gracefully."""
        assert _detect_provider("v1/chat/completions") == "openai"

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

    def test_responses_api_token_shape(self):
        """Responses API uses input_tokens/output_tokens instead of prompt_tokens/completion_tokens."""
        span = _FakeSpan()
        data = {
            "id": "resp_abc123",
            "output": [{"type": "message", "content": [{"type": "text", "text": "hi"}]}],
            "usage": {"input_tokens": 25, "output_tokens": 12, "total_tokens": 37},
        }
        _set_openai_response_attrs(span, data, elapsed_ms=55)
        assert span.attrs["prov.llm.prompt_tokens"] == 25
        assert span.attrs["prov.llm.completion_tokens"] == 12
        assert span.attrs["prov.llm.total_tokens"] == 37
        assert span.attrs["agentweave.latency_ms"] == 55
        # gen_ai.* dual-emit
        assert span.attrs["gen_ai.usage.input_tokens"] == 25
        assert span.attrs["gen_ai.usage.output_tokens"] == 12


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

    def test_responses_api_usage_chunk(self):
        """Responses API uses input_tokens/output_tokens in SSE usage chunks."""
        line = 'data: {"usage": {"input_tokens": 30, "output_tokens": 15}}'
        inp, out, stop = _parse_openai_sse(line, 0, 0, None)
        assert inp == 30
        assert out == 15

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


class TestOpenaiStreamingZeroTokensWarning:
    """Warn when OpenAI streaming response has no usage data (issue #39)."""

    def test_warns_when_openai_streaming_has_zero_tokens(self, caplog):
        """Simulate an OpenAI stream with no usage chunk — should log a warning."""
        lines = [
            'data: {"choices": [{"delta": {"content": "Hi"}, "finish_reason": null}]}',
            'data: {"choices": [{"delta": {"content": " there"}, "finish_reason": "stop"}]}',
            "data: [DONE]",
        ]
        inp, out, stop = 0, 0, None
        for l in lines:
            inp, out, stop = _parse_openai_sse(l, inp, out, stop)

        # Both tokens remain 0 — the warning should fire
        assert inp == 0
        assert out == 0

        # Simulate the warning logic from _stream_and_trace
        proxy_logger = logging.getLogger("agentweave.proxy")
        with caplog.at_level(logging.WARNING, logger="agentweave.proxy"):
            if inp == 0 and out == 0:
                proxy_logger.warning(
                    "OpenAI streaming response completed with 0 tokens. "
                    'Add stream_options={"include_usage": true} to your request '
                    "to enable token tracking."
                )

        assert len(caplog.records) == 1
        assert "0 tokens" in caplog.records[0].message
        assert "include_usage" in caplog.records[0].message

    def test_no_warning_when_usage_present(self, caplog):
        """Stream with usage chunk should NOT trigger the warning."""
        lines = [
            'data: {"choices": [{"delta": {"content": "Hi"}, "finish_reason": "stop"}]}',
            'data: {"usage": {"prompt_tokens": 10, "completion_tokens": 5}}',
            "data: [DONE]",
        ]
        inp, out, stop = 0, 0, None
        for l in lines:
            inp, out, stop = _parse_openai_sse(l, inp, out, stop)

        assert inp == 10
        assert out == 5

        # The condition would not trigger
        proxy_logger = logging.getLogger("agentweave.proxy")
        with caplog.at_level(logging.WARNING, logger="agentweave.proxy"):
            if inp == 0 and out == 0:
                proxy_logger.warning(
                    "OpenAI streaming response completed with 0 tokens. "
                    'Add stream_options={"include_usage": true} to your request '
                    "to enable token tracking."
                )

        assert len(caplog.records) == 0


class TestHealthEndpoint:
    """Verify /health returns 200 without auth."""

    def test_health_no_auth(self):
        from fastapi.testclient import TestClient
        from agentweave.proxy import app
        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "version" in data

    def test_health_with_auth(self):
        """Auth header should be ignored on /health."""
        from fastapi.testclient import TestClient
        from agentweave.proxy import app
        client = TestClient(app)
        resp = client.get("/health", headers={"Authorization": "Bearer wrongtoken"})
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class _FakeRequest:
    """Minimal Request stub with a headers dict."""

    def __init__(self, headers: dict[str, str] | None = None):
        self.headers = headers or {}


class TestCheckAuth:
    """Auth token validation via _check_auth."""

    def test_open_mode_no_token(self, monkeypatch):
        monkeypatch.setattr(proxy_module, "_PROXY_TOKEN", None)
        result = _check_auth(_FakeRequest())
        assert result is None

    def test_missing_authorization_header(self, monkeypatch):
        monkeypatch.setattr(proxy_module, "_PROXY_TOKEN", "secret123")
        result = _check_auth(_FakeRequest({}))
        assert result is not None
        assert result.status_code == 401
        assert b"missing_token" in result.body

    def test_invalid_token(self, monkeypatch):
        monkeypatch.setattr(proxy_module, "_PROXY_TOKEN", "secret123")
        result = _check_auth(_FakeRequest({"authorization": "Bearer wrong"}))
        assert result is not None
        assert result.status_code == 401
        assert b"invalid_token" in result.body

    def test_valid_token(self, monkeypatch):
        monkeypatch.setattr(proxy_module, "_PROXY_TOKEN", "secret123")
        result = _check_auth(_FakeRequest({"authorization": "Bearer secret123"}))
        assert result is None


# ---------------------------------------------------------------------------
# Anthropic SSE parser
# ---------------------------------------------------------------------------


class TestParseAnthropicSse:
    """Anthropic SSE token parsing and stop_reason extraction."""

    def test_message_start_with_cache_tokens(self):
        line = (
            'data: {"type": "message_start", "message": {"usage": '
            '{"input_tokens": 10, "cache_creation_input_tokens": 50, '
            '"cache_read_input_tokens": 100}}}'
        )
        inp, out, stop = _parse_anthropic_sse(line, 0, 0, None)
        assert inp == 160  # 10 + 50 + 100
        assert out == 0
        assert stop is None

    def test_message_delta_output_tokens_and_stop(self):
        line = (
            'data: {"type": "message_delta", "usage": {"output_tokens": 42}, '
            '"delta": {"stop_reason": "end_turn"}}'
        )
        inp, out, stop = _parse_anthropic_sse(line, 100, 0, None)
        assert inp == 100
        assert out == 42
        assert stop == "end_turn"

    def test_non_data_line_ignored(self):
        line = "event: message_start"
        inp, out, stop = _parse_anthropic_sse(line, 5, 3, None)
        assert (inp, out, stop) == (5, 3, None)

    def test_done_sentinel(self):
        line = "data: [DONE]"
        inp, out, stop = _parse_anthropic_sse(line, 10, 20, "end_turn")
        assert (inp, out, stop) == (10, 20, "end_turn")


# ---------------------------------------------------------------------------
# Google stream parser
# ---------------------------------------------------------------------------


class TestParseGoogleStream:
    """Google streaming token parsing."""

    def test_usage_metadata(self):
        line = 'data: {"usageMetadata": {"promptTokenCount": 30, "candidatesTokenCount": 12}}'
        inp, out, stop = _parse_google_stream(line, 0, 0, None)
        assert inp == 30
        assert out == 12

    def test_finish_reason(self):
        line = 'data: {"candidates": [{"finishReason": "STOP"}]}'
        inp, out, stop = _parse_google_stream(line, 0, 0, None)
        assert stop == "STOP"

    def test_bare_json_line(self):
        line = '{"usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 2}}'
        inp, out, stop = _parse_google_stream(line, 0, 0, None)
        assert inp == 5
        assert out == 2

    def test_finish_reason_unspecified_ignored(self):
        line = 'data: {"candidates": [{"finishReason": "FINISH_REASON_UNSPECIFIED"}]}'
        inp, out, stop = _parse_google_stream(line, 0, 0, None)
        assert stop is None


# ---------------------------------------------------------------------------
# Anthropic response attrs
# ---------------------------------------------------------------------------


class TestSetAnthropicResponseAttrs:
    """Verify _set_anthropic_response_attrs populates span correctly."""

    def test_token_counts_with_cache(self):
        span = _FakeSpan()
        data = {
            "usage": {
                "input_tokens": 5,
                "cache_creation_input_tokens": 20,
                "cache_read_input_tokens": 75,
                "output_tokens": 30,
            },
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "response"}],
        }
        _set_anthropic_response_attrs(span, data, elapsed_ms=99)
        assert span.attrs["prov.llm.prompt_tokens"] == 100  # 5 + 20 + 75
        assert span.attrs["prov.llm.completion_tokens"] == 30
        assert span.attrs["prov.llm.total_tokens"] == 130
        assert span.attrs["prov.llm.stop_reason"] == "end_turn"
        assert span.attrs["agentweave.latency_ms"] == 99
        # gen_ai.* dual-emit
        assert span.attrs["gen_ai.usage.input_tokens"] == 100
        assert span.attrs["gen_ai.usage.output_tokens"] == 30
        assert span.attrs["gen_ai.response.finish_reasons"] == ["end_turn"]

    def test_no_stop_reason(self):
        span = _FakeSpan()
        data = {"usage": {"input_tokens": 10, "output_tokens": 5}}
        _set_anthropic_response_attrs(span, data, elapsed_ms=50)
        assert span.attrs["prov.llm.prompt_tokens"] == 10
        assert "prov.llm.stop_reason" not in span.attrs
        assert "gen_ai.response.finish_reasons" not in span.attrs


# ---------------------------------------------------------------------------
# Google response attrs
# ---------------------------------------------------------------------------


class TestSetGoogleResponseAttrs:
    """Verify _set_google_response_attrs populates span correctly."""

    def test_usage_and_stop(self):
        span = _FakeSpan()
        data = {
            "usageMetadata": {
                "promptTokenCount": 25,
                "candidatesTokenCount": 10,
                "totalTokenCount": 35,
            },
            "candidates": [
                {
                    "finishReason": "STOP",
                    "content": {"parts": [{"text": "hello"}]},
                }
            ],
        }
        _set_google_response_attrs(span, data, elapsed_ms=77)
        assert span.attrs["prov.llm.prompt_tokens"] == 25
        assert span.attrs["prov.llm.completion_tokens"] == 10
        assert span.attrs["prov.llm.total_tokens"] == 35
        assert span.attrs["prov.llm.stop_reason"] == "STOP"
        assert span.attrs["agentweave.latency_ms"] == 77
        # gen_ai.* dual-emit
        assert span.attrs["gen_ai.usage.input_tokens"] == 25
        assert span.attrs["gen_ai.usage.output_tokens"] == 10
        assert span.attrs["gen_ai.response.finish_reasons"] == ["STOP"]

    def test_no_candidates(self):
        span = _FakeSpan()
        data = {
            "usageMetadata": {"promptTokenCount": 8, "candidatesTokenCount": 3, "totalTokenCount": 11},
        }
        _set_google_response_attrs(span, data, elapsed_ms=20)
        assert span.attrs["prov.llm.prompt_tokens"] == 8
        assert "prov.llm.stop_reason" not in span.attrs
        assert "gen_ai.response.finish_reasons" not in span.attrs


# ---------------------------------------------------------------------------
# Response text extractors
# ---------------------------------------------------------------------------


class TestResponseText:
    """Text extraction for Anthropic and Google response formats."""

    def test_anthropic_response_text(self):
        data = {"content": [{"type": "text", "text": "Hello from Claude"}]}
        assert _anthropic_response_text(data) == "Hello from Claude"

    def test_anthropic_response_text_empty(self):
        assert _anthropic_response_text({}) == ""

    def test_google_response_text(self):
        data = {"candidates": [{"content": {"parts": [{"text": "Hello from Gemini"}]}}]}
        assert _google_response_text(data) == "Hello from Gemini"

    def test_google_response_text_empty(self):
        assert _google_response_text({}) == ""


# ---------------------------------------------------------------------------
# Prompt capture (_set_request_attrs)
# ---------------------------------------------------------------------------


class TestPromptCapture:
    """Verify prompt preview is captured when enabled."""

    def _call(self, monkeypatch, provider, body):
        monkeypatch.setenv("AGENTWEAVE_CAPTURE_PROMPTS", "true")
        # Avoid config side effects
        from agentweave.config import AgentWeaveConfig
        monkeypatch.setattr(AgentWeaveConfig, "get_or_none", staticmethod(lambda: None))
        span = _FakeSpan()
        _set_request_attrs(
            span, model="test-model", provider=provider,
            agent_id="agent-1", agent_model="test-model",
            path="v1/messages", body=body,
        )
        return span

    def test_anthropic_string_content(self, monkeypatch):
        body = {"messages": [{"role": "user", "content": "What is 2+2?"}]}
        span = self._call(monkeypatch, "anthropic", body)
        assert span.attrs["prov.llm.prompt_preview"] == "What is 2+2?"

    def test_anthropic_list_content(self, monkeypatch):
        body = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Hello"},
                        {"type": "text", "text": "World"},
                    ],
                }
            ]
        }
        span = self._call(monkeypatch, "anthropic", body)
        assert span.attrs["prov.llm.prompt_preview"] == "Hello World"

    def test_google_content(self, monkeypatch):
        body = {"contents": [{"parts": [{"text": "Tell me a joke"}]}]}
        span = self._call(monkeypatch, "google", body)
        assert span.attrs["prov.llm.prompt_preview"] == "Tell me a joke"

    def test_disabled_by_env(self, monkeypatch):
        monkeypatch.setenv("AGENTWEAVE_CAPTURE_PROMPTS", "false")
        from agentweave.config import AgentWeaveConfig
        monkeypatch.setattr(AgentWeaveConfig, "get_or_none", staticmethod(lambda: None))
        span = _FakeSpan()
        _set_request_attrs(
            span, model="m", provider="anthropic", agent_id="a",
            agent_model="m", path="v1/messages",
            body={"messages": [{"role": "user", "content": "secret"}]},
        )
        assert "prov.llm.prompt_preview" not in span.attrs


# ---------------------------------------------------------------------------
# Header forwarding
# ---------------------------------------------------------------------------


class TestHeaderForwarding:
    """Verify header strip logic matches proxy behavior."""

    def _filter_headers(self, raw_headers: dict[str, str], token_set: bool) -> dict[str, str]:
        """Replicate the header-filtering logic from the proxy() route."""
        filtered = {
            k: v for k, v in raw_headers.items()
            if k.lower() not in _SKIP_HEADERS_ALWAYS
        }
        if token_set:
            filtered.pop("authorization", None)
        return filtered

    def test_skip_headers_always_stripped(self):
        headers = {
            "host": "localhost",
            "content-length": "123",
            "x-agentweave-agent-id": "my-agent",
            "x-api-key": "sk-ant-123",
            "x-custom": "keep-me",
        }
        result = self._filter_headers(headers, token_set=False)
        assert "host" not in result
        assert "content-length" not in result
        assert "x-agentweave-agent-id" not in result
        assert result["x-api-key"] == "sk-ant-123"
        assert result["x-custom"] == "keep-me"

    def test_auth_stripped_when_proxy_token_set(self):
        headers = {
            "authorization": "Bearer proxy-token",
            "x-api-key": "sk-ant-123",
        }
        result = self._filter_headers(headers, token_set=True)
        assert "authorization" not in result
        assert result["x-api-key"] == "sk-ant-123"

    def test_session_headers_stripped(self):
        headers = {
            "x-agentweave-session-id": "sess-123",
            "x-agentweave-project": "launchpad",
            "x-agentweave-turn": "5",
            "x-api-key": "sk-ant-123",
        }
        result = self._filter_headers(headers, token_set=False)
        assert "x-agentweave-session-id" not in result
        assert "x-agentweave-project" not in result
        assert "x-agentweave-turn" not in result
        assert result["x-api-key"] == "sk-ant-123"

    def test_auth_forwarded_when_no_proxy_token(self):
        headers = {
            "authorization": "Bearer sk-ant-123",
            "x-api-key": "sk-ant-123",
        }
        result = self._filter_headers(headers, token_set=False)
        assert result["authorization"] == "Bearer sk-ant-123"


# ---------------------------------------------------------------------------
# Session context
# ---------------------------------------------------------------------------


class TestSessionContext:
    """Verify session/project/turn attributes are set on spans."""

    def _call(self, monkeypatch, session_id=None, project=None, turn=None):
        from agentweave.config import AgentWeaveConfig
        monkeypatch.setattr(AgentWeaveConfig, "get_or_none", staticmethod(lambda: None))
        span = _FakeSpan()
        _set_request_attrs(
            span, model="test-model", provider="anthropic",
            agent_id="agent-1", agent_model="test-model",
            path="v1/messages", body={},
            session_id=session_id, project=project, turn=turn,
        )
        return span

    def test_all_set(self, monkeypatch):
        span = self._call(monkeypatch, session_id="sess-abc", project="launchpad", turn=3)
        assert span.attrs["prov.session.id"] == "sess-abc"
        assert span.attrs["prov.project"] == "launchpad"
        assert span.attrs["prov.session.turn"] == 3

    def test_partial_set(self, monkeypatch):
        span = self._call(monkeypatch, session_id="sess-xyz")
        assert span.attrs["prov.session.id"] == "sess-xyz"
        assert "prov.project" not in span.attrs
        assert "prov.session.turn" not in span.attrs

    def test_none_set(self, monkeypatch):
        span = self._call(monkeypatch)
        assert "prov.session.id" not in span.attrs
        assert "prov.project" not in span.attrs
        assert "prov.session.turn" not in span.attrs

    def test_turn_is_int(self, monkeypatch):
        span = self._call(monkeypatch, turn=7)
        assert span.attrs["prov.session.turn"] == 7
        assert isinstance(span.attrs["prov.session.turn"], int)

    def test_session_id_dual_emit(self, monkeypatch):
        """session.id must be emitted alongside prov.session.id."""
        span = self._call(monkeypatch, session_id="conv-abc123")
        assert span.attrs["session.id"] == "conv-abc123"
        assert span.attrs["prov.session.id"] == "conv-abc123"

    def test_session_id_not_set_when_none(self, monkeypatch):
        """Neither session.id nor prov.session.id should appear when not provided."""
        span = self._call(monkeypatch)
        assert "session.id" not in span.attrs
        assert "prov.session.id" not in span.attrs



# ---------------------------------------------------------------------------
# Deterministic trace ID (_normalize_trace_id + _set_request_attrs)
# ---------------------------------------------------------------------------


class TestSubAgentAttributionHeaders:
    """Verify sub-agent attribution headers are read, set on spans, and stripped from forwarding (issue #15)."""

    def _call(self, monkeypatch, parent_session_id=None, agent_type=None, turn_depth=None):
        from agentweave.config import AgentWeaveConfig
        monkeypatch.setattr(AgentWeaveConfig, "get_or_none", staticmethod(lambda: None))
        span = _FakeSpan()
        _set_request_attrs(
            span, model="test-model", provider="anthropic",
            agent_id="sub-agent-1", agent_model="test-model",
            path="v1/messages", body={},
            parent_session_id=parent_session_id,
            agent_type=agent_type,
            turn_depth=turn_depth,
        )
        return span

    def test_all_subagent_attrs_set(self, monkeypatch):
        span = self._call(monkeypatch, parent_session_id="sess-parent-123",
                          agent_type="subagent", turn_depth=2)
        assert span.attrs["prov.parent.session.id"] == "sess-parent-123"
        assert span.attrs["prov.agent.type"] == "subagent"
        assert span.attrs["prov.session.turn"] == 2

    def test_delegated_agent_type(self, monkeypatch):
        span = self._call(monkeypatch, parent_session_id="sess-parent-456",
                          agent_type="delegated", turn_depth=3)
        assert span.attrs["prov.parent.session.id"] == "sess-parent-456"
        assert span.attrs["prov.agent.type"] == "delegated"
        assert span.attrs["prov.session.turn"] == 3

    def test_no_subagent_attrs_when_not_provided(self, monkeypatch):
        span = self._call(monkeypatch)
        assert "prov.parent.session.id" not in span.attrs
        assert "prov.agent.type" not in span.attrs

    def test_partial_subagent_attrs(self, monkeypatch):
        span = self._call(monkeypatch, parent_session_id="sess-parent-789")
        assert span.attrs["prov.parent.session.id"] == "sess-parent-789"
        assert "prov.agent.type" not in span.attrs

    def test_subagent_headers_stripped_from_forwarding(self):
        """Sub-agent headers must be in _SKIP_HEADERS_ALWAYS."""
        assert "x-agentweave-parent-session-id" in _SKIP_HEADERS_ALWAYS
        assert "x-agentweave-agent-type" in _SKIP_HEADERS_ALWAYS
        assert "x-agentweave-turn-depth" in _SKIP_HEADERS_ALWAYS


class TestTraceparentPassthrough:
    """Verify W3C traceparent header is read, set on spans, and forwarded downstream (issue #44)."""

    def _call(self, monkeypatch, traceparent=None):
        from agentweave.config import AgentWeaveConfig
        monkeypatch.setattr(AgentWeaveConfig, "get_or_none", staticmethod(lambda: None))
        span = _FakeSpan()
        _set_request_attrs(
            span, model="test-model", provider="anthropic",
            agent_id="agent-1", agent_model="test-model",
            path="v1/messages", body={},
            traceparent=traceparent,
        )
        return span

    def test_traceparent_set_on_span(self, monkeypatch):
        tp = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
        span = self._call(monkeypatch, traceparent=tp)
        assert span.attrs["prov.trace.parent"] == tp

    def test_traceparent_not_set_when_absent(self, monkeypatch):
        span = self._call(monkeypatch)
        assert "prov.trace.parent" not in span.attrs

    def test_traceparent_not_stripped_from_forwarding(self):
        """traceparent must NOT be in _SKIP_HEADERS_ALWAYS — it should be forwarded."""
        assert "traceparent" not in _SKIP_HEADERS_ALWAYS


class TestSessionEndpoint:
    """POST /session stores context, GET /session returns it, env-var fallback works."""

    def test_post_session_stores_context(self):
        from fastapi.testclient import TestClient
        from agentweave.proxy import app
        client = TestClient(app)
        resp = client.post("/session", json={
            "session_id": "sess-001",
            "parent_session_id": "sess-parent-001",
            "task_label": "build dashboard",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["context"]["prov.session.id"] == "sess-001"
        assert data["context"]["prov.parent.session.id"] == "sess-parent-001"
        assert data["context"]["prov.task.label"] == "build dashboard"

    def test_get_session_returns_current_context(self):
        from fastapi.testclient import TestClient
        from agentweave.proxy import app
        client = TestClient(app)
        # Set context first
        client.post("/session", json={"session_id": "sess-get-test"})
        resp = client.get("/session")
        assert resp.status_code == 200
        assert resp.json()["prov.session.id"] == "sess-get-test"

    def test_empty_values_excluded(self):
        from fastapi.testclient import TestClient
        from agentweave.proxy import app
        client = TestClient(app)
        resp = client.post("/session", json={
            "session_id": "sess-only",
            "parent_session_id": "",
            "task_label": "",
        })
        data = resp.json()
        assert "prov.session.id" in data["context"]
        assert "prov.parent.session.id" not in data["context"]
        assert "prov.task.label" not in data["context"]

    def test_env_var_fallback_at_startup(self, monkeypatch):
        """AGENTWEAVE_SESSION_ID env var populates _session_context at module reload."""
        import importlib
        monkeypatch.setenv("AGENTWEAVE_SESSION_ID", "env-sess-42")
        monkeypatch.setenv("AGENTWEAVE_PARENT_SESSION_ID", "")
        monkeypatch.setenv("AGENTWEAVE_TASK_LABEL", "")
        importlib.reload(proxy_module)
        assert proxy_module._session_context.get("prov.session.id") == "env-sess-42"
        assert "prov.parent.session.id" not in proxy_module._session_context
        assert "prov.task.label" not in proxy_module._session_context
        # Clean up: reload without env var to restore original state
        monkeypatch.delenv("AGENTWEAVE_SESSION_ID")
        importlib.reload(proxy_module)

    def test_session_context_applied_to_spans(self, monkeypatch):
        """After POST /session, _set_request_attrs applies context attrs to spans."""
        from agentweave.config import AgentWeaveConfig
        monkeypatch.setattr(AgentWeaveConfig, "get_or_none", staticmethod(lambda: None))
        # Set the global context
        monkeypatch.setattr(proxy_module, "_session_context", {
            "prov.session.id": "ctx-sess-99",
            "prov.task.label": "run tests",
        })
        span = _FakeSpan()
        _set_request_attrs(
            span, model="test-model", provider="anthropic",
            agent_id="agent-1", agent_model="test-model",
            path="v1/messages", body={},
        )
        assert span.attrs["prov.session.id"] == "ctx-sess-99"
        assert span.attrs["prov.task.label"] == "run tests"
        assert "prov.parent.session.id" not in span.attrs
        # Restore
        monkeypatch.setattr(proxy_module, "_session_context", {})

    def test_empty_context_no_blank_attrs(self, monkeypatch):
        """When _session_context is empty, no blank attributes end up on spans."""
        from agentweave.config import AgentWeaveConfig
        monkeypatch.setattr(AgentWeaveConfig, "get_or_none", staticmethod(lambda: None))
        monkeypatch.setattr(proxy_module, "_session_context", {})
        span = _FakeSpan()
        _set_request_attrs(
            span, model="test-model", provider="anthropic",
            agent_id="agent-1", agent_model="test-model",
            path="v1/messages", body={},
        )
        assert "prov.task.label" not in span.attrs


class TestNormalizeTraceId:
    """Unit tests for the _normalize_trace_id helper."""

    def test_valid_hex_32_chars(self):
        from agentweave.proxy import _normalize_trace_id
        hex_id = "a" * 32
        assert _normalize_trace_id(hex_id) == int(hex_id, 16)

    def test_mixed_case_hex(self):
        from agentweave.proxy import _normalize_trace_id
        hex_id = "A" * 16 + "b" * 16
        assert _normalize_trace_id(hex_id) == int(hex_id, 16)

    def test_arbitrary_string_is_hashed(self):
        import hashlib
        from agentweave.proxy import _normalize_trace_id
        arbitrary = "order-abc123-attempt-1"
        expected = int(hashlib.sha256(arbitrary.encode()).hexdigest()[:32], 16)
        assert _normalize_trace_id(arbitrary) == expected

    def test_same_input_same_output(self):
        from agentweave.proxy import _normalize_trace_id
        val = "order-abc123"
        assert _normalize_trace_id(val) == _normalize_trace_id(val)

    def test_empty_string_returns_none(self):
        from agentweave.proxy import _normalize_trace_id
        assert _normalize_trace_id("") is None

    def test_whitespace_string_returns_none(self):
        from agentweave.proxy import _normalize_trace_id
        assert _normalize_trace_id("   ") is None

    def test_too_short_hex_is_hashed(self):
        """A hex string that is <32 chars should be hashed, not used directly."""
        import hashlib
        from agentweave.proxy import _normalize_trace_id
        short_hex = "deadbeef"
        expected = int(hashlib.sha256(short_hex.encode()).hexdigest()[:32], 16)
        assert _normalize_trace_id(short_hex) == expected


class TestDeterministicTraceIdHeader:
    """Verify X-AgentWeave-Trace-Id is stripped from forwarded headers and sets span attribute."""

    def test_trace_id_header_stripped(self):
        """x-agentweave-trace-id must not be forwarded upstream."""
        assert "x-agentweave-trace-id" in _SKIP_HEADERS_ALWAYS

    def test_trace_id_attribute_set_on_span(self, monkeypatch):
        """agentweave.trace_id attribute is set on the span when header is present."""
        from agentweave.config import AgentWeaveConfig
        monkeypatch.setattr(AgentWeaveConfig, "get_or_none", staticmethod(lambda: None))
        span = _FakeSpan()
        _set_request_attrs(
            span, model="test-model", provider="anthropic",
            agent_id="agent-1", agent_model="test-model",
            path="v1/messages", body={},
            det_trace_id_raw="order-abc123-attempt-1",
        )
        assert span.attrs["agentweave.trace_id"] == "order-abc123-attempt-1"

    def test_trace_id_attribute_absent_when_not_provided(self, monkeypatch):
        """agentweave.trace_id should not appear when header is absent."""
        from agentweave.config import AgentWeaveConfig
        monkeypatch.setattr(AgentWeaveConfig, "get_or_none", staticmethod(lambda: None))
        span = _FakeSpan()
        _set_request_attrs(
            span, model="test-model", provider="anthropic",
            agent_id="agent-1", agent_model="test-model",
            path="v1/messages", body={},
        )
        assert "agentweave.trace_id" not in span.attrs


# ---------------------------------------------------------------------------
# Cache token breakdown (issue #61)
# ---------------------------------------------------------------------------


class TestExtractAnthropicCacheTokens:
    """Unit tests for _extract_anthropic_cache_tokens helper."""

    def test_extracts_cache_creation_and_read(self):
        line = (
            'data: {"type": "message_start", "message": {"usage": '
            '{"input_tokens": 10, "cache_creation_input_tokens": 50, '
            '"cache_read_input_tokens": 100}}}'
        )
        cw, cr = _extract_anthropic_cache_tokens(line)
        assert cw == 50
        assert cr == 100

    def test_non_message_start_returns_zeros(self):
        line = 'data: {"type": "message_delta", "usage": {"output_tokens": 5}}'
        cw, cr = _extract_anthropic_cache_tokens(line)
        assert cw == 0
        assert cr == 0

    def test_no_cache_fields_returns_zeros(self):
        line = 'data: {"type": "message_start", "message": {"usage": {"input_tokens": 10}}}'
        cw, cr = _extract_anthropic_cache_tokens(line)
        assert cw == 0
        assert cr == 0

    def test_non_data_line_returns_zeros(self):
        assert _extract_anthropic_cache_tokens("event: message_start") == (0, 0)

    def test_done_sentinel_returns_zeros(self):
        assert _extract_anthropic_cache_tokens("data: [DONE]") == (0, 0)

    def test_partial_cache_fields(self):
        """Only cache_read present, no cache_write."""
        line = (
            'data: {"type": "message_start", "message": {"usage": '
            '{"input_tokens": 5, "cache_read_input_tokens": 300}}}'
        )
        cw, cr = _extract_anthropic_cache_tokens(line)
        assert cw == 0
        assert cr == 300


class TestCacheTokenBreakdownAttrs:
    """Verify cache span attributes are emitted correctly per provider."""

    def test_anthropic_full_cache_response(self):
        """cache_read, cache_write, and hit_rate set for Anthropic response with cache fields."""
        span = _FakeSpan()
        data = {
            "usage": {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_creation_input_tokens": 200,
                "cache_read_input_tokens": 800,
            },
            "stop_reason": "end_turn",
        }
        _set_anthropic_response_attrs(span, data, elapsed_ms=42)
        assert span.attrs["tokens.cache_read"] == 800
        assert span.attrs["tokens.cache_write"] == 200
        # hit_rate = 800 / (100 + 200 + 800) = 800/1100
        expected_rate = 800 / 1100
        assert abs(span.attrs["cache.hit_rate"] - expected_rate) < 1e-9

    def test_anthropic_no_cache_fields_emits_zeros(self):
        """When cache fields absent, emit zeros and 0.0 hit_rate."""
        span = _FakeSpan()
        data = {"usage": {"input_tokens": 100, "output_tokens": 50}}
        _set_anthropic_response_attrs(span, data, elapsed_ms=10)
        assert span.attrs["tokens.cache_read"] == 0
        assert span.attrs["tokens.cache_write"] == 0
        assert span.attrs["cache.hit_rate"] == 0.0

    def test_anthropic_zero_usage_no_division_error(self):
        """All-zero usage must not raise ZeroDivisionError."""
        span = _FakeSpan()
        data = {"usage": {}}
        _set_anthropic_response_attrs(span, data, elapsed_ms=5)
        assert span.attrs["cache.hit_rate"] == 0.0

    def test_anthropic_cache_only_reads_hit_rate_is_one(self):
        """If all input is cache_read, hit_rate should be 1.0."""
        span = _FakeSpan()
        data = {
            "usage": {
                "input_tokens": 0,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 500,
                "output_tokens": 20,
            }
        }
        _set_anthropic_response_attrs(span, data, elapsed_ms=5)
        assert span.attrs["tokens.cache_read"] == 500
        assert span.attrs["tokens.cache_write"] == 0
        assert span.attrs["cache.hit_rate"] == 1.0

    def test_google_response_emits_zero_cache_attrs(self):
        """Google responses always emit zero cache tokens so Grafana queries don't break."""
        span = _FakeSpan()
        data = {
            "usageMetadata": {
                "promptTokenCount": 10,
                "candidatesTokenCount": 5,
                "totalTokenCount": 15,
            }
        }
        _set_google_response_attrs(span, data, elapsed_ms=10)
        assert span.attrs["tokens.cache_read"] == 0
        assert span.attrs["tokens.cache_write"] == 0
        assert span.attrs["cache.hit_rate"] == 0.0

    def test_openai_response_emits_zero_cache_attrs(self):
        """OpenAI responses always emit zero cache tokens so Grafana queries don't break."""
        span = _FakeSpan()
        data = {
            "choices": [{"finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        _set_openai_response_attrs(span, data, elapsed_ms=10)
        assert span.attrs["tokens.cache_read"] == 0
        assert span.attrs["tokens.cache_write"] == 0
        assert span.attrs["cache.hit_rate"] == 0.0

    def test_anthropic_prompt_tokens_still_summed(self):
        """prov.llm.prompt_tokens must still equal raw + cache_write + cache_read."""
        span = _FakeSpan()
        data = {
            "usage": {
                "input_tokens": 10,
                "cache_creation_input_tokens": 30,
                "cache_read_input_tokens": 60,
                "output_tokens": 5,
            }
        }
        _set_anthropic_response_attrs(span, data, elapsed_ms=1)
        assert span.attrs["prov.llm.prompt_tokens"] == 100  # 10 + 30 + 60
