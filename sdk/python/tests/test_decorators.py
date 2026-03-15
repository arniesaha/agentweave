"""Tests for @trace_tool and @trace_agent decorators."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from agentweave import schema
from agentweave.config import AgentWeaveConfig
from agentweave.decorators import trace_agent, trace_llm, trace_tool


@pytest.fixture(autouse=True)
def _setup_test_tracer():
    """Set up an in-memory tracer for each test.

    Rather than fighting OTel's global provider (which refuses override after
    first set), inject our test provider directly into agentweave.exporter._provider.
    """
    import agentweave.exporter as _exporter_mod

    AgentWeaveConfig.reset()
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    # Inject test provider directly — bypasses OTel "no override" restriction
    old_provider = _exporter_mod._provider
    _exporter_mod._provider = provider

    # Set up config without triggering the real exporter
    with patch("agentweave.config.init_tracer"):
        AgentWeaveConfig.setup(
            agent_id="test-agent",
            agent_model="test-model",
            agent_version="1.0.0",
            otel_endpoint="http://localhost:4318",
        )

    yield exporter, provider

    provider.shutdown()
    _exporter_mod._provider = old_provider
    AgentWeaveConfig.reset()


class TestTraceToolSync:
    """Tests for @trace_tool on synchronous functions."""

    def test_bare_decorator(self, _setup_test_tracer):
        exporter, _ = _setup_test_tracer

        @trace_tool
        def my_func(x: int) -> int:
            return x * 2

        result = my_func(5)
        assert result == 10

        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].name == "tool.my_func"

    def test_named_decorator(self, _setup_test_tracer):
        exporter, _ = _setup_test_tracer

        @trace_tool(name="web_search")
        def search(query: str) -> str:
            return f"results for {query}"

        result = search("python")
        assert result == "results for python"

        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].name == "tool.web_search"

    def test_captures_input(self, _setup_test_tracer):
        exporter, _ = _setup_test_tracer

        @trace_tool(name="exec", captures_input=True)
        def exec_cmd(command: str) -> str:
            return "ok"

        exec_cmd("ls -la")

        spans = exporter.get_finished_spans()
        attrs = dict(spans[0].attributes)
        assert schema.PROV_USED in attrs
        assert "ls -la" in attrs[schema.PROV_USED]

    def test_captures_output(self, _setup_test_tracer):
        exporter, _ = _setup_test_tracer

        @trace_tool(name="exec", captures_input=True, captures_output=True)
        def exec_cmd(command: str) -> str:
            return "hello world"

        exec_cmd("echo hello")

        spans = exporter.get_finished_spans()
        attrs = dict(spans[0].attributes)
        assert schema.PROV_WAS_GENERATED_BY in attrs
        assert "hello world" in attrs[f"{schema.PROV_ENTITY}.output.value"]

    def test_prov_agent_attributes(self, _setup_test_tracer):
        exporter, _ = _setup_test_tracer

        @trace_tool
        def my_tool() -> str:
            return "done"

        my_tool()

        spans = exporter.get_finished_spans()
        attrs = dict(spans[0].attributes)
        assert attrs[schema.PROV_AGENT_ID] == "test-agent"
        assert attrs[schema.PROV_AGENT_MODEL] == "test-model"
        assert attrs[schema.PROV_WAS_ASSOCIATED_WITH] == "test-agent"

    def test_exception_handling(self, _setup_test_tracer):
        exporter, _ = _setup_test_tracer

        @trace_tool
        def failing_tool() -> None:
            raise ValueError("something broke")

        with pytest.raises(ValueError, match="something broke"):
            failing_tool()

        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].status.status_code == trace.StatusCode.ERROR

    def test_preserves_function_metadata(self):
        @trace_tool(name="my_tool")
        def documented_func(x: int) -> int:
            """This function has docs."""
            return x

        assert documented_func.__name__ == "documented_func"
        assert documented_func.__doc__ == "This function has docs."


class TestTraceToolAsync:
    """Tests for @trace_tool on async functions."""

    def test_async_tool(self, _setup_test_tracer):
        exporter, _ = _setup_test_tracer

        @trace_tool(name="async_search", captures_input=True)
        async def async_search(query: str) -> str:
            return f"async results for {query}"

        result = asyncio.run(async_search("test"))
        assert result == "async results for test"

        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].name == "tool.async_search"

    def test_async_exception(self, _setup_test_tracer):
        exporter, _ = _setup_test_tracer

        @trace_tool
        async def bad_async():
            raise RuntimeError("async fail")

        with pytest.raises(RuntimeError, match="async fail"):
            asyncio.run(bad_async())

        spans = exporter.get_finished_spans()
        assert spans[0].status.status_code == trace.StatusCode.ERROR


class TestTraceAgent:
    """Tests for @trace_agent decorator."""

    def test_bare_decorator(self, _setup_test_tracer):
        exporter, _ = _setup_test_tracer

        @trace_agent
        def handle_message(message: str) -> str:
            return f"handled: {message}"

        result = handle_message("hello")
        assert result == "handled: hello"

        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].name == "agent.handle_message"
        attrs = dict(spans[0].attributes)
        assert attrs[schema.PROV_ACTIVITY_TYPE] == schema.ACTIVITY_AGENT_TURN
        # OTel gen_ai.* dual-emit
        assert attrs[schema.GEN_AI_OPERATION_NAME] == "invoke_agent"

    def test_named_agent(self, _setup_test_tracer):
        exporter, _ = _setup_test_tracer

        @trace_agent(name="nix_handler", captures_input=True, captures_output=True)
        def handle(msg: str) -> str:
            return "response"

        handle("input message")

        spans = exporter.get_finished_spans()
        assert spans[0].name == "agent.nix_handler"

    def test_async_agent(self, _setup_test_tracer):
        exporter, _ = _setup_test_tracer

        @trace_agent
        async def async_handler(message: str) -> str:
            return f"async: {message}"

        result = asyncio.run(async_handler("hi"))
        assert result == "async: hi"

        spans = exporter.get_finished_spans()
        assert spans[0].name == "agent.async_handler"

    def test_captures_input(self, _setup_test_tracer):
        exporter, _ = _setup_test_tracer

        @trace_agent(name="input_agent", captures_input=True)
        def handle(msg: str) -> str:
            return "response"

        handle("input message")

        spans = exporter.get_finished_spans()
        attrs = dict(spans[0].attributes)
        assert schema.PROV_USED in attrs
        assert "input message" in attrs[schema.PROV_USED]

    def test_captures_output(self, _setup_test_tracer):
        exporter, _ = _setup_test_tracer

        @trace_agent(name="output_agent", captures_output=True)
        def handle(msg: str) -> str:
            return "agent response"

        handle("hello")

        spans = exporter.get_finished_spans()
        attrs = dict(spans[0].attributes)
        assert schema.PROV_WAS_GENERATED_BY in attrs
        assert "agent response" in attrs[f"{schema.PROV_ENTITY}.output.value"]

    def test_captures_input_and_output(self, _setup_test_tracer):
        exporter, _ = _setup_test_tracer

        @trace_agent(name="full_agent", captures_input=True, captures_output=True)
        def handle(msg: str) -> str:
            return "full response"

        handle("full input")

        spans = exporter.get_finished_spans()
        attrs = dict(spans[0].attributes)
        assert "full input" in attrs[schema.PROV_USED]
        assert "full response" in attrs[f"{schema.PROV_ENTITY}.output.value"]

    def test_async_captures_input_output(self, _setup_test_tracer):
        exporter, _ = _setup_test_tracer

        @trace_agent(name="async_cap_agent", captures_input=True, captures_output=True)
        async def handle(msg: str) -> str:
            return "async response"

        result = asyncio.run(handle("async input"))
        assert result == "async response"

        spans = exporter.get_finished_spans()
        attrs = dict(spans[0].attributes)
        assert "async input" in attrs[schema.PROV_USED]
        assert "async response" in attrs[f"{schema.PROV_ENTITY}.output.value"]

    def test_nested_agent_tool_spans(self, _setup_test_tracer):
        exporter, _ = _setup_test_tracer

        @trace_tool(name="inner_tool")
        def inner_tool() -> str:
            return "tool result"

        @trace_agent(name="outer_agent")
        def agent_turn() -> str:
            return inner_tool()

        result = agent_turn()
        assert result == "tool result"

        spans = exporter.get_finished_spans()
        assert len(spans) == 2
        span_names = {s.name for s in spans}
        assert "tool.inner_tool" in span_names
        assert "agent.outer_agent" in span_names

        # inner_tool should be a child of outer_agent
        tool_span = next(s for s in spans if s.name == "tool.inner_tool")
        agent_span = next(s for s in spans if s.name == "agent.outer_agent")
        assert tool_span.context.trace_id == agent_span.context.trace_id


class TestTraceAgentSessionId:
    """Tests for @trace_agent session_id parameter."""

    def test_session_id_sets_attribute(self, _setup_test_tracer):
        exporter, _ = _setup_test_tracer

        @trace_agent(name="session_agent", session_id="conv-abc123")
        def handle(msg: str) -> str:
            return "response"

        handle("hello")

        spans = exporter.get_finished_spans()
        attrs = dict(spans[0].attributes)
        assert attrs[schema.SESSION_ID] == "conv-abc123"
        assert attrs[schema.PROV_SESSION_ID] == "conv-abc123"

    def test_session_id_absent_when_not_provided(self, _setup_test_tracer):
        exporter, _ = _setup_test_tracer

        @trace_agent(name="no_session_agent")
        def handle(msg: str) -> str:
            return "response"

        handle("hello")

        spans = exporter.get_finished_spans()
        attrs = dict(spans[0].attributes)
        assert schema.SESSION_ID not in attrs
        assert schema.PROV_SESSION_ID not in attrs

    def test_session_id_async_agent(self, _setup_test_tracer):
        exporter, _ = _setup_test_tracer

        @trace_agent(name="async_session_agent", session_id="sess-xyz")
        async def handle(msg: str) -> str:
            return "async response"

        import asyncio
        asyncio.run(handle("hi"))

        spans = exporter.get_finished_spans()
        attrs = dict(spans[0].attributes)
        assert attrs[schema.SESSION_ID] == "sess-xyz"
        assert attrs[schema.PROV_SESSION_ID] == "sess-xyz"


class TestTraceLlm:
    """Tests for @trace_llm decorator."""

    def test_basic_llm_span(self, _setup_test_tracer):
        exporter, _ = _setup_test_tracer

        class FakeUsage:
            input_tokens = 150
            output_tokens = 42

        class FakeContent:
            text = "The capital of Portugal is Lisbon."

        class FakeResponse:
            usage = FakeUsage()
            content = [FakeContent()]
            stop_reason = "end_turn"

        @trace_llm(provider="anthropic", model="claude-sonnet-4-6",
                   captures_input=True, captures_output=True)
        def call_claude(messages: list) -> FakeResponse:
            return FakeResponse()

        result = call_claude(messages=[{"role": "user", "content": "Capital of Portugal?"}])
        assert result.stop_reason == "end_turn"

        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        attrs = dict(spans[0].attributes)

        assert spans[0].name == "llm.claude-sonnet-4-6"
        assert attrs["prov.activity.type"] == "llm_call"
        assert attrs["prov.llm.provider"] == "anthropic"
        assert attrs["prov.llm.model"] == "claude-sonnet-4-6"
        assert attrs["prov.llm.prompt_tokens"] == 150
        assert attrs["prov.llm.completion_tokens"] == 42
        assert attrs["prov.llm.total_tokens"] == 192
        assert attrs["prov.llm.stop_reason"] == "end_turn"
        assert "Lisbon" in attrs.get("prov.llm.response_preview", "")

        # OTel gen_ai.* dual-emit assertions
        assert attrs[schema.GEN_AI_OPERATION_NAME] == "chat"
        assert attrs[schema.GEN_AI_SYSTEM] == "anthropic"
        assert attrs[schema.GEN_AI_REQUEST_MODEL] == "claude-sonnet-4-6"
        assert attrs[schema.GEN_AI_USAGE_INPUT_TOKENS] == 150
        assert attrs[schema.GEN_AI_USAGE_OUTPUT_TOKENS] == 42
        assert list(attrs[schema.GEN_AI_RESPONSE_FINISH_REASONS]) == ["end_turn"]

    def test_openai_token_convention(self, _setup_test_tracer):
        exporter, _ = _setup_test_tracer

        class FakeUsage:
            prompt_tokens = 100
            completion_tokens = 30

        class FakeMessage:
            content = "Paris is the capital of France."

        class FakeChoice:
            message = FakeMessage()
            finish_reason = "stop"

        class FakeResponse:
            usage = FakeUsage()
            choices = [FakeChoice()]
            finish_reason = "stop"

        @trace_llm(provider="openai", model="gpt-4o", captures_output=True)
        def call_openai(prompt: str) -> FakeResponse:
            return FakeResponse()

        call_openai(prompt="Capital of France?")

        spans = exporter.get_finished_spans()
        attrs = dict(spans[0].attributes)
        assert attrs["prov.llm.provider"] == "openai"
        assert attrs["prov.llm.prompt_tokens"] == 100
        assert attrs["prov.llm.completion_tokens"] == 30
        assert attrs["prov.llm.total_tokens"] == 130
        assert "Paris" in attrs.get("prov.llm.response_preview", "")

        # OTel gen_ai.* dual-emit assertions
        assert attrs[schema.GEN_AI_OPERATION_NAME] == "chat"
        assert attrs[schema.GEN_AI_SYSTEM] == "openai"
        assert attrs[schema.GEN_AI_REQUEST_MODEL] == "gpt-4o"
        assert attrs[schema.GEN_AI_USAGE_INPUT_TOKENS] == 100
        assert attrs[schema.GEN_AI_USAGE_OUTPUT_TOKENS] == 30

    def test_nested_agent_llm_tool_trace(self, _setup_test_tracer):
        """Full chain: agent_turn → llm_call → tool_call, all same trace_id."""
        exporter, _ = _setup_test_tracer

        class FakeResp:
            class usage:
                input_tokens = 200
                output_tokens = 50
            content = [type("C", (), {"text": "delegating to max"})()]
            stop_reason = "tool_use"

        @trace_tool(name="delegate_to_max")
        def delegate_to_max(task: str) -> dict:
            return {"jobs_found": 4}

        @trace_llm(provider="anthropic", model="claude-sonnet-4-6")
        def call_claude(messages: list) -> FakeResp:
            return FakeResp()

        @trace_agent(name="nix")
        def handle(message: str) -> str:
            call_claude(messages=[{"role": "user", "content": message}])
            result = delegate_to_max(task="scrape linkedin")
            return f"found {result['jobs_found']} jobs"

        handle("scrape linkedin for EM roles")

        spans = exporter.get_finished_spans()
        assert len(spans) == 3

        names = {s.name for s in spans}
        assert names == {"agent.nix", "llm.claude-sonnet-4-6", "tool.delegate_to_max"}

        # All spans share the same trace_id
        trace_ids = {s.context.trace_id for s in spans}
        assert len(trace_ids) == 1

        # llm and tool are children of agent
        agent_span = next(s for s in spans if s.name == "agent.nix")
        llm_span = next(s for s in spans if s.name == "llm.claude-sonnet-4-6")
        tool_span = next(s for s in spans if s.name == "tool.delegate_to_max")
        assert llm_span.parent.span_id == agent_span.context.span_id
        assert tool_span.parent.span_id == agent_span.context.span_id

        # Token counts on llm span
        llm_attrs = dict(llm_span.attributes)
        assert llm_attrs["prov.llm.total_tokens"] == 250

class TestTraceAgentDeterministicTraceId:
    """Tests for @trace_agent traceId parameter (deterministic trace IDs)."""

    def test_valid_hex_trace_id(self, _setup_test_tracer):
        """32-char hex traceId sets the OTel trace ID on the span."""
        exporter, _ = _setup_test_tracer
        hex_id = "a" * 32  # valid 32-char hex

        @trace_agent(name="det_agent", traceId=hex_id)
        def handle(msg: str) -> str:
            return "ok"

        handle("hi")

        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        span = spans[0]
        actual_trace_id = format(span.context.trace_id, "032x")
        assert actual_trace_id == hex_id
        attrs = dict(span.attributes)
        assert attrs[schema.AGENTWEAVE_TRACE_ID] == hex_id

    def test_arbitrary_string_trace_id_is_hashed(self, _setup_test_tracer):
        """Non-hex traceId is SHA-256 hashed to produce a valid OTel trace ID."""
        import hashlib
        exporter, _ = _setup_test_tracer
        arbitrary_id = "order-abc123-attempt-1"

        @trace_agent(name="hash_agent", traceId=arbitrary_id)
        def handle(msg: str) -> str:
            return "ok"

        handle("hi")

        spans = exporter.get_finished_spans()
        span = spans[0]
        expected_hex = hashlib.sha256(arbitrary_id.encode()).hexdigest()[:32]
        assert format(span.context.trace_id, "032x") == expected_hex
        attrs = dict(span.attributes)
        assert attrs[schema.AGENTWEAVE_TRACE_ID] == arbitrary_id

    def test_same_trace_id_across_retries(self, _setup_test_tracer):
        """Two calls with the same traceId produce spans with identical trace IDs."""
        exporter, _ = _setup_test_tracer
        trace_id = "retry-request-unique-key-42"

        @trace_agent(name="retry_agent", traceId=trace_id)
        def handle(msg: str) -> str:
            return "ok"

        handle("first call")
        handle("second call (retry)")

        spans = exporter.get_finished_spans()
        assert len(spans) == 2
        assert spans[0].context.trace_id == spans[1].context.trace_id

    def test_no_trace_id_uses_random(self, _setup_test_tracer):
        """Without traceId, two calls produce different trace IDs."""
        exporter, _ = _setup_test_tracer

        @trace_agent(name="random_agent")
        def handle(msg: str) -> str:
            return "ok"

        handle("first call")
        handle("second call")

        spans = exporter.get_finished_spans()
        assert len(spans) == 2
        assert spans[0].context.trace_id != spans[1].context.trace_id

    def test_trace_id_propagates_to_child_spans(self, _setup_test_tracer):
        """Child spans (e.g. tool calls) inherit the deterministic trace ID."""
        exporter, _ = _setup_test_tracer
        from agentweave.decorators import trace_tool
        hex_id = "b" * 32

        @trace_tool(name="child_tool")
        def my_tool() -> str:
            return "tool result"

        @trace_agent(name="parent_agent", traceId=hex_id)
        def handle(msg: str) -> str:
            return my_tool()

        handle("hi")

        spans = exporter.get_finished_spans()
        assert len(spans) == 2
        for span in spans:
            assert format(span.context.trace_id, "032x") == hex_id

    def test_async_agent_with_trace_id(self, _setup_test_tracer):
        """traceId also works on async agents."""
        exporter, _ = _setup_test_tracer
        hex_id = "c" * 32

        @trace_agent(name="async_det_agent", traceId=hex_id)
        async def handle(msg: str) -> str:
            return "async ok"

        asyncio.run(handle("hi"))

        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        assert format(spans[0].context.trace_id, "032x") == hex_id

    def test_agentweave_trace_id_attribute_absent_without_trace_id(self, _setup_test_tracer):
        """agentweave.trace_id attribute is only set when traceId is provided."""
        exporter, _ = _setup_test_tracer

        @trace_agent(name="no_trace_id_agent")
        def handle(msg: str) -> str:
            return "ok"

        handle("hi")

        spans = exporter.get_finished_spans()
        attrs = dict(spans[0].attributes)
        assert schema.AGENTWEAVE_TRACE_ID not in attrs


