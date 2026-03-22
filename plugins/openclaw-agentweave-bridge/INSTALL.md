# agentweave-bridge — Installation Guide

OpenClaw plugin that creates root OTel spans per user message, enabling full conversation traces in AgentWeave.

## Prerequisites

- OpenClaw installed (`openclaw` CLI available)
- AgentWeave proxy running (NodePort 30400)
- Grafana Tempo receiving OTLP (NodePort 30418)

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
          "otlpEndpoint": "http://192.168.1.70:30418",
          "agentId": "nix-v1",
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
| `agentId` | ❌ | `"nix-v1"` | Agent identifier stamped on all spans |
| `project` | ❌ | — | Project tag for filtering in AgentWeave dashboard |
| `enabled` | ❌ | `true` | Set to `false` to disable without removing config |

## Step 3 — Restart OpenClaw

```bash
openclaw gateway restart
```

## Step 4 — Verify traces appear

Send a message to your OpenClaw agent. Within ~10 seconds, open Grafana Explore:

```
http://192.168.1.70:30300/explore
```

Run a TraceQL query:
```
{ resource.service.name = "agentweave-proxy" && span.prov.agent.id = "nix-v1" }
```

You should see an `openclaw.turn` root span for the message, with LLM call spans as children.

Or use the AgentWeave dashboard Session Explorer:
- Open `agentweave.arnabsaha.com` → Sessions tab
- Click your session node → "Open in Grafana"

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
