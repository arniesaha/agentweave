# AgentWeave Go SDK

Observability tracing for multi-agent AI systems — Go edition.

## Install

```bash
go get github.com/arniesaha/agentweave-go
```

## Setup

```go
import agentweave "github.com/arniesaha/agentweave-go"

func main() {
    err := agentweave.Setup(agentweave.Config{
        AgentID:      "my-agent-v1",
        AgentModel:   "claude-sonnet-4-6",
        OTLPEndpoint: "http://tempo:4318",
    })
    if err != nil {
        log.Fatal(err)
    }
    defer agentweave.Shutdown(context.Background())
}
```

## Usage

```go
// Trace a tool call
result, err := agentweave.TraceTool(ctx, "web_search", func() (any, error) {
    return search(query), nil
})

// Trace an agent turn
result, err := agentweave.TraceAgent(ctx, "handle", agentweave.AgentConfig{
    AgentID: "nix-v1",
    Model:   "claude-sonnet-4-6",
}, func() (any, error) {
    return processMessage(msg), nil
})

// Trace an LLM call
result, err := agentweave.TraceLlm(ctx, agentweave.LLMConfig{
    Provider: "anthropic",
    Model:    "claude-sonnet-4-6",
}, func() (any, error) {
    return client.Messages.Create(ctx, params)
})
```

## Spans

All spans emit W3C PROV-O compatible attributes:

| Attribute | Description |
|-----------|-------------|
| `prov.activity.type` | `tool_call` \| `agent_turn` \| `llm_call` |
| `prov.agent.id` | Agent identifier |
| `prov.agent.model` | Model name |
| `prov.llm.provider` | `anthropic` \| `openai` \| `google` |
| `prov.llm.prompt_tokens` | Input token count |
| `prov.llm.completion_tokens` | Output token count |
| `prov.llm.total_tokens` | Total tokens |

Traces are exported via OTLP HTTP to Grafana Tempo, Jaeger, or any compatible backend.
