"""Tests for session_id propagation across auto-instrument and decorators.

Covers nexus#29: ~40% of lakehouse.spans rows had NULL session_id because
auto_instrument() LLM spans and @trace_tool spans never read any session
context.  These tests verify the ContextVar-based propagation introduced in
agentweave/context.py.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import types
from unittest.mock import patch

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import agentweave
from agentweave import schema
from agentweave.config import AgentWeaveConfig
from agentweave import context as aw_context
from agentweave.decorators import trace_agent, trace_tool
from agentweave.instrument import (
    auto_instrument,
    uninstrument,
    _active_patches,
    _proxy_env_overrides,
)


# ---------------------------------------------------------------------------
# Fake Anthropic SDK — minimal surface used by _patch_anthropic
# ---------------------------------------------------------------------------

class _FakeUsage:
    input_tokens = 10
    output_tokens = 5


class _FakeText:
    text = "ok"


class _FakeResponse:
    usage = _FakeUsage()
    content = [_FakeText()]
    stop_reason = "end_turn"


def _install_fake_anthropic() -> None:
    """Install a minimal anthropic.resources stub with Messages.create."""
    pkg = types.ModuleType("anthropic")
    resources = types.ModuleType("anthropic.resources")

    class Messages:
        def create(self, *args, **kwargs):  # noqa: D401
            return _FakeResponse()

    class AsyncMessages:
        async def create(self, *args, **kwargs):  # noqa: D401
            return _FakeResponse()

    resources.Messages = Messages
    resources.AsyncMessages = AsyncMessages
    pkg.resources = resources
    sys.modules["anthropic"] = pkg
    sys.modules["anthropic.resources"] = resources


def _uninstall_fake_anthropic() -> None:
    sys.modules.pop("anthropic", None)
    sys.modules.pop("anthropic.resources", None)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tracer_exporter():
    """Inject an in-memory exporter for span assertions."""
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

    yield exporter

    provider.shutdown()
    _exporter_mod._provider = old_provider
    AgentWeaveConfig.reset()


@pytest.fixture(autouse=True)
def _cleanup():
    """Reset env, patches, and the warn-once flag between tests."""
    saved_env = {
        k: os.environ.get(k)
        for k in ("AGENTWEAVE_SESSION_ID", "AGENTWEAVE_DEBUG")
    }
    for k in saved_env:
        os.environ.pop(k, None)
    aw_context._reset_warned_for_tests()

    yield

    uninstrument()
    _active_patches.clear()
    _proxy_env_overrides.clear()
    _uninstall_fake_anthropic()
    for k, v in saved_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    aw_context._reset_warned_for_tests()


def _llm_span(spans):
    """Helper: return the first span whose name starts with 'llm.'."""
    matches = [s for s in spans if s.name.startswith("llm.")]
    assert matches, f"no llm.* span in {[s.name for s in spans]}"
    return matches[0]


def _make_anthropic_call():
    """Trigger a single auto-instrumented Anthropic call."""
    import anthropic.resources as r
    return r.Messages().create(model="claude-test-model", messages=[{"role": "user", "content": "hi"}])


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestAutoInstrumentSessionPropagation:

    def test_auto_instrument_stamps_session_id_from_contextvar(self, tracer_exporter):
        _install_fake_anthropic()
        auto_instrument(providers=["anthropic"])

        with agentweave.session_scope("sess-from-cv"):
            _make_anthropic_call()

        attrs = dict(_llm_span(tracer_exporter.get_finished_spans()).attributes)
        assert attrs[schema.SESSION_ID] == "sess-from-cv"
        assert attrs[schema.PROV_SESSION_ID] == "sess-from-cv"

    def test_auto_instrument_stamps_session_id_from_env_var(self, tracer_exporter):
        _install_fake_anthropic()
        os.environ["AGENTWEAVE_SESSION_ID"] = "sess-from-env"
        auto_instrument(providers=["anthropic"])

        _make_anthropic_call()

        attrs = dict(_llm_span(tracer_exporter.get_finished_spans()).attributes)
        assert attrs[schema.SESSION_ID] == "sess-from-env"
        assert attrs[schema.PROV_SESSION_ID] == "sess-from-env"

    def test_contextvar_wins_over_env_var(self, tracer_exporter):
        _install_fake_anthropic()
        os.environ["AGENTWEAVE_SESSION_ID"] = "sess-env-loser"
        auto_instrument(providers=["anthropic"])

        with agentweave.session_scope("sess-cv-winner"):
            _make_anthropic_call()

        attrs = dict(_llm_span(tracer_exporter.get_finished_spans()).attributes)
        assert attrs[schema.SESSION_ID] == "sess-cv-winner"

    def test_no_session_id_emits_no_attribute_no_exception(self, tracer_exporter):
        _install_fake_anthropic()
        auto_instrument(providers=["anthropic"])

        # Should not raise; just emit a span without session.id
        _make_anthropic_call()

        attrs = dict(_llm_span(tracer_exporter.get_finished_spans()).attributes)
        assert schema.SESSION_ID not in attrs
        assert schema.PROV_SESSION_ID not in attrs

    def test_async_contextvar_isolation(self, tracer_exporter):
        _install_fake_anthropic()
        auto_instrument(providers=["anthropic"])
        import anthropic.resources as r

        async def _task(sid: str):
            with agentweave.session_scope(sid):
                # Yield to event loop so both tasks interleave
                await asyncio.sleep(0)
                await r.AsyncMessages().create(
                    model="claude-test-model", messages=[{"role": "user", "content": sid}]
                )

        async def _run():
            await asyncio.gather(_task("sess-A"), _task("sess-B"))

        asyncio.run(_run())

        spans = [s for s in tracer_exporter.get_finished_spans() if s.name.startswith("llm.")]
        assert len(spans) == 2
        sids = {dict(s.attributes).get(schema.SESSION_ID) for s in spans}
        assert sids == {"sess-A", "sess-B"}, "ContextVar leaked across async tasks"


class TestTraceToolSessionPropagation:

    def test_trace_tool_inherits_session_id(self, tracer_exporter):

        @trace_tool
        def my_tool() -> str:
            return "ok"

        with agentweave.session_scope("sess-tool"):
            my_tool()

        spans = tracer_exporter.get_finished_spans()
        tool_span = next(s for s in spans if s.name.startswith("tool."))
        attrs = dict(tool_span.attributes)
        assert attrs[schema.SESSION_ID] == "sess-tool"
        assert attrs[schema.PROV_SESSION_ID] == "sess-tool"


class TestTraceAgentPrecedence:

    def test_explicit_kwarg_wins_over_contextvar(self, tracer_exporter):

        @trace_agent(session_id="sess-explicit")
        def handle(msg: str) -> str:
            return msg

        with agentweave.session_scope("sess-cv-loser"):
            handle("hi")

        spans = tracer_exporter.get_finished_spans()
        agent_span = next(s for s in spans if s.name.startswith("agent."))
        attrs = dict(agent_span.attributes)
        assert attrs[schema.SESSION_ID] == "sess-explicit"

    def test_trace_agent_propagates_to_nested_tool(self, tracer_exporter):
        """Body of @trace_agent should set ContextVar so nested @trace_tool inherits."""

        @trace_tool
        def inner_tool() -> str:
            return "x"

        @trace_agent(session_id="sess-agent")
        def outer() -> str:
            return inner_tool()

        outer()

        spans = tracer_exporter.get_finished_spans()
        tool_span = next(s for s in spans if s.name.startswith("tool."))
        agent_span = next(s for s in spans if s.name.startswith("agent."))
        assert dict(tool_span.attributes)[schema.SESSION_ID] == "sess-agent"
        assert dict(agent_span.attributes)[schema.SESSION_ID] == "sess-agent"


class TestDebugWarning:

    def test_debug_warning_fires_once(self, tracer_exporter, caplog):
        _install_fake_anthropic()
        os.environ["AGENTWEAVE_DEBUG"] = "1"
        auto_instrument(providers=["anthropic"])

        with caplog.at_level(logging.WARNING, logger="agentweave"):
            _make_anthropic_call()
            _make_anthropic_call()

        warnings = [
            r for r in caplog.records
            if r.name == "agentweave" and "session_id" in r.getMessage()
        ]
        assert len(warnings) == 1, (
            f"expected exactly one warning, got {len(warnings)}: {[r.getMessage() for r in warnings]}"
        )

    def test_no_warning_without_debug_flag(self, tracer_exporter, caplog):
        _install_fake_anthropic()
        # AGENTWEAVE_DEBUG intentionally unset
        auto_instrument(providers=["anthropic"])

        with caplog.at_level(logging.WARNING, logger="agentweave"):
            _make_anthropic_call()

        warnings = [
            r for r in caplog.records
            if r.name == "agentweave" and "session_id" in r.getMessage()
        ]
        assert warnings == []
