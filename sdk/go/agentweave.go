// Package agentweave provides observability tracing for multi-agent AI systems.
// It emits W3C PROV-O compatible OpenTelemetry spans for tool calls, agent turns,
// and LLM invocations.
package agentweave

import "sync"

var (
	globalConfig *Config
	mu           sync.RWMutex
)

// Config holds global identity and export settings for AgentWeave.
type Config struct {
	AgentID        string // Unique identifier for this agent (e.g. "nix-v1")
	AgentModel     string // Model name (e.g. "claude-sonnet-4-6")
	AgentVersion   string // Agent version string (e.g. "0.1.0")
	OTLPEndpoint   string // OTLP HTTP endpoint (e.g. "http://localhost:4318")
	CapturesInput  bool   // Default: capture tool/agent inputs in spans
	CapturesOutput bool   // Default: capture tool/agent outputs in spans
	Enabled        bool   // Enable or disable tracing globally (default true)
}

// Setup initialises AgentWeave with the given config and starts the OTel exporter.
// Call this once at application startup.
func Setup(cfg Config) error {
	mu.Lock()
	globalConfig = &cfg
	mu.Unlock()
	return initTracer(&cfg)
}
