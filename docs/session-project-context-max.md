# AgentWeave Header Integration — Max (Mac Mini)

Max runs as a TypeScript agent in [agent-max (pi-mono)](https://github.com/arniesaha/agent-max).
It is **not** an OpenClaw instance — header injection needs to happen in the agent-max codebase,
not via OpenClaw config.

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

Max uses Google/Gemini via the `@google/generative-ai` SDK (or equivalent). The AgentWeave proxy-max
instance is running on the NAS at NodePort **30401** and handles Google routes (`/v1beta/models/...`).

### 1. Point Max's LLM client at the proxy

In agent-max (`src/agent.ts` or wherever the Gemini client is initialized), change the base URL:

```typescript
// Before
const genAI = new GoogleGenerativeAI(process.env.GOOGLE_API_KEY)

// After — route through AgentWeave proxy
import { GoogleGenerativeAI, GoogleGenerativeAIClientOptions } from "@google/generative-ai"

const clientOptions: GoogleGenerativeAIClientOptions = {
  baseUrl: "http://192.168.1.70:30401",
  customHeaders: {
    "X-AgentWeave-Session-Id": "max-main",
    "X-AgentWeave-Project": "max",
    "X-AgentWeave-Agent-Id": "max-v1",
  },
}
const genAI = new GoogleGenerativeAI(process.env.GOOGLE_API_KEY!, clientOptions)
```

> The proxy forwards to `https://generativelanguage.googleapis.com` automatically — the API key still travels in the request as usual.

### 2. Build and deploy

```bash
cd ~/max/projects/agent-max
npm run build
# restart agent-max launchd service or however it's deployed
```

### 3. Verify traces appear in Grafana

Open `http://o11y.arnabsaha.com` → AgentWeave Overview → Session Explorer.
After Max makes an LLM call, a row with `prov.project = max` and `prov.agent.id = max-v1` should appear.
