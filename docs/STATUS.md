# AgentWeave Project Status

Last updated: 2026-05-27

## Current Release Line

AgentWeave is in developer preview on the `0.3.x` line.

| Component | Current version/source | Status |
|---|---:|---|
| Python SDK / proxy | `0.3.0` in `sdk/python/pyproject.toml` | Active |
| TypeScript SDK | `0.3.0` in `sdk/js/package.json` | Active |
| Go SDK | tag-based module `github.com/arniesaha/agentweave-go` | Preview |
| Dashboard | built from `dashboard/` | Dogfooded |

## Public Developer-Preview Path

The public quickstart should stay local-first:

```bash
pip install "agentweave-sdk[proxy]"
agentweave proxy start --port 4000 --endpoint http://localhost:4318
export ANTHROPIC_BASE_URL=http://localhost:4000/v1
```

Use normal provider API keys in the local environment. Private NAS NodePorts,
tunnels, and proxy-side credential injection belong in dogfood runbooks rather
than public setup docs.

## What Works Today

- Multi-provider transparent proxy paths for Anthropic, OpenAI-compatible, and
  Gemini-compatible APIs.
- Python SDK decorators and `auto_instrument()` for Anthropic/OpenAI clients.
- TypeScript and Go SDKs with basic tracing APIs.
- OpenClaw bridge dogfooding with session, agent, model, token, and cost
  attribution.
- Dashboard views for overview, routing, session graph, and replay.
- Framework examples for LangGraph, CrewAI, AutoGen, and OpenAI Agents SDK.

## Launch-Readiness Focus

The developer-preview milestone is about confidence before broader public
distribution:

1. Agentic install/debugging through `agentweave doctor`.
2. Compatibility matrix and smoke checks for common agent frameworks.
3. Docs/version/source-of-truth consistency.
4. Dogfood trace data-quality gate.
5. Sanitized dogfood demo traces and public screenshots.

## Private Dogfood Deployment

Arnab's live dogfood stack runs on private infrastructure and is intentionally
not the public install path. Internal details such as LAN IPs, Kubernetes
ClusterIPs, and Cloudflare tunnel hosts should stay in deployment runbooks.

Useful private runbooks:

- `docs/DEPLOYMENT-RUNBOOK.md`
- `docs/attribution-runbook.md`
- `docs/project-tracking.md`

## Repository

https://github.com/arniesaha/agentweave
