# Session-Level Trace Grouping

AgentWeave supports grouping all spans from a single user interaction or
agent conversation under a shared **session ID**.  This makes it easy to
filter, replay, or debug an entire multi-turn conversation in Grafana / Tempo.

## How It Works

A session ID is a free-form string (e.g. a UUID, a conversation ID, a ticket
number).  When provided it is attached to every span as the `session.id`
attribute (and `prov.session.id` for backward compatibility).

---

## SDK — `@trace_agent(session_id=...)`

Pass `session_id` to the `@trace_agent` decorator.  All child spans (LLM
calls, tool calls) created inside the decorated function automatically inherit
the same OTel trace, so they are already grouped by `trace_id`.  The
`session.id` attribute lets you **filter across multiple traces** that belong
to the same long-running conversation.

```python
import agentweave
from agentweave import trace_agent, trace_llm

agentweave.AgentWeaveConfig.setup(
    agent_id="my-agent",
    otel_endpoint="http://localhost:4318",
)

CONVERSATION_ID = "conv-abc123"

@trace_agent(name="my_agent", session_id=CONVERSATION_ID)
def handle_turn(message: str) -> str:
    # ... call LLMs, tools, etc.
    return "response"

# Every span produced by handle_turn() will carry:
#   session.id = "conv-abc123"
handle_turn("Hello!")
handle_turn("Follow-up question")
```

The `session_id` value is fixed at decoration time.  If you need a
**dynamic** session ID per call, set it manually on the current span:

```python
from opentelemetry import trace
from agentweave import schema

@trace_agent(name="my_agent")
def handle_turn(message: str, session_id: str) -> str:
    span = trace.get_current_span()
    span.set_attribute(schema.SESSION_ID, session_id)
    span.set_attribute(schema.PROV_SESSION_ID, session_id)
    # ...
    return "response"
```

---

## Proxy — `X-AgentWeave-Session-Id` Header

When routing LLM calls through the AgentWeave proxy, pass the session ID as
an HTTP header.  The proxy strips the header before forwarding to the upstream
provider and attaches it as `session.id` on the emitted OTel span.

```bash
curl http://localhost:4000/v1/messages \
  -H "X-AgentWeave-Session-Id: conv-abc123" \
  -H "X-Api-Key: $ANTHROPIC_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-sonnet-4-6","max_tokens":256,"messages":[{"role":"user","content":"Hello"}]}'
```

With the Anthropic SDK:

```python
import anthropic

client = anthropic.Anthropic(
    base_url="http://localhost:4000",
    default_headers={"X-AgentWeave-Session-Id": "conv-abc123"},
)
```

---

## Grafana Dashboard

The **AgentWeave — Agent Activity** dashboard includes a **Session ID**
text-box variable.  Enter a session ID to filter the TraceQL queries:

```
{ span.session.id = "$session_id" }
```

You can combine it with the existing **Model** and **Project** filters to
narrow down exactly which spans you care about.

---

## Span Attributes

| Attribute | Description |
|---|---|
| `session.id` | Primary session identifier (canonical key) |
| `prov.session.id` | Backward-compatible alias (same value) |

Both attributes are always written together so existing dashboards and alerts
that target `prov.session.id` continue to work.
