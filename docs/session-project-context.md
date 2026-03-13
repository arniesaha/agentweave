# Session & Project Context Propagation

Tag every LLM span with a session ID and project so you can answer:
*"In this conversation about Launchpad, how many tokens were used? How many turns happened?"*

## How it works

The proxy reads three optional request headers and stamps them as span attributes:

| Header | Span attribute | Type | Description |
|--------|---------------|------|-------------|
| `X-AgentWeave-Session-Id` | `prov.session.id` | string | Unique conversation thread ID |
| `X-AgentWeave-Project` | `prov.project` | string | High-level topic (launchpad, agentweave, nix, …) |
| `X-AgentWeave-Turn` | `prov.session.turn` | int | Turn number within the session |

All three are optional — spans without them still work normally.

## Sending headers

### From any HTTP client

```python
import anthropic

client = anthropic.Anthropic(
    base_url="http://localhost:4000",
    default_headers={
        "X-AgentWeave-Session-Id": "sess-abc123",
        "X-AgentWeave-Project": "launchpad",
        "X-AgentWeave-Turn": "1",
    }
)
```

```bash
curl http://localhost:4000/v1/messages \
  -H "X-AgentWeave-Session-Id: sess-abc123" \
  -H "X-AgentWeave-Project: launchpad" \
  -H "X-AgentWeave-Turn: 1" \
  ...
```

### From OpenClaw (static headers)

Add to `models.providers.anthropic.headers` in `openclaw.json`:

```json
{
  "models": {
    "providers": {
      "anthropic": {
        "baseUrl": "http://localhost:4000",
        "headers": {
          "X-AgentWeave-Session-Id": "nix-main",
          "X-AgentWeave-Project": "nix",
          "X-AgentWeave-Agent-Id": "nix-v1"
        }
      }
    }
  }
}
```

Every LLM call OpenClaw makes will be tagged automatically. No code changes needed.

> **Note:** `X-AgentWeave-Turn` can't be a static header since it needs to increment per message. For dynamic turn injection, a proxy-side counter keyed on session ID would be needed (see [open issue](#turn-counter)).

## Grafana panels

Three panels were added to the `agentweave-overview` dashboard:

### Session Explorer
Table of all spans that have a `prov.session.id`, filterable by the `${project}` template variable. Shows trace ID, duration, model, and agent per call.

### Project Rollup
Bar chart grouped by `prov.project`, sorted by call count. Shows token spend distribution across projects over time.

### Turn Depth Histogram
Distribution of `prov.session.turn` values. Identifies sessions with unusually high back-and-forth (complex or inefficient tasks).

## Schema constants

Defined in `sdk/python/agentweave/schema.py`:

```python
PROV_SESSION_ID   = "prov.session.id"
PROV_PROJECT      = "prov.project"
PROV_SESSION_TURN = "prov.session.turn"
```

## Security note

All three headers are stripped before the request is forwarded to the upstream provider (Anthropic, Google, OpenAI). They never leave the proxy.

## Known limitations & future work

- **Turn counter:** Static header injection can't auto-increment. Options: (a) native support in OpenClaw to inject session turn from its internal counter, (b) proxy-side stateful counter keyed on `X-AgentWeave-Session-Id`.
- **Project auto-detection:** Currently set manually per agent. A future enhancement could infer project from request content or agent ID mapping.
