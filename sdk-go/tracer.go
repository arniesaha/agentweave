package agentweave

import (
	"context"
	"fmt"
	"strings"

	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracehttp"
	sdkresource "go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	semconv "go.opentelemetry.io/otel/semconv/v1.21.0"
	"go.opentelemetry.io/otel/trace"
)

var _provider *sdktrace.TracerProvider

func initTracer(cfg *Config) error {
	endpoint := cfg.OTLPEndpoint
	if endpoint == "" {
		endpoint = "http://localhost:4318"
	}

	// Strip trailing slash and /v1/traces if already included
	endpoint = strings.TrimSuffix(endpoint, "/v1/traces")
	endpoint = strings.TrimSuffix(endpoint, "/")

	exporter, err := otlptracehttp.New(
		context.Background(),
		otlptracehttp.WithEndpoint(strings.TrimPrefix(strings.TrimPrefix(endpoint, "https://"), "http://")),
		otlptracehttp.WithURLPath("/v1/traces"),
	)
	if err != nil {
		return fmt.Errorf("agentweave: failed to create OTLP exporter: %w", err)
	}

	res, err := sdkresource.New(
		context.Background(),
		sdkresource.WithAttributes(
			semconv.ServiceName("agentweave"),
		),
	)
	if err != nil {
		res = sdkresource.Default()
	}

	_provider = sdktrace.NewTracerProvider(
		sdktrace.WithBatcher(exporter),
		sdktrace.WithResource(res),
	)
	otel.SetTracerProvider(_provider)
	return nil
}

// getTracer returns the AgentWeave tracer. Uses the module-level provider if set,
// otherwise falls back to the global OTel provider.
func getTracer() trace.Tracer {
	if _provider != nil {
		return _provider.Tracer("agentweave")
	}
	return otel.GetTracerProvider().Tracer("agentweave")
}

// Shutdown flushes and shuts down the tracer provider.
func Shutdown(ctx context.Context) error {
	if _provider != nil {
		return _provider.Shutdown(ctx)
	}
	return nil
}
