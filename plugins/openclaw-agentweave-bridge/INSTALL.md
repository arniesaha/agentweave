# agentweave-bridge — Installation Guide

OpenClaw plugin that creates root OTel spans per user message, enabling full conversation traces in AgentWeave.

## Prerequisites

- OpenClaw installed (`openclaw` CLI available)
- AgentWeave proxy running, for example `http://localhost:4000`
- An OTLP/Tempo collector reachable over HTTP, for example `http://localhost:4318`

## Step 1 — Install plugin dependencies

```bash
cd /path/to/agentweave/plugins/openclaw-agentweave-bridge
npm install
```

## Step 2 — Register plugin in OpenClaw config

Edit `~/.openclaw/openclaw.json` and add the plugin under `plugins.entries`:

```json
{
  "plugins": {
    "entries": {
      "agentweave-bridge": {
        "path": "/absolute/path/to/agentweave/plugins/openclaw-agentweave-bridge",
        "config": {
          "otlpEndpoint": "http://localhost:4318",
          "proxyUrl": "http://localhost:4000",
          "agentId": "openclaw-v1",
          "project": "agentweave",
          "enabled": true
        }
      }
    }
  }
}
```

**Config options:**

| Field | Required | Default | Description |
|---|---|---|---|
| `otlpEndpoint` | ✅ | — | OTLP HTTP endpoint for Grafana Tempo |
| `proxyUrl` | ❌ | `AGENTWEAVE_PROXY_URL` | AgentWeave proxy URL injected into sub-agent provider env vars |
| `agentId` | ❌ | `"nix-v1"` | Agent identifier stamped on all spans; set this explicitly per machine |
| `project` | ❌ | — | Project tag for filtering in AgentWeave dashboard |
| `enabled` | ❌ | `true` | Set to `false` to disable without removing config |

For shared hosts, replace `localhost` with the proxy or collector host that is
reachable from that machine, such as `http://agentweave-proxy.internal:4000`.

## Step 3 — Restart OpenClaw

```bash
openclaw gateway restart
```

## Step 4 — Verify the local install

Run AgentWeave doctor on the same machine:

```bash
AGENTWEAVE_PROXY_URL=http://localhost:4000 \
AGENTWEAVE_OTLP_ENDPOINT=http://localhost:4318 \
agentweave doctor
```

The `openclaw.bridge` check should pass. If it warns, confirm that
`~/.openclaw/openclaw.json` has a `plugins.entries.agentweave-bridge` entry and
that `config.enabled` is not `false`.

Then send a message to your OpenClaw agent. Within ~10 seconds, open Grafana Explore:

```
http://localhost:3000/explore
```

Run a TraceQL query:
```
{ resource.service.name = "agentweave-proxy" && span.prov.agent.id = "openclaw-v1" }
```

You should see an `openclaw.turn` root span for the message, with LLM call spans as children.

Or use the AgentWeave dashboard Session Explorer:
- Open your AgentWeave dashboard → Sessions tab
- Click your session node → "Open in Grafana"

## Fleet rollout checklist

Repeat these steps on every OpenClaw machine:

1. Copy or clone `plugins/openclaw-agentweave-bridge` to a stable local path.
2. Run `npm install` in the plugin directory.
3. Add the `agentweave-bridge` entry to that machine's `openclaw.json`.
4. Set machine-specific `agentId` / `project` values.
5. Restart OpenClaw.
6. Run `agentweave doctor` and confirm `openclaw.bridge` passes.

## How it works

```
User sends message
       ↓
OpenClaw emits: message.queued
       ↓
agentweave-bridge creates root span "openclaw.turn"
  → sets AGENTWEAVE_TRACEPARENT in process env
  → sets AGENTWEAVE_SESSION_ID in process env
       ↓
OpenClaw processes message → LLM calls → tool calls
  (proxy reads AGENTWEAVE_TRACEPARENT, attaches LLM spans as children)
       ↓
OpenClaw emits: message.processed
       ↓
agentweave-bridge ends root span with outcome + duration
```

## Uninstall

Remove the `agentweave-bridge` entry from `~/.openclaw/openclaw.json` and restart:
```bash
openclaw gateway restart
```

No data is deleted — historical traces remain in Tempo.
