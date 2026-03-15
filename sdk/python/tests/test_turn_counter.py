"""Tests for the per-session LLM turn counter (agent.turn_count attribute)."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from agentweave import schema
from agentweave.config import AgentWeaveConfig
from agentweave.decorators import trace_agent, trace_llm, trace_tool, _turn_counter


# ---------------------------------------------------------------------------
# Fixture — reuses the same pattern as test_decorators.py
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _setup_test_tracer():
    import agentweave.exporter as _exporter_mod

    AgentWeaveConfig.reset()
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    old_provider = _exporter_mod._provider
    _exporter_mod._provider = provider

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


# ---------------------------------------------------------------------------
# Helpers — fake LLM response objects
# ---------------------------------------------------------------------------

class _FakeUsage:
    input_tokens = 10
    output_tokens = 5


class _FakeResponse:
    usage = _FakeUsage()
    content = []
    stop_reason = "end_turn"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestTurnCounterSingleSession:
    """Basic turn counter increments within a single @trace_agent session."""

    def test_single_llm_call_is_turn_1(self, _setup_test_tracer):
        exporter, _ = _setup_test_tracer

        @trace_llm(provider="anthropic", model="claude-test")
        def call_llm(messages: list) -> _FakeResponse:
            return _FakeResponse()

        @trace_agent(name="one_turn_agent")
        def agent(msg: str) -> str:
            call_llm(messages=[])
            return "done"

        agent("hello")

        spans = exporter.get_finished_spans()
        llm_span = next(s for s in spans if s.name.startswith("llm."))
        attrs = dict(llm_span.attributes)
        assert attrs[schema.AGENT_TURN_COUNT] == 1

    def test_three_llm_calls_increment_to_3(self, _setup_test_tracer):
        exporter, _ = _setup_test_tracer

        @trace_llm(provider="anthropic", model="claude-test")
        def call_llm(messages: list) -> _FakeResponse:
            return _FakeResponse()

        @trace_agent(name="three_turn_agent")
        def agent(msg: str) -> str:
            call_llm(messages=[])  # turn 1
            call_llm(messages=[])  # turn 2
            call_llm(messages=[])  # turn 3
            return "done"

        agent("hello")

        spans = exporter.get_finished_spans()
        llm_spans = sorted(
            [s for s in spans if s.name.startswith("llm.")],
            key=lambda s: s.start_time,
        )
        assert len(llm_spans) == 3
        turn_counts = [dict(s.attributes)[schema.AGENT_TURN_COUNT] for s in llm_spans]
        assert turn_counts == [1, 2, 3]

    def test_counter_resets_between_sessions(self, _setup_test_tracer):
        """Each @trace_agent invocation starts a fresh counter from 0."""
        exporter, _ = _setup_test_tracer

        @trace_llm(provider="anthropic", model="claude-test")
        def call_llm(messages: list) -> _FakeResponse:
            return _FakeResponse()

        @trace_agent(name="resetting_agent")
        def agent(msg: str) -> str:
            call_llm(messages=[])
            call_llm(messages=[])
            return "done"

        # First session: turns 1, 2
        agent("first call")
        # Second session: should restart at 1, 2 (not 3, 4)
        agent("second call")

        spans = exporter.get_finished_spans()
        llm_spans = sorted(
            [s for s in spans if s.name.startswith("llm.")],
            key=lambda s: s.start_time,
        )
        assert len(llm_spans) == 4
        turn_counts = [dict(s.attributes)[schema.AGENT_TURN_COUNT] for s in llm_spans]
        # Session 1 → [1, 2]; Session 2 → [1, 2]
        assert turn_counts == [1, 2, 1, 2]


class TestTurnCounterWithTools:
    """Turn counter only counts LLM calls, not tool calls."""

    def test_tool_calls_not_counted(self, _setup_test_tracer):
        exporter, _ = _setup_test_tracer

        @trace_tool(name="search")
        def search(q: str) -> str:
            return "results"

        @trace_llm(provider="anthropic", model="claude-test")
        def call_llm(messages: list) -> _FakeResponse:
            return _FakeResponse()

        @trace_agent(name="react_agent")
        def agent(msg: str) -> str:
            call_llm(messages=[])  # turn 1 — decides to search
            search("query")
            call_llm(messages=[])  # turn 2 — processes results
            return "answer"

        agent("what is X?")

        spans = exporter.get_finished_spans()
        llm_spans = sorted(
            [s for s in spans if s.name.startswith("llm.")],
            key=lambda s: s.start_time,
        )
        assert len(llm_spans) == 2
        turn_counts = [dict(s.attributes)[schema.AGENT_TURN_COUNT] for s in llm_spans]
        assert turn_counts == [1, 2]

        # Tool span must NOT have agent.turn_count
        tool_span = next(s for s in spans if s.name == "tool.search")
        assert schema.AGENT_TURN_COUNT not in dict(tool_span.attributes)


class TestTurnCounterAsync:
    """Turn counter works correctly with async decorated functions."""

    def test_async_agent_and_llm(self, _setup_test_tracer):
        exporter, _ = _setup_test_tracer

        @trace_llm(provider="anthropic", model="claude-async")
        async def call_llm(messages: list) -> _FakeResponse:
            return _FakeResponse()

        @trace_agent(name="async_agent")
        async def agent(msg: str) -> str:
            await call_llm(messages=[])  # turn 1
            await call_llm(messages=[])  # turn 2
            return "async done"

        asyncio.run(agent("async hello"))

        spans = exporter.get_finished_spans()
        llm_spans = sorted(
            [s for s in spans if s.name.startswith("llm.")],
            key=lambda s: s.start_time,
        )
        turn_counts = [dict(s.attributes)[schema.AGENT_TURN_COUNT] for s in llm_spans]
        assert turn_counts == [1, 2]

    def test_async_counter_resets_per_session(self, _setup_test_tracer):
        exporter, _ = _setup_test_tracer

        @trace_llm(provider="anthropic", model="claude-async")
        async def call_llm(messages: list) -> _FakeResponse:
            return _FakeResponse()

        @trace_agent(name="async_reset_agent")
        async def agent(msg: str) -> str:
            await call_llm(messages=[])
            return "done"

        asyncio.run(agent("first"))
        asyncio.run(agent("second"))

        spans = exporter.get_finished_spans()
        llm_spans = sorted(
            [s for s in spans if s.name.startswith("llm.")],
            key=lambda s: s.start_time,
        )
        turn_counts = [dict(s.attributes)[schema.AGENT_TURN_COUNT] for s in llm_spans]
        # Both sessions start at 1
        assert turn_counts == [1, 1]


class TestTurnCounterWithoutAgent:
    """@trace_llm outside of @trace_agent still increments (no reset — no agent wrapping)."""

    def test_llm_without_agent_still_sets_attribute(self, _setup_test_tracer):
        """Turn counter increments even without @trace_agent wrapping (uses whatever context value is set)."""
        exporter, _ = _setup_test_tracer

        # Reset to a known state
        _turn_counter.set(0)

        @trace_llm(provider="anthropic", model="claude-standalone")
        def call_llm(messages: list) -> _FakeResponse:
            return _FakeResponse()

        call_llm(messages=[])

        spans = exporter.get_finished_spans()
        llm_span = spans[0]
        attrs = dict(llm_span.attributes)
        # Without an agent wrapper, default is 0 → increments to 1
        assert attrs[schema.AGENT_TURN_COUNT] == 1


class TestTurnCounterNestedAgentLlm:
    """Nested agent + multiple LLM calls — the canonical ReAct pattern."""

    def test_react_loop_turn_counts(self, _setup_test_tracer):
        """Simulates a 3-turn ReAct loop: think→act→observe→think→act→observe→answer."""
        exporter, _ = _setup_test_tracer

        @trace_tool(name="calculator")
        def calculator(expr: str) -> str:
            return "42"

        @trace_llm(provider="anthropic", model="claude-react")
        def think(messages: list) -> _FakeResponse:
            return _FakeResponse()

        @trace_agent(name="react_loop")
        def run_react(query: str) -> str:
            # Turn 1: initial reasoning
            think(messages=[{"role": "user", "content": query}])
            calculator("2 + 2")
            # Turn 2: incorporate observation
            think(messages=[{"role": "user", "content": "observation 1"}])
            calculator("3 * 14")
            # Turn 3: final answer
            think(messages=[{"role": "user", "content": "observation 2"}])
            return "42 is the answer"

        result = run_react("What is the meaning of life?")
        assert result == "42 is the answer"

        spans = exporter.get_finished_spans()
        llm_spans = sorted(
            [s for s in spans if s.name == "llm.claude-react"],
            key=lambda s: s.start_time,
        )
        assert len(llm_spans) == 3

        turn_counts = [dict(s.attributes)[schema.AGENT_TURN_COUNT] for s in llm_spans]
        assert turn_counts == [1, 2, 3], f"Expected [1, 2, 3], got {turn_counts}"

        # All spans share the same trace
        trace_ids = {s.context.trace_id for s in spans}
        assert len(trace_ids) == 1

        # LLM spans are children of the agent span
        agent_span = next(s for s in spans if s.name == "agent.react_loop")
        for llm_span in llm_spans:
            assert llm_span.parent.span_id == agent_span.context.span_id
