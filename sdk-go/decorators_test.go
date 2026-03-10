package agentweave_test

import (
	"context"
	"errors"
	"testing"

	agentweave "github.com/arniesaha/agentweave-go"
	"go.opentelemetry.io/otel"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	"go.opentelemetry.io/otel/sdk/trace/tracetest"
)

// setupTestTracer installs an in-memory exporter and returns the exporter + cleanup fn.
func setupTestTracer(t *testing.T) *tracetest.SpanRecorder {
	t.Helper()
	recorder := tracetest.NewSpanRecorder()
	tp := sdktrace.NewTracerProvider(sdktrace.WithSpanProcessor(recorder))
	otel.SetTracerProvider(tp)
	t.Cleanup(func() { _ = tp.Shutdown(context.Background()) })
	return recorder
}

func TestTraceTool(t *testing.T) {
	recorder := setupTestTracer(t)
	ctx := context.Background()

	result, err := agentweave.TraceTool(ctx, "web_search", func() (any, error) {
		return "results", nil
	})

	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if result != "results" {
		t.Errorf("expected 'results', got %v", result)
	}

	spans := recorder.Ended()
	if len(spans) != 1 {
		t.Fatalf("expected 1 span, got %d", len(spans))
	}

	span := spans[0]
	if span.Name() != "tool.web_search" {
		t.Errorf("expected span name 'tool.web_search', got %q", span.Name())
	}

	attrs := attrsMap(span)
	if attrs[agentweave.ProvActivityType] != agentweave.ActivityToolCall {
		t.Errorf("expected prov.activity.type=%q, got %q", agentweave.ActivityToolCall, attrs[agentweave.ProvActivityType])
	}
}

func TestTraceToolError(t *testing.T) {
	recorder := setupTestTracer(t)
	ctx := context.Background()

	_, err := agentweave.TraceTool(ctx, "failing_tool", func() (any, error) {
		return nil, errors.New("tool failed")
	})

	if err == nil {
		t.Fatal("expected error, got nil")
	}

	spans := recorder.Ended()
	if len(spans) != 1 {
		t.Fatalf("expected 1 span, got %d", len(spans))
	}
}

func TestTraceAgent(t *testing.T) {
	recorder := setupTestTracer(t)
	ctx := context.Background()

	cfg := agentweave.AgentConfig{
		AgentID: "nix-v1",
		Model:   "claude-sonnet-4-6",
	}

	_, err := agentweave.TraceAgent(ctx, "handle", cfg, func() (any, error) {
		return "done", nil
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	spans := recorder.Ended()
	if len(spans) != 1 {
		t.Fatalf("expected 1 span, got %d", len(spans))
	}

	span := spans[0]
	if span.Name() != "agent.handle" {
		t.Errorf("expected 'agent.handle', got %q", span.Name())
	}

	attrs := attrsMap(span)
	if attrs[agentweave.ProvAgentID] != "nix-v1" {
		t.Errorf("expected prov.agent.id='nix-v1', got %q", attrs[agentweave.ProvAgentID])
	}
	if attrs[agentweave.ProvActivityType] != agentweave.ActivityAgentTurn {
		t.Errorf("expected prov.activity.type=%q, got %q", agentweave.ActivityAgentTurn, attrs[agentweave.ProvActivityType])
	}
}

func TestTraceLlm(t *testing.T) {
	recorder := setupTestTracer(t)
	ctx := context.Background()

	cfg := agentweave.LLMConfig{
		Provider: "anthropic",
		Model:    "claude-sonnet-4-6",
	}

	_, err := agentweave.TraceLlm(ctx, cfg, func() (any, error) {
		return "response text", nil
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	spans := recorder.Ended()
	if len(spans) != 1 {
		t.Fatalf("expected 1 span, got %d", len(spans))
	}

	span := spans[0]
	if span.Name() != "llm.claude-sonnet-4-6" {
		t.Errorf("expected 'llm.claude-sonnet-4-6', got %q", span.Name())
	}

	attrs := attrsMap(span)
	if attrs[agentweave.ProvLLMProvider] != "anthropic" {
		t.Errorf("expected prov.llm.provider='anthropic', got %q", attrs[agentweave.ProvLLMProvider])
	}
	if attrs[agentweave.ProvLLMModel] != "claude-sonnet-4-6" {
		t.Errorf("expected prov.llm.model='claude-sonnet-4-6', got %q", attrs[agentweave.ProvLLMModel])
	}
}

// attrsMap converts span attributes to a string map for easy assertion.
func attrsMap(span sdktrace.ReadOnlySpan) map[string]string {
	m := make(map[string]string)
	for _, a := range span.Attributes() {
		m[string(a.Key)] = a.Value.AsString()
	}
	return m
}
