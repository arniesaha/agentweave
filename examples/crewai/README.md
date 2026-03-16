# CrewAI + AgentWeave

A minimal CrewAI crew with AgentWeave tracing via **proxy mode** — no code
changes to the crew itself.

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
python crew_example.py
```

## What you see after running

- A two-agent crew (researcher + writer) collaborates to produce a summary.
- Traces appear in your OTLP backend (Tempo, Jaeger, Langfuse, etc.) with:
  - LLM call spans for each agent's inference, including token counts and cost
  - Automatic provider detection (OpenAI)

Replace `AGENTWEAVE_PROXY_URL` with your proxy endpoint if it is not
`http://localhost:4000/v1`.
