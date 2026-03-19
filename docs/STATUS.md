# AgentWeave — Project Status
*Last updated: March 18, 2026*

---

## Current State (v0.2.x)

### Proxy
- Multi-provider transparent proxy: Anthropic, OpenAI, Google Gemini
- Deployed on k3s NAS: NodePort 30400 (nix-v1), 30401 (max-v1), 30402 (nix-subagent-v1)
- Reads `AGENTWEAVE_AGENT_TYPE` from env — auto-tags spans with `prov.agent.type`
- Session context via `POST /session` or env vars: `AGENTWEAVE_SESSION_ID`, `AGENTWEAVE_PARENT_SESSION_ID`, `AGENTWEAVE_TASK_LABEL`, `AGENTWEAVE_AGENT_TYPE`
- Sub-agent attribution: `prov.parent.session.id`, `prov.agent.type`, `prov.task.label`
- Benchmarks: ~6ms p50 overhead on LLM calls (<2%)

### Dashboard
- Live AgentWeave dashboard (React SPA) at NodePort 30896
- Overview tab: LLM calls, cost, latency by model over time
- Session Explorer tab: interactive multi-level sub-agent graph with parent→child edges
- Nodes coloured by agent type (main = purple, subagent = teal)
- Time range filter: 1h / 3h / 6h / 24h / 7d

### Python SDK (`agentweave-sdk==0.1.1` on PyPI)
- `@trace_tool`, `@trace_agent`, `@trace_llm` decorators
- `auto_instrument()` — zero-decorator patching for Anthropic + OpenAI SDKs
- W3C PROV-O OTel spans, W3C TraceContext propagation
- Sub-agent attribution parameters: `parent_session_id`, `agent_type`, `turn_depth`
- 237 tests passing

### TypeScript SDK (`agentweave-sdk` on npm)
- Same decorator API as Python: `traceAgent`, `traceTool`, `traceLlm`
- W3C trace context propagation
- 10 tests passing

### Go SDK (`go get github.com/arniesaha/agentweave-go`)
- `TraceTool`, `TraceAgent`, `TraceLlm`
- 4 tests passing

### Dogfooding (Live)
- Nix (NAS OpenClaw): all LLM calls traced via NodePort 30400, tagged `nix-v1 / main`
- Max (Mac Mini pi-mono): all LLM calls traced via NodePort 30401, tagged `max-v1 / main`
- Sub-agents (Claude Code spawns): traced via NodePort 30402, tagged `nix-subagent-v1 / subagent`
- Grafana + Tempo: `http://192.168.1.70:30300` (admin/observability123)

---

## Recently Closed Issues
| # | Title | Closed |
|---|-------|--------|
| #96 | Dashboard: sub-agent edges not rendered (parent-child graph missing) | Mar 18, 2026 |
| #97 | README: update screenshots + framework example backlinks | Mar 18, 2026 |

## Open Issues
| # | Title |
|---|-------|
| #1 | feat: sub-agent span linking — propagate trace context across spawned agents |
| #2 | ci: publish to PyPI on version tags via GitHub Actions |
| #4 | test: validate Dockerfile + k8s manifests end-to-end |
| #91 | ci: npm publish workflow for TypeScript SDK |
| #98 | docs: update stale docs |

---

## Repo
https://github.com/arniesaha/agentweave

## Local Path
`/home/Arnab/dev/agentweave`
