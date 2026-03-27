"""Tests for Google SDK instrumentation and proxy mode in auto_instrument()."""

from __future__ import annotations

import asyncio
import os
import sys
import types
from unittest.mock import patch

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from agentweave import schema
from agentweave.config import AgentWeaveConfig
from agentweave.instrument import auto_instrument, uninstrument, _active_patches, _proxy_env_overrides


# ---------------------------------------------------------------------------
# Fake Google generativeai module
# ---------------------------------------------------------------------------

class _FakeGoogleUsage:
    prompt_token_count = 20
    candidates_token_count = 15
    total_token_count = 35


class _FakeGooglePart:
    text = "Hello from Gemini."


class _FakeGoogleContent:
    parts = [_FakeGooglePart()]


class _FakeGoogleCandidate:
    content = _FakeGoogleContent()
    finish_reason = "STOP"


class _FakeGoogleResponse:
    usage_metadata = _FakeGoogleUsage()
    candidates = [_FakeGoogleCandidate()]


def _install_fake_google():
    """Register a fake ``google.generativeai`` package in sys.modules."""
    google_mod = types.ModuleType("google")
    genai_mod = types.ModuleType("google.generativeai")

    class GenerativeModel:
        model_name = "gemini-2.0-flash"

        def __init__(self, model_name: str = "gemini-2.0-flash"):
            self.model_name = model_name

        def generate_content(self, contents, **kwargs):
            return _FakeGoogleResponse()

        async def generate_content_async(self, contents, **kwargs):
            return _FakeGoogleResponse()

    genai_mod.GenerativeModel = GenerativeModel
    google_mod.generativeai = genai_mod

    sys.modules["google"] = google_mod
    sys.modules["google.generativeai"] = genai_mod
    return genai_mod


def _uninstall_fake(prefix: str):
    to_remove = [k for k in sys.modules if k == prefix or k.startswith(prefix + ".")]
    for k in to_remove:
        del sys.modules[k]


# ---------------------------------------------------------------------------
# Fixtures
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


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    uninstrument()
    _active_patches.clear()
    _proxy_env_overrides.clear()
    _uninstall_fake("google")
    # Clean up proxy env vars just in case
    for env_key in ("ANTHROPIC_BASE_URL", "OPENAI_BASE_URL", "GOOGLE_GENAI_BASE_URL"):
        os.environ.pop(env_key, None)


# ---------------------------------------------------------------------------
# Google SDK tests
# ---------------------------------------------------------------------------

class TestGoogleInstrumentation:

    def test_google_sync(self, _setup_test_tracer):
        exporter, _ = _setup_test_tracer
        _install_fake_google()
        auto_instrument(providers=["google"])

        import google.generativeai as genai
        model = genai.GenerativeModel("gemini-2.0-flash")
        resp = model.generate_content("Hello!")

        assert resp.usage_metadata.prompt_token_count == 20

        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        attrs = dict(spans[0].attributes)
        assert spans[0].name == "llm.gemini-2.0-flash"
        assert attrs[schema.PROV_ACTIVITY_TYPE] == schema.ACTIVITY_LLM_CALL
        assert attrs[schema.PROV_LLM_PROVIDER] == "google"
        assert attrs[schema.PROV_LLM_MODEL] == "gemini-2.0-flash"
        assert attrs[schema.PROV_LLM_PROMPT_TOKENS] == 20
        assert attrs[schema.PROV_LLM_COMPLETION_TOKENS] == 15
        assert attrs[schema.PROV_LLM_TOTAL_TOKENS] == 35
        assert attrs[schema.PROV_LLM_STOP_REASON] == "STOP"
        assert attrs[schema.AUTO_INSTRUMENTED] is True
        # OTel gen_ai.* dual-emit
        assert attrs[schema.GEN_AI_OPERATION_NAME] == "chat"
        assert attrs[schema.GEN_AI_SYSTEM] == "google"
        assert attrs[schema.GEN_AI_REQUEST_MODEL] == "gemini-2.0-flash"
        assert attrs[schema.GEN_AI_USAGE_INPUT_TOKENS] == 20
        assert attrs[schema.GEN_AI_USAGE_OUTPUT_TOKENS] == 15
        assert list(attrs[schema.GEN_AI_RESPONSE_FINISH_REASONS]) == ["STOP"]

    def test_google_async(self, _setup_test_tracer):
        exporter, _ = _setup_test_tracer
        _install_fake_google()
        auto_instrument(providers=["google"])

        import google.generativeai as genai

        async def _run():
            model = genai.GenerativeModel("gemini-2.0-flash")
            return await model.generate_content_async("Hello!")

        resp = asyncio.run(_run())
        assert resp.usage_metadata.prompt_token_count == 20

        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].name == "llm.gemini-2.0-flash"

    def test_google_captures_output(self, _setup_test_tracer):
        exporter, _ = _setup_test_tracer
        _install_fake_google()
        auto_instrument(providers=["google"], captures_output=True)

        import google.generativeai as genai
        model = genai.GenerativeModel("gemini-2.0-flash")
        model.generate_content("Hello!")

        spans = exporter.get_finished_spans()
        attrs = dict(spans[0].attributes)
        assert "Hello from Gemini" in attrs.get(schema.PROV_LLM_RESPONSE_PREVIEW, "")

    def test_google_uninstrument(self, _setup_test_tracer):
        exporter, _ = _setup_test_tracer
        _install_fake_google()
        auto_instrument(providers=["google"])
        uninstrument(providers=["google"])

        import google.generativeai as genai
        model = genai.GenerativeModel("gemini-2.0-flash")
        model.generate_content("Hello!")

        spans = exporter.get_finished_spans()
        assert len(spans) == 0

    def test_all_providers_includes_google(self, _setup_test_tracer):
        exporter, _ = _setup_test_tracer
        _install_fake_google()
        auto_instrument()  # default: all known providers

        assert "google" in _active_patches

    def test_google_missing_sdk_skipped(self, _setup_test_tracer):
        _uninstall_fake("google")
        # Should not raise — silently skips missing SDK
        auto_instrument(providers=["google"])
        assert "google" not in _active_patches


