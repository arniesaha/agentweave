# agentweave-bridge — Installation Guide

OpenClaw plugin that creates root OTel spans per user message, enabling full conversation traces in AgentWeave.

## Quick install (recommended)

On any host with the AgentWeave CLI:

```bash
pip install agentweave
agentweave openclaw install \
  --proxy-url http://localhost:4000 \
  --otlp-endpoint http://localhost:4318 \
  --agent-id "$(hostname)" \
  --project my-project
openclaw gateway restart
agentweave doctor          # openclaw.bridge should PASS
```

This copies a prebuilt, self-contained bundle into
`~/.openclaw/user-plugins/agentweave-bridge` and registers it in
`~/.openclaw/openclaw.json`. Re-running is safe (idempotent); hand-edited config
values are preserved unless you pass `--force`. Remove with
`agentweave openclaw uninstall --purge`.

The manual steps below remain available for custom layouts.

## Prerequisites

- OpenClaw installed (`openclaw` CLI available)
- AgentWeave proxy running, for example `http://localhost:4000`
- An OTLP collector or Tempo endpoint reachable over HTTP, for example `http://localhost:4318`

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
| `otlpEndpoint` | ✅ | — | OTLP HTTP endpoint. In dogfood, point this at the AgentWeave collector; the dashboard still reads from Tempo. |
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

Developer-preview fleet guidance:

- Keep the plugin path local to each OpenClaw install; `agentweave doctor`
  warns if `plugins.entries.agentweave-bridge.path` points at a missing
  directory.
- Treat this as a per-machine rollout checklist, not full automation. Hosts
  with AgentWeave proxy or OTLP settings but no registered bridge will report
  an `openclaw.bridge` warning so they can be fixed before comparing Tempo
  coverage against the lakehouse session count.
- After updating a host, send one OpenClaw message and confirm a fresh
  `openclaw.turn` span appears before moving to the next machine.

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
