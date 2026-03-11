package agentweave

import (
	"context"
	"fmt"

	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/codes"
	"go.opentelemetry.io/otel/trace"
)

// AgentConfig holds identity attributes for an agent turn span.
type AgentConfig struct {
	AgentID string
	Model   string
	Version string
}

// LLMConfig holds attributes for an LLM call span.
type LLMConfig struct {
	Provider       string
	Model          string
	CapturesInput  bool
	CapturesOutput bool
}

// TraceTool wraps fn as a tool_call span. name is the tool name.
func TraceTool(ctx context.Context, name string, fn func(context.Context) (any, error)) (any, error) {
	tracer := getTracer()
	spanName := fmt.Sprintf("%s.%s", SpanPrefixTool, name)

	ctx, span := tracer.Start(ctx, spanName)
	defer span.End()

	span.SetAttributes(
		attribute.String(ProvActivityType, ActivityToolCall),
	)
	applyGlobalConfig(span)

	result, err := fn(ctx)
	if err != nil {
		span.RecordError(err)
		span.SetStatus(codes.Error, err.Error())
		return nil, err
	}
	return result, nil
}

// TraceAgent wraps fn as an agent_turn span.
func TraceAgent(ctx context.Context, name string, cfg AgentConfig, fn func(context.Context) (any, error)) (any, error) {
	tracer := getTracer()
	spanName := fmt.Sprintf("%s.%s", SpanPrefixAgent, name)

	ctx, span := tracer.Start(ctx, spanName)
	defer span.End()

	span.SetAttributes(
		attribute.String(ProvActivityType, ActivityAgentTurn),
	)
	if cfg.AgentID != "" {
		span.SetAttributes(attribute.String(ProvAgentID, cfg.AgentID))
		span.SetAttributes(attribute.String(ProvWasAssociatedWith, cfg.AgentID))
	}
	if cfg.Model != "" {
		span.SetAttributes(attribute.String(ProvAgentModel, cfg.Model))
	}
	if cfg.Version != "" {
		span.SetAttributes(attribute.String(ProvAgentVersion, cfg.Version))
	}

	result, err := fn(ctx)
	if err != nil {
		span.RecordError(err)
		span.SetStatus(codes.Error, err.Error())
		return nil, err
	}
	return result, nil
}

// TraceLlm wraps fn as an llm_call span. Extracts token counts from the response
// if it implements LLMResponse.
func TraceLlm(ctx context.Context, cfg LLMConfig, fn func(context.Context) (any, error)) (any, error) {
	tracer := getTracer()
	spanName := fmt.Sprintf("%s.%s", SpanPrefixLLM, cfg.Model)

	ctx, span := tracer.Start(ctx, spanName, trace.WithSpanKind(trace.SpanKindClient))
	defer span.End()

	span.SetAttributes(
		attribute.String(ProvActivityType, ActivityLLMCall),
		attribute.String(ProvLLMProvider, cfg.Provider),
		attribute.String(ProvLLMModel, cfg.Model),
	)
	applyGlobalConfig(span)

	result, err := fn(ctx)
	if err != nil {
		span.RecordError(err)
		span.SetStatus(codes.Error, err.Error())
		return nil, err
	}

	// Extract token counts if response implements LLMUsage
	if r, ok := result.(LLMResponse); ok {
		usage := r.GetUsage()
		span.SetAttributes(
			attribute.Int64(ProvLLMPromptTokens, int64(usage.PromptTokens)),
			attribute.Int64(ProvLLMCompletionTokens, int64(usage.CompletionTokens)),
			attribute.Int64(ProvLLMTotalTokens, int64(usage.PromptTokens+usage.CompletionTokens)),
		)
		if usage.StopReason != "" {
			span.SetAttributes(attribute.String(ProvLLMStopReason, usage.StopReason))
		}
	}

	return result, nil
}

// LLMUsage holds normalized token usage from any LLM provider.
type LLMUsage struct {
	PromptTokens     int
	CompletionTokens int
	StopReason       string
}

// LLMResponse is implemented by response types that expose usage metadata.
type LLMResponse interface {
	GetUsage() LLMUsage
}

// applyGlobalConfig sets agent identity attributes from the global config.
func applyGlobalConfig(span trace.Span) {
	mu.RLock()
	cfg := globalConfig
	mu.RUnlock()

	if cfg == nil {
		return
	}
	if cfg.AgentID != "" {
		span.SetAttributes(
			attribute.String(ProvAgentID, cfg.AgentID),
			attribute.String(ProvWasAssociatedWith, cfg.AgentID),
		)
	}
	if cfg.AgentModel != "" {
		span.SetAttributes(attribute.String(ProvAgentModel, cfg.AgentModel))
	}
}
