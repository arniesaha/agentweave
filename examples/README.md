# AgentWeave Framework Integration Examples

These examples show how to add **AgentWeave observability** to popular agent
frameworks with **zero code changes** to the agent itself.

Every example uses **proxy mode**: point your LLM client's `base_url` at the
AgentWeave proxy and every request is automatically traced, token-counted and
costed.

## Quick start

```bash
# 1. Start the AgentWeave proxy (runs on port 4000)
agentweave proxy start --port 4000 --endpoint http://localhost:4318

# 2. Export your LLM API key
export OPENAI_API_KEY=sk-...

# 3. Run any example
cd examples/langgraph
pip install -r requirements.txt
python langgraph_example.py
```

Traces are emitted via OTLP and visible in Grafana Tempo, Jaeger, Langfuse, or
any OpenTelemetry-compatible backend.

## Examples

| Framework | Directory | Description |
|-----------|-----------|-------------|
| [LangGraph](langgraph/) | `examples/langgraph/` | Stateful graph agent with proxy and optional decorator mode |
| [CrewAI](crewai/) | `examples/crewai/` | Multi-agent crew with proxy-based tracing |
| [AutoGen](autogen/) | `examples/autogen/` | Conversable agents with proxy-based tracing |
| [OpenAI Agents SDK](openai-agents-sdk/) | `examples/openai-agents-sdk/` | OpenAI Agents SDK with proxy-based tracing |

## How proxy mode works

```
Your Agent ──base_url=http://localhost:4000/v1──> AgentWeave Proxy :4000
                                                        │
                                                  auto-detects provider
                                                  extracts token counts
                                                  computes cost (USD)
                                                  emits OTel spans
                                                        │
                                                        ▼
                                                  upstream LLM API
                                                  (OpenAI / Anthropic / Google)
```

Replace `http://localhost:4000/v1` with your proxy endpoint if deployed
elsewhere (e.g. `http://192.168.1.70:30400/v1`).
