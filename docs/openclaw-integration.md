# OpenClaw Integration: Distributed Tracing

How to wire OpenClaw (or any agent host) into AgentWeave's distributed
tracing so every user message produces a connected trace tree in Tempo.

## Overview

```
User message
  └─ Host creates root span (session.id, prov.session.id, prov.agent.id)
       ├─ LLM call 1  ← traceparent injected
       ├─ Tool call    ← traceparent injected
       │    └─ Sub-agent LLM call  ← child span, linked via traceparent
       └─ LLM call 2  ← traceparent injected
```

The host is responsible for:

1. Starting a **root span** for each user message.
2. Setting the required **span attributes**.
3. **Injecting `traceparent`** into every downstream HTTP call (LLM
   requests via the proxy, tool calls, sub-agent invocations).

## Required Span Attributes

Set these on the root span so the dashboard and Tempo queries work:

| Attribute | Type | Description |
|-----------|------|-------------|
| `session.id` | string | Unique ID for the conversation/session. Shared across all spans in the same session. |
| `prov.session.id` | string | Same value as `session.id` (dual-emitted for backward compatibility). |
| `prov.agent.id` | string | Identifier for the agent instance, e.g. `"openclaw-v1"`. |

Optional but recommended:

| Attribute | Type | Description |
|-----------|------|-------------|
| `prov.agent.type` | string | `"main"`, `"subagent"`, or `"delegated"`. |
| `prov.parent.session.id` | string | Parent session ID when this is a sub-agent. |
| `prov.task.label` | string | Human-readable label for the task. |
| `prov.project` | string | Project grouping, e.g. `"launchpad"`. |

## Step 1: Create a Root Span per User Message

Use OpenTelemetry to start a span when the host receives a user message:

```python
from opentelemetry import trace

tracer = trace.get_tracer("openclaw")

def handle_user_message(session_id: str, agent_id: str, message: str):
    with tracer.start_as_current_span("agent.turn") as span:
        span.set_attribute("session.id", session_id)
        span.set_attribute("prov.session.id", session_id)
        span.set_attribute("prov.agent.id", agent_id)

        # ... process the message, make LLM calls, run tools ...
```

## Step 2: Inject traceparent into Downstream Calls

Use the helpers in `agentweave.propagation` to inject the W3C
`traceparent` header into outgoing requests. This links child spans
(LLM calls via the proxy, sub-agent calls) to the root span.

```python
from agentweave.propagation import inject_trace_context

def call_llm_via_proxy(prompt: str, session_id: str):
    headers = {
        "X-AgentWeave-Session-Id": session_id,
        "X-AgentWeave-Agent-Id": "openclaw-v1",
    }
    # Inject traceparent into headers for distributed trace linking
    inject_trace_context(headers)
    # headers now contains {"traceparent": "00-<trace_id>-<span_id>-01", ...}

    response = httpx.post(
        "http://localhost:4000/v1/messages",
        headers=headers,
        json={"model": "claude-sonnet-4-6", "messages": [{"role": "user", "content": prompt}]},
    )
    return response.json()
```

The proxy reads the `traceparent` header, records it as `prov.trace.parent`
on the span, and forwards it to the upstream provider.

## Step 3: Propagate Context to Sub-Agents

When the host spawns a sub-agent, extract the current context and pass
it along so sub-agent spans appear as children in the trace:

```python
from agentweave.propagation import inject_trace_context, extract_trace_context

def spawn_sub_agent(task: str, parent_session_id: str):
    sub_session_id = f"sub-{uuid4().hex[:12]}"

    headers = {
        "X-AgentWeave-Session-Id": sub_session_id,
        "X-AgentWeave-Parent-Session-Id": parent_session_id,
        "X-AgentWeave-Agent-Type": "subagent",
    }
    inject_trace_context(headers)

    # Send to sub-agent endpoint or invoke directly
    run_sub_agent(task, headers=headers)
```

On the sub-agent side, extract the context to link spans:

```python
from opentelemetry import trace, context
from agentweave.propagation import extract_trace_context

def run_sub_agent(task: str, headers: dict):
    ctx = extract_trace_context(headers)
    tracer = trace.get_tracer("openclaw-sub")

    with tracer.start_as_current_span("agent.subtask", context=ctx) as span:
        span.set_attribute("session.id", headers["X-AgentWeave-Session-Id"])
        span.set_attribute("prov.session.id", headers["X-AgentWeave-Session-Id"])
        span.set_attribute("prov.parent.session.id", headers["X-AgentWeave-Parent-Session-Id"])
        span.set_attribute("prov.agent.type", "subagent")
        # ... do work ...
```

## Verifying the Integration

1. Start the proxy: `agentweave proxy start --port 4000`
2. Send a traced request from the host (as shown above).
3. Open the AgentWeave dashboard → Session Explorer.
4. Paste the `session.id` into the replay input and click **Open in Grafana**.
5. In Grafana Explore, you should see the full trace tree: root span →
   LLM calls → sub-agent spans, all linked by `traceparent`.

## Proxy Headers Reference

| Header | Purpose |
|--------|---------|
| `traceparent` | W3C trace context — links spans across services |
| `X-AgentWeave-Session-Id` | Session ID stamped on every span |
| `X-AgentWeave-Agent-Id` | Agent identifier |
| `X-AgentWeave-Parent-Session-Id` | Parent session (sub-agent attribution) |
| `X-AgentWeave-Agent-Type` | `main` / `subagent` / `delegated` |
| `X-AgentWeave-Project` | Project grouping |
| `X-AgentWeave-Turn` | Turn number within session |
