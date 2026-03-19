# Example 01: Runaway Token Triage

## What this shows

A common silent killer in production agents: a tool that **returns way more
text than expected**, silently inflating your context window and blowing up
token costs. This example makes it visible.

### The pipeline

```
search_tool → summarize_tool → LLM (Claude Haiku) → report_tool
                   ↑
          the offending step
```

The `summarize_tool_broken` version returns a ~5000-word fake document as raw
text. The LLM gets a huge prompt, token counts spike, and costs go up. The
`summarize_tool_fixed` version truncates the output to 800 characters before
returning.

Both runs produce full AgentWeave traces so you can compare them side-by-side
in Grafana Tempo.

## How to run

```bash
# Point at the AgentWeave proxy (handles Anthropic auth internally)
export ANTHROPIC_BASE_URL=http://192.168.1.70:30400/v1
export ANTHROPIC_API_KEY=dummy

cd /path/to/agentweave
python examples/01-token-spike/main.py
```

The script runs **two pipelines back-to-back** — broken first, then fixed —
and prints the session IDs for each.

## What to look for in the dashboard

1. **Token counts** — `prov.llm.prompt_tokens` should be ~3×–10× higher in
   the broken run vs. the fixed run. Look for the `llm.claude-3-haiku-20240307`
   span attributes.

2. **Span attributes on `tool.summarize_broken`** — `prov.entity.output.value`
   (captured because `captures_output=True`) shows the giant blob.

3. **Side-by-side cost** — `cost.usd` on the LLM span will be visibly higher
   in the broken run.

4. **Session filter** — filter by `session.id` in Tempo to isolate each run.
   The session IDs are printed at the end of each run.

## Prerequisites

- AgentWeave proxy running at `http://192.168.1.70:30400`
- Grafana/Tempo at `http://192.168.1.70:30418` (OTLP) + `https://o11y.arnabsaha.com`
- Python packages: `anthropic`, `agentweave` (from `sdk/python/`)
