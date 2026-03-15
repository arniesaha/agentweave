# Deterministic Trace IDs

By default, AgentWeave generates a random trace ID for every agent invocation.
This works well for one-shot requests, but causes a problem on **retries**: each
attempt creates a new, unrelated trace, making it impossible to tell that three
traces are all attempts of the same logical request.

Deterministic trace IDs solve this. Supply the same ID on every attempt and your
backend (Grafana, Datadog, Arize, …) can deduplicate — only the first successful
trace is unique; retries land on top of it.

---

## SDK (Python) — `@trace_agent(traceId=...)`

Pass `traceId` to `@trace_agent` to pin the root span to a known trace ID.
All child spans (tool calls, LLM calls) automatically inherit it.

### Valid 32-char hex string

If you already have a UUID or hex token, strip the hyphens and pass it directly:

```python
from agentweave.decorators import trace_agent, trace_tool

REQUEST_ID = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"  # 32-char hex

@trace_agent(traceId=REQUEST_ID)
def handle(message: str) -> str:
    return search(message)

@trace_tool
def search(query: str) -> str:
    return "results"
```

Both the `agent.handle` span **and** the nested `tool.search` span will carry
`trace_id = a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4`.

### Arbitrary string (auto-hashed)

If your ID is a human-readable string (e.g. `"order-abc123"`),
AgentWeave SHA-256 hashes it to produce a stable 32-char hex trace ID.
The original string is preserved in the `agentweave.trace_id` span attribute.

```python
from agentweave.decorators import trace_agent

def process_order(order_id: str, attempt: int) -> str:
    # Same order_id → same trace ID across all retries
    @trace_agent(name="order_processor", traceId=f"order-{order_id}")
    def _handle(msg: str) -> str:
        return f"processed {msg}"

    return _handle(f"attempt {attempt}")

process_order("abc123", attempt=1)  # trace_id = sha256("order-abc123")[:32]
process_order("abc123", attempt=2)  # same trace_id → deduplicable
```

### Dynamic `traceId` at call time

`@trace_agent` sets `traceId` at decoration time. If you need a **per-call**
deterministic ID, create the wrapper inside the request handler or pass the ID
in via a closure:

```python
from agentweave.decorators import trace_agent

def handle_request(request_id: str, message: str) -> str:
    @trace_agent(name="my_agent", traceId=request_id)
    def _handle(msg: str) -> str:
        # your agent logic
        return f"handled: {msg}"

    return _handle(message)
```

---

## Proxy — `X-AgentWeave-Trace-Id` header

When routing LLM calls through the AgentWeave proxy, send the
`X-AgentWeave-Trace-Id` header. The proxy sets the OTel trace ID on the span
and stores the raw value in the `agentweave.trace_id` attribute.

```bash
curl http://localhost:4000/v1/messages \
  -H "Content-Type: application/json" \
  -H "X-AgentWeave-Agent-Id: my-agent" \
  -H "X-AgentWeave-Trace-Id: order-abc123-attempt-1" \
  -d '{"model":"claude-sonnet-4-6","max_tokens":1024,"messages":[{"role":"user","content":"Hello"}]}'
```

Retrying the same request with the same `X-AgentWeave-Trace-Id` value produces
a span with the **same** OTel trace ID, so duplicate traces are easy to filter.

### ID format rules (same as SDK)

| Input | Behaviour |
|-------|-----------|
| 32-char hex string | Used directly as the OTel trace ID |
| Any other string | SHA-256 hashed; first 32 hex chars used |
| Header absent | Random trace ID (default behaviour) |

The original header value is always stored as `agentweave.trace_id` on the span
regardless of whether it was hashed.

---

## Deduplication strategies

### Grafana / Tempo

Filter on `agentweave.trace_id` to find all attempts:

```logql
{resource.service.name="my-agent"} | json | agentweave_trace_id = "order-abc123"
```

Or use the OTel trace ID directly — if you supplied a deterministic ID all
attempts share the same trace ID and appear as a single trace in Tempo.

### Arize / Phoenix

Tag your rows with the deterministic trace ID and filter duplicates in the
dataset view using `trace_id`.

### Custom deduplication

Every span carries:

- `agentweave.trace_id` — the raw caller-supplied string (for human-readable lookup)
- The standard OTel `trace_id` — normalized 128-bit int (32 hex chars) derived
  from the above

Store the first-seen `agentweave.trace_id` per request; ignore subsequent spans
with the same value that arrive after a success.

---

## FAQ

**Q: Does this affect span IDs?**  
No. Only the trace ID is deterministic. Each span still gets a unique, random
span ID.

**Q: What if two different requests accidentally share the same `traceId`?**  
Their spans will appear merged in the same trace view. Use request-scoped
identifiers (e.g. `order_id + user_id`) rather than counters to avoid collisions.

**Q: Is the `traceId` forwarded to the upstream LLM API?**  
No. `X-AgentWeave-Trace-Id` is stripped by the proxy before forwarding, just
like all other `X-AgentWeave-*` headers.

**Q: Can I use UUIDs?**  
Yes — strip the hyphens first:

```python
import uuid
trace_id = uuid.uuid4().hex  # "550e8400e29b41d4a716446655440000" (32 hex chars)

@trace_agent(traceId=trace_id)
def handle(msg): ...
```
