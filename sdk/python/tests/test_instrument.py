"""Tests for auto_instrument() / uninstrument() monkey-patching."""

from __future__ import annotations

import asyncio
import sys
import types
from unittest.mock import patch

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from agentweave import schema
from agentweave.config import AgentWeaveConfig
from agentweave.decorators import trace_llm
from agentweave.instrument import auto_instrument, uninstrument, _active_patches


# ---------------------------------------------------------------------------
# Fake SDK modules
# ---------------------------------------------------------------------------

class _FakeUsageAnthropic:
    input_tokens = 100
    output_tokens = 50


class _FakeContentBlock:
    text = "Hello from Claude."


class _FakeAnthropicResponse:
    usage = _FakeUsageAnthropic()
    content = [_FakeContentBlock()]
    stop_reason = "end_turn"


class _FakeUsageOpenAI:
    prompt_tokens = 80
    completion_tokens = 40


class _FakeOAIMessage:
    content = "Hello from GPT."


class _FakeOAIChoice:
    message = _FakeOAIMessage()
    finish_reason = "stop"


class _FakeOpenAIResponse:
    usage = _FakeUsageOpenAI()
    choices = [_FakeOAIChoice()]


def _install_fake_anthropic():
    """Register a fake ``anthropic`` package in sys.modules."""
    mod = types.ModuleType("anthropic")
    resources = types.ModuleType("anthropic.resources")

    class Messages:
        def create(self, *, model="claude-sonnet-4-6", messages=None, **kw):
            return _FakeAnthropicResponse()

    class AsyncMessages:
        async def create(self, *, model="claude-sonnet-4-6", messages=None, **kw):
            return _FakeAnthropicResponse()

    resources.Messages = Messages
    resources.AsyncMessages = AsyncMessages
    mod.resources = resources
    sys.modules["anthropic"] = mod
    sys.modules["anthropic.resources"] = resources
    return mod


def _install_fake_openai():
    """Register a fake ``openai`` package in sys.modules."""
    openai_mod = types.ModuleType("openai")
    resources_mod = types.ModuleType("openai.resources")
    chat_mod = types.ModuleType("openai.resources.chat")
    completions_mod = types.ModuleType("openai.resources.chat.completions")

    class Completions:
        def create(self, *, model="gpt-4o", messages=None, **kw):
            return _FakeOpenAIResponse()

    class AsyncCompletions:
        async def create(self, *, model="gpt-4o", messages=None, **kw):
            return _FakeOpenAIResponse()

    completions_mod.Completions = Completions
    completions_mod.AsyncCompletions = AsyncCompletions
    chat_mod.completions = completions_mod
    resources_mod.chat = chat_mod
    openai_mod.resources = resources_mod

    sys.modules["openai"] = openai_mod
    sys.modules["openai.resources"] = resources_mod
    sys.modules["openai.resources.chat"] = chat_mod
    sys.modules["openai.resources.chat.completions"] = completions_mod
    return openai_mod


def _uninstall_fake(prefix: str):
    """Remove all fake modules with the given prefix from sys.modules."""
    to_remove = [k for k in sys.modules if k == prefix or k.startswith(prefix + ".")]
    for k in to_remove:
        del sys.modules[k]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _setup_test_tracer():
    """In-memory OTel tracer — same pattern as test_decorators.py."""
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


@pytest.fixture(autouse=True)
def _cleanup_patches():
    """Ensure auto-instrumentation is cleaned up between tests."""
    yield
    uninstrument()
    _active_patches.clear()
    _uninstall_fake("anthropic")
    _uninstall_fake("openai")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestAnthropicInstrumentation:

    def test_anthropic_sync(self, _setup_test_tracer):
        exporter, _ = _setup_test_tracer
        _install_fake_anthropic()
        auto_instrument(providers=["anthropic"])

        import anthropic.resources
        client = anthropic.resources.Messages()
        resp = client.create(model="claude-sonnet-4-6", messages=[{"role": "user", "content": "hi"}])
        assert resp.stop_reason == "end_turn"

        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        attrs = dict(spans[0].attributes)
        assert spans[0].name == "llm.claude-sonnet-4-6"
        assert attrs[schema.PROV_ACTIVITY_TYPE] == schema.ACTIVITY_LLM_CALL
        assert attrs[schema.PROV_LLM_PROVIDER] == "anthropic"
        assert attrs[schema.PROV_LLM_PROMPT_TOKENS] == 100
        assert attrs[schema.PROV_LLM_COMPLETION_TOKENS] == 50
        assert attrs[schema.PROV_LLM_TOTAL_TOKENS] == 150
        assert attrs[schema.AUTO_INSTRUMENTED] is True
        # OTel gen_ai.* dual-emit
        assert attrs[schema.GEN_AI_OPERATION_NAME] == "chat"
        assert attrs[schema.GEN_AI_SYSTEM] == "anthropic"
        assert attrs[schema.GEN_AI_REQUEST_MODEL] == "claude-sonnet-4-6"
        assert attrs[schema.GEN_AI_USAGE_INPUT_TOKENS] == 100
        assert attrs[schema.GEN_AI_USAGE_OUTPUT_TOKENS] == 50
        assert list(attrs[schema.GEN_AI_RESPONSE_FINISH_REASONS]) == ["end_turn"]

    def test_anthropic_async(self, _setup_test_tracer):
        exporter, _ = _setup_test_tracer
        _install_fake_anthropic()
        auto_instrument(providers=["anthropic"])

        import anthropic.resources

        async def _run():
            client = anthropic.resources.AsyncMessages()
            return await client.create(model="claude-sonnet-4-6", messages=[])

        resp = asyncio.run(_run())
        assert resp.stop_reason == "end_turn"

        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].name == "llm.claude-sonnet-4-6"


