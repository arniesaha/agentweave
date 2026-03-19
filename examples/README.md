# AgentWeave Examples

This directory contains two sets of examples:

1. **Golden path scenarios** (`01-*`, `02-*`, `03-*`) — self-contained demos
   that showcase specific observability problems AgentWeave solves. Start here
   if you're evaluating AgentWeave.

2. **Framework integrations** (`langgraph/`, `crewai/`, `autogen/`,
   `openai-agents-sdk/`) — zero-code-change proxy examples for popular agent
   frameworks.

---

## 🟡 Golden Path Scenarios

These three demos each isolate a real production problem and show how
AgentWeave makes it visible. Each runs against the live proxy at
`http://192.168.1.70:30400/v1` and produces real traces in Grafana Tempo.

### Prerequisites

```bash
# Point at the AgentWeave proxy (handles Anthropic auth internally)
export ANTHROPIC_BASE_URL=http://192.168.1.70:30400/v1
export ANTHROPIC_API_KEY=dummy

# Python deps (from repo root)
pip install anthropic
pip install -e sdk/python/
```

### Scenario overview

| # | Directory | Problem | Key Technique |
|---|-----------|---------|---------------|
| 1 | [01-token-spike/](01-token-spike/) | Runaway token costs from an over-verbose tool | `auto_instrument()` + input/output capture |
| 2 | [02-agent-delegation/](02-agent-delegation/) | Multi-agent hierarchy invisible in traces | `@trace_agent` + `parent_session_id` linking |
| 3 | [03-tool-failure/](03-tool-failure/) | Silent wrong-data bug causes LLM hallucination | `@trace_tool(captures_output=True)` |

### Run all three

```bash
python examples/01-token-spike/main.py
python examples/02-agent-delegation/main.py
python examples/03-tool-failure/main.py
```

After each run, traces appear in Grafana Tempo under
`https://o11y.arnabsaha.com/explore`. Filter by `session.id` using the
session ID printed at the end of each script.

### What to look for in the dashboard

**Token spike (01):**
- Compare `prov.llm.prompt_tokens` between the broken and fixed runs
- Inspect `prov.entity.output.value` on the `tool.summarize_broken` span
- The `cost.usd` attribute shows the dollar impact

**Agent delegation (02):**
- In the session graph, you should see the orchestrator with two child edges
- Filter spans by `prov.parent_session_id` to find all sub-agents of a run
- `prov.session.turn` = 1 (orchestrator) vs 2 (sub-agents)

**Tool failure (03):**
- Inspect `prov.entity.output.value` on the buggy vs fixed tool spans
- The raw JSON output reveals the wrong field name immediately
- No extra logging needed — `captures_output=True` does it automatically

---

## 🔧 Framework Integration Examples

These examples show how to add **AgentWeave observability** to popular agent
frameworks with **zero code changes** to the agent itself.

Every example uses **proxy mode**: point your LLM client's `base_url` at the
AgentWeave proxy and every request is automatically traced, token-counted and
costed.

## Quick start (framework examples)

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

## Framework examples

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
