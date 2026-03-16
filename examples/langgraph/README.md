# LangGraph + AgentWeave

A minimal LangGraph agent with AgentWeave tracing via **proxy mode** — no code
changes to the agent itself.

## Prerequisites

```bash
pip install -r requirements.txt
export OPENAI_API_KEY=sk-...
```

## How to run

```bash
# 1. Start the AgentWeave proxy
agentweave proxy start --port 4000 --endpoint http://localhost:4318

# 2. Run the example
python langgraph_example.py
```

## What you see after running

- The agent answers a question using a simple tool-calling graph.
- Traces appear in your OTLP backend (Tempo, Jaeger, Langfuse, etc.) with:
  - LLM call spans including model, token counts, and cost
  - Automatic provider detection (OpenAI)

Replace `AGENTWEAVE_PROXY_URL` with your proxy endpoint if it is not
`http://localhost:4000/v1`.

## Decorator mode (optional)

The example also includes a decorator-mode variant that gives you explicit
control over span names and nesting. See the `run_with_decorators()` function.
