"""Tests for W3C trace context propagation."""

import unittest

import agentweave.exporter as _exporter_mod
from agentweave.propagation import extract_trace_context, get_traceparent, inject_trace_context
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


class TestTracePropagation(unittest.TestCase):

    def setUp(self):
        self.exporter = InMemorySpanExporter()
        self.provider = TracerProvider()
        self.provider.add_span_processor(SimpleSpanProcessor(self.exporter))
        self._old_provider = _exporter_mod._provider
        _exporter_mod._provider = self.provider
        self.headers: dict = {}

    def tearDown(self):
        self.provider.shutdown()
        _exporter_mod._provider = self._old_provider

    def test_inject_trace_context(self):
        """inject_trace_context returns headers with traceparent when a span is active."""
        tracer = _exporter_mod.get_tracer()
        with tracer.start_as_current_span("test-span"):
            headers = inject_trace_context(self.headers)
        self.assertIn("traceparent", headers)
        self.assertTrue(headers["traceparent"].startswith("00-"))

    def test_extract_trace_context(self):
        """extract_trace_context returns a valid context from injected headers."""
        tracer = _exporter_mod.get_tracer()
        with tracer.start_as_current_span("parent-span"):
            inject_trace_context(self.headers)

        # Headers should have a traceparent — extract should parse it back
        self.assertIn("traceparent", self.headers)
        context = extract_trace_context(self.headers)
        self.assertIsNotNone(context)

        # The extracted context should carry the original trace_id (parsed from traceparent)
        from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
        extracted_span_ctx = TraceContextTextMapPropagator().extract(self.headers)
        from opentelemetry.trace import get_current_span
        span = get_current_span(extracted_span_ctx)
        self.assertTrue(span.get_span_context().is_valid)

    def test_get_traceparent(self):
        """get_traceparent returns a valid W3C traceparent string."""
        tracer = _exporter_mod.get_tracer()
        with tracer.start_as_current_span("test-span"):
            inject_trace_context(self.headers)
            tp = get_traceparent()
        self.assertIsNotNone(tp)
        self.assertTrue(tp.startswith("00-"))
        # Format: 00-<trace_id>-<span_id>-<flags>
        parts = tp.split("-")
        self.assertEqual(len(parts), 4)


if __name__ == "__main__":
    unittest.main()
