# AgentWeave Header Integration — Max (Mac Mini)

This is the todo for wiring Max's OpenClaw to tag LLM calls with AgentWeave session/project context.
Nix's side is already done — this doc covers what needs to happen on Max.

## What was done on Nix (reference)

`~/.openclaw/openclaw.json` on the NAS was patched to add static headers to the Anthropic provider:

```json
{
  "models": {
    "providers": {
      "anthropic": {
        "baseUrl": "http://192.168.1.70:30400",
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

OpenClaw was restarted via SIGUSR1 and all Anthropic calls now appear in Grafana tagged under project `nix`.

## What needs to happen on Max

Max uses Google/Gemini. The AgentWeave proxy-max instance is running on the NAS at NodePort **30401** and already handles Google routes (`/v1beta/models/...`).

### 1. Update `~/.openclaw/openclaw.json` on the Mac Mini

Change `models.providers.google.baseUrl` to route through the proxy, and add headers:

```json
{
  "models": {
    "providers": {
      "google": {
        "baseUrl": "http://192.168.1.70:30401",
        "headers": {
          "X-AgentWeave-Session-Id": "max-main",
          "X-AgentWeave-Project": "max",
          "X-AgentWeave-Agent-Id": "max-v1"
        }
      }
    }
  }
}
```

> The proxy forwards to `https://generativelanguage.googleapis.com` automatically — the API key is still picked up from the existing `apiKey` field.

### 2. Restart OpenClaw on Mac Mini

```bash
# Via launchctl
launchctl stop ai.openclaw.gateway && launchctl start ai.openclaw.gateway

# Or send SIGUSR1 for a hot reload
pkill -USR1 -f 'node.*openclaw'
```

### 3. Verify traces appear in Grafana

Open `http://o11y.arnabsaha.com` → AgentWeave Overview → Session Explorer.
After Max makes an LLM call, a row with `prov.project = max` and `prov.agent.id = max-v1` should appear.

## Note on OpenClaw installation on Max

As of 2026-03-12, OpenClaw on the Mac Mini has a broken install:
```
Error: Cannot find module '/opt/homebrew/lib/node_modules/openclaw/dist/index.js'
```
This needs to be fixed first before the header changes will have any effect.