class TestOpenAIInstrumentation:

    def test_openai_sync(self, _setup_test_tracer):
        exporter, _ = _setup_test_tracer
        _install_fake_openai()
        auto_instrument(providers=["openai"])

        import openai.resources.chat.completions as comp_mod
        client = comp_mod.Completions()
        resp = client.create(model="gpt-4o", messages=[])

        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        attrs = dict(spans[0].attributes)
        assert spans[0].name == "llm.gpt-4o"
        assert attrs[schema.PROV_LLM_PROVIDER] == "openai"
        assert attrs[schema.PROV_LLM_PROMPT_TOKENS] == 80
        assert attrs[schema.PROV_LLM_COMPLETION_TOKENS] == 40
        assert attrs[schema.PROV_LLM_TOTAL_TOKENS] == 120
        # OTel gen_ai.* dual-emit
        assert attrs[schema.GEN_AI_OPERATION_NAME] == "chat"
        assert attrs[schema.GEN_AI_SYSTEM] == "openai"
        assert attrs[schema.GEN_AI_REQUEST_MODEL] == "gpt-4o"
        assert attrs[schema.GEN_AI_USAGE_INPUT_TOKENS] == 80
        assert attrs[schema.GEN_AI_USAGE_OUTPUT_TOKENS] == 40

    def test_openai_async(self, _setup_test_tracer):
        exporter, _ = _setup_test_tracer
        _install_fake_openai()
        auto_instrument(providers=["openai"])

        import openai.resources.chat.completions as comp_mod

        async def _run():
            client = comp_mod.AsyncCompletions()
            return await client.create(model="gpt-4o", messages=[])

        asyncio.run(_run())

        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].name == "llm.gpt-4o"


class TestEdgeCases:

    def test_no_double_trace(self, _setup_test_tracer):
        """When an explicit @trace_llm wraps a patched create(), only one llm_call span."""
        exporter, _ = _setup_test_tracer
        _install_fake_anthropic()
        auto_instrument(providers=["anthropic"])

        import anthropic.resources

        @trace_llm(provider="anthropic", model="claude-sonnet-4-6")
        def call_claude(messages):
            client = anthropic.resources.Messages()
            return client.create(model="claude-sonnet-4-6", messages=messages)

        call_claude(messages=[{"role": "user", "content": "hi"}])

        spans = exporter.get_finished_spans()
        llm_spans = [s for s in spans if dict(s.attributes).get(schema.PROV_ACTIVITY_TYPE) == schema.ACTIVITY_LLM_CALL]
        assert len(llm_spans) == 1  # no double-trace

    def test_selective_providers(self, _setup_test_tracer):
        """auto_instrument(providers=['anthropic']) should NOT patch OpenAI."""
        exporter, _ = _setup_test_tracer
        _install_fake_anthropic()
        _install_fake_openai()
        auto_instrument(providers=["anthropic"])

        import openai.resources.chat.completions as comp_mod
        client = comp_mod.Completions()
        client.create(model="gpt-4o", messages=[])

        # OpenAI call should not produce a span
        spans = exporter.get_finished_spans()
        assert len(spans) == 0

    def test_uninstrument(self, _setup_test_tracer):
        """After uninstrument(), calls produce no auto spans."""
        exporter, _ = _setup_test_tracer
        _install_fake_anthropic()
        auto_instrument(providers=["anthropic"])
        uninstrument()

        import anthropic.resources
        client = anthropic.resources.Messages()
        client.create(model="claude-sonnet-4-6", messages=[])

        spans = exporter.get_finished_spans()
        assert len(spans) == 0

    def test_missing_sdk_skipped(self):
        """auto_instrument() with uninstalled SDK doesn't raise."""
        _uninstall_fake("anthropic")
        _uninstall_fake("openai")
        # Should not raise — silently skips missing SDKs
        auto_instrument()

    def test_idempotent(self, _setup_test_tracer):
        """Calling auto_instrument() twice doesn't double-patch."""
        exporter, _ = _setup_test_tracer
        _install_fake_anthropic()
        auto_instrument(providers=["anthropic"])
        auto_instrument(providers=["anthropic"])  # second call — no-op

        import anthropic.resources
        client = anthropic.resources.Messages()
        client.create(model="claude-sonnet-4-6", messages=[])

        spans = exporter.get_finished_spans()
        assert len(spans) == 1  # not 2

    def test_captures_output(self, _setup_test_tracer):
        """captures_output=True records the response preview."""
        exporter, _ = _setup_test_tracer
        _install_fake_anthropic()
        auto_instrument(providers=["anthropic"], captures_output=True)

        import anthropic.resources
        client = anthropic.resources.Messages()
        client.create(model="claude-sonnet-4-6", messages=[])

        spans = exporter.get_finished_spans()
        attrs = dict(spans[0].attributes)
        assert "Hello from Claude" in attrs.get(schema.PROV_LLM_RESPONSE_PREVIEW, "")