# ---------------------------------------------------------------------------
# Proxy mode tests
# ---------------------------------------------------------------------------

class TestProxyMode:

    def test_proxy_mode_sets_anthropic_base_url(self, _setup_test_tracer):
        original = os.environ.get("ANTHROPIC_BASE_URL")
        auto_instrument(
            providers=["anthropic"],
            mode="proxy",
            proxy_url="http://192.168.1.70:30400",
        )
        assert os.environ.get("ANTHROPIC_BASE_URL") == "http://192.168.1.70:30400/v1"
        # Cleanup
        if original is None:
            os.environ.pop("ANTHROPIC_BASE_URL", None)
        else:
            os.environ["ANTHROPIC_BASE_URL"] = original

    def test_proxy_mode_sets_openai_base_url(self, _setup_test_tracer):
        auto_instrument(
            providers=["openai"],
            mode="proxy",
            proxy_url="http://192.168.1.70:30400",
        )
        assert os.environ.get("OPENAI_BASE_URL") == "http://192.168.1.70:30400/v1"

    def test_proxy_mode_sets_google_base_url(self, _setup_test_tracer):
        auto_instrument(
            providers=["google"],
            mode="proxy",
            proxy_url="http://proxy.example.com:9000",
        )
        assert os.environ.get("GOOGLE_GENAI_BASE_URL") == "http://proxy.example.com:9000/v1"

    def test_proxy_mode_strips_trailing_slash(self, _setup_test_tracer):
        auto_instrument(
            providers=["anthropic"],
            mode="proxy",
            proxy_url="http://192.168.1.70:30400/",
        )
        assert os.environ.get("ANTHROPIC_BASE_URL") == "http://192.168.1.70:30400/v1"

    def test_proxy_mode_restores_env_vars_on_uninstrument(self, _setup_test_tracer):
        original_value = os.environ.get("ANTHROPIC_BASE_URL")
        try:
            os.environ["ANTHROPIC_BASE_URL"] = "http://original.example.com/v1"
            auto_instrument(
                providers=["anthropic"],
                mode="proxy",
                proxy_url="http://proxy.example.com:30400",
            )
            assert os.environ["ANTHROPIC_BASE_URL"] == "http://proxy.example.com:30400/v1"

            uninstrument()
            assert os.environ.get("ANTHROPIC_BASE_URL") == "http://original.example.com/v1"
        finally:
            if original_value is None:
                os.environ.pop("ANTHROPIC_BASE_URL", None)
            else:
                os.environ["ANTHROPIC_BASE_URL"] = original_value

    def test_proxy_mode_clears_unset_env_vars_on_uninstrument(self, _setup_test_tracer):
        os.environ.pop("OPENAI_BASE_URL", None)
        auto_instrument(
            providers=["openai"],
            mode="proxy",
            proxy_url="http://proxy.example.com:30400",
        )
        assert "OPENAI_BASE_URL" in os.environ

        uninstrument()
        assert "OPENAI_BASE_URL" not in os.environ

    def test_proxy_mode_requires_proxy_url(self, _setup_test_tracer):
        with pytest.raises(ValueError, match="proxy_url"):
            auto_instrument(mode="proxy")

    def test_invalid_mode_raises(self, _setup_test_tracer):
        with pytest.raises(ValueError, match="mode must be"):
            auto_instrument(mode="invalid")

    def test_proxy_mode_does_not_emit_spans(self, _setup_test_tracer):
        """In proxy mode, SDK calls are routed to the proxy — no local OTel spans."""
        exporter, _ = _setup_test_tracer
        _install_fake_google()
        auto_instrument(
            providers=["google"],
            mode="proxy",
            proxy_url="http://proxy.example.com:30400",
        )
        # In proxy mode we do NOT patch SDK methods — just set env vars
        import google.generativeai as genai
        model = genai.GenerativeModel("gemini-2.0-flash")
        model.generate_content("Hello!")

        spans = exporter.get_finished_spans()
        assert len(spans) == 0

    def test_direct_mode_emits_spans(self, _setup_test_tracer):
        """In direct mode (default), SDK calls DO emit local OTel spans."""
        exporter, _ = _setup_test_tracer
        _install_fake_google()
        auto_instrument(providers=["google"], mode="direct")

        import google.generativeai as genai
        model = genai.GenerativeModel("gemini-2.0-flash")
        model.generate_content("Hello!")

        spans = exporter.get_finished_spans()
        assert len(spans) == 1
