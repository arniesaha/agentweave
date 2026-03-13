# AgentWeave Header Integration — Max (Mac Mini)

Max runs as a TypeScript agent in [agent-max (pi-mono)](https://github.com/arniesaha/agent-max).
It is **not** an OpenClaw instance — header injection happens in the agent-max codebase,
not via OpenClaw config.

Nix's side is already done — this doc covers what was done on Max.

## Status: Done

All three headers (`Agent-Id`, `Session-Id`, `Project`) are live. Max's LLM calls
appear in Grafana tagged under `prov.project = max` and `prov.agent.id = max-v1`.

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

## What was done on Max

Max uses `pi-ai` (`@mariozechner/pi-ai`) which manages the Google GenAI and Anthropic SDKs
internally. The SDKs are not instantiated directly — instead, `getModel()` returns a model
object and `streamSimple()` handles streaming. Headers are injected via a `streamFn` wrapper
in the Agent constructor.

### How it works

In `src/agent.ts`, a custom `streamFn` wraps `streamSimple` to inject all AgentWeave headers
into every LLM call (both Google and Anthropic):

```typescript
import { streamSimple } from "@mariozechner/pi-ai";

// Wrap streamSimple to inject AgentWeave proxy auth + tracing headers
const proxyToken = process.env.AGENTWEAVE_PROXY_TOKEN;
const agentWeaveStreamFn: typeof streamSimple = (m, ctx, opts) =>
  streamSimple(m, ctx, {
    ...opts,
    headers: {
      ...opts?.headers,
      "X-AgentWeave-Agent-Id": "max-v1",
      "X-AgentWeave-Session-Id": "max-main",
      "X-AgentWeave-Project": "max",
      // Only for Anthropic — Google uses query param auth
      ...(proxyToken && provider === "anthropic"
        ? { Authorization: `Bearer ${proxyToken}` }
        : {}),
    },
  });

const agent = new Agent({
  // ...
  streamFn: agentWeaveStreamFn,
});
```

### Why `streamFn` instead of SDK-level config

- `pi-ai` hardcodes `baseUrl` in its model registry — patching `model.baseUrl` after
  `getModel()` is the only way to override it
- `pi-ai` doesn't expose a `headers` option in the Agent constructor — the `streamFn`
  wrapper is needed to inject custom headers into streaming calls
- The `Authorization: Bearer <token>` is only sent for Anthropic calls; sending it for
  Google calls would conflict with Google's API key auth (`key=` query param)

### Base URL routing

Both providers are routed through the AgentWeave proxy on the NAS via env vars:

```
ANTHROPIC_BASE_URL=http://192.168.1.70:30401
GOOGLE_GENAI_BASE_URL=http://192.168.1.70:30401
```

For Google, `/v1beta` is appended automatically (`model.baseUrl` is set to
`${GOOGLE_GENAI_BASE_URL}/v1beta`) because pi-ai clears `apiVersion` when a custom
`baseUrl` is set.

### Native SDK tracing (Phase 2)

In addition to the proxy headers, Max also has native AgentWeave TS SDK integration
(`src/tracing.ts`) for tool-level spans:

- `AgentWeaveConfig.setup()` is called at startup, sending spans directly to Tempo
  at `http://192.168.1.70:30418`
- All tools are wrapped with `traceTool()` for granular span tracing
- W3C trace context (`traceparent`) is injected into Nix A2A calls via
  `@opentelemetry/api` propagation for cross-agent trace linking

### Managed via launchd

Max runs as a launchd service (`com.arnab.agent-max`). Restart with:

```bash
cd ~/max/projects/agent-max
npm run build
launchctl stop com.arnab.agent-max && sleep 5 && launchctl start com.arnab.agent-max
```

### Verify traces appear in Grafana

Open `http://o11y.arnabsaha.com` → AgentWeave Overview → Session Explorer.
After Max makes an LLM call, a row with `prov.project = max` and `prov.agent.id = max-v1` should appear.
