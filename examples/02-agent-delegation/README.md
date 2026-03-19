# Example 02: Multi-Agent Delegation Loop

## What this shows

Real multi-agent systems have a hierarchy: a parent orchestrator delegates
subtasks to specialized sub-agents. Without observability, all you see is one
giant trace — you can't tell which agent did what.

This example shows how AgentWeave's `@trace_agent` decorator + session
linking makes the hierarchy **visible in the session graph**.

### The architecture

```
orchestrator_agent   (main, turn_depth=1)
        │
        ├── analyst_agent    (subagent, turn_depth=2)
        │         └── LLM call: research bullets
        │
        └── writer_agent     (subagent, turn_depth=2)
                  └── LLM call: executive summary
```

Each agent is tagged with:
- `prov.agent.type`: `main` or `subagent`
- `prov.session.turn`: depth in the hierarchy
- `prov.parent_session_id`: links sub-agents back to the orchestrator

## How to run

```bash
export ANTHROPIC_BASE_URL=http://192.168.1.70:30400/v1
export ANTHROPIC_API_KEY=dummy   # proxy injects the real key (AGENTWEAVE_ANTHROPIC_API_KEY)

cd /path/to/agentweave
python examples/02-agent-delegation/main.py
```

The script prints the session ID at the end — use it to filter in Grafana.

## What to look for in the dashboard

1. **Session graph** — in the AgentWeave sessions view, you should see the
   orchestrator session with two child edges pointing to analyst and writer.

2. **`prov.parent_session_id`** on the sub-agent spans — this is the key
   attribute that wires the hierarchy. Filter by it to find all sub-agents
   of a given orchestrator run.

3. **`prov.session.turn`** — `1` for the orchestrator, `2` for sub-agents.
   In deeper hierarchies (3+ levels), this shows nesting depth.

4. **Token breakdown per agent** — each sub-agent's LLM call has its own
   token counts (`prov.llm.prompt_tokens`, `prov.llm.completion_tokens`),
   so you can see which agent consumed the most tokens.

5. **Span name prefix** — agent spans use `agent.<name>`, LLM spans use
   `llm.<model>`. The nesting shows the call flow.

## Prerequisites

- AgentWeave proxy running at `http://192.168.1.70:30400`
- Grafana/Tempo at `https://o11y.arnabsaha.com`
- Python packages: `anthropic`, `agentweave` (from `sdk/python/`)
