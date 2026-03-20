# AgentWeave — Project Status
*Last updated: March 20, 2026*

---

## Current State (v0.2.x)

### Proxy
- Multi-provider transparent proxy: Anthropic, OpenAI, Google Gemini
- Deployed on k3s NAS: NodePort 30400 (nix-v1), 30401 (max-v1), 30402 (nix-subagent-v1)
- Reads `AGENTWEAVE_AGENT_TYPE` from env — auto-tags spans with `prov.agent.type`
- Session context via `POST /session` or env vars: `AGENTWEAVE_SESSION_ID`, `AGENTWEAVE_PARENT_SESSION_ID`, `AGENTWEAVE_TASK_LABEL`, `AGENTWEAVE_AGENT_TYPE`
- Sub-agent attribution: `prov.parent.session.id`, `prov.agent.type`, `prov.task.label`
- traceparent passthrough: reads incoming `traceparent` header, sets `prov.trace.parent` on span, forwards downstream
- Benchmarks: ~6ms p50 overhead on LLM calls (<2%)

### Dashboard (v25)
- Live at agentweave.arnabsaha.com (NodePort 30896)
- Overview tab: LLM calls, cost, latency by model over time
- Session Explorer tab: interactive multi-level sub-agent graph with parent→child edges
- Session replay: click any node → session ID auto-fills → "Open in Grafana" opens Tempo Explore
- Nodes coloured by agent type (main = purple, subagent = teal)
- Time range filter: 15m / 1h / 3h / 6h / 24h / 7d
- Fullscreen mode with body scroll lock
- Mobile-friendly stat cards (responsive font sizing)

### Python SDK (`agentweave-sdk==0.1.1` on PyPI)
- `@trace_tool`, `@trace_agent`, `@trace_llm` decorators
- `auto_instrument()` — zero-decorator patching for Anthropic + OpenAI SDKs
- W3C PROV-O OTel spans, W3C TraceContext propagation
- Sub-agent attribution parameters: `parent_session_id`, `agent_type`, `turn_depth`
- 240 tests passing

### TypeScript SDK (`agentweave-sdk` on npm)
- Same decorator API as Python: `traceAgent`, `traceTool`, `traceLlm`
- W3C trace context propagation
- 10 tests passing

### Go SDK (`go get github.com/arniesaha/agentweave-go`)
- `TraceTool`, `TraceAgent`, `TraceLlm`
- 4 tests passing

### Dogfooding (Live)
- Nix (NAS OpenClaw): all LLM calls traced via NodePort 30400, tagged `nix-v1 / main`
- Max (Mac Mini): all LLM calls traced via NodePort 30401, tagged `max-v1 / main`
- Sub-agents (Claude Code spawns): traced via NodePort 30402, tagged `nix-subagent-v1 / subagent`
- Grafana + Tempo: `http://192.168.1.70:30300`

---

## Recently Closed Issues
| # | Title | Closed |
|---|-------|--------|
| #100 | feat: session-level distributed tracing (#44) | Mar 20, 2026 |
| #99 | fix: compute_cost called without cache token counts | Mar 19, 2026 |
| #98 | docs: update stale docs | Mar 19, 2026 |
| #97 | README: update screenshots + framework example backlinks | Mar 18, 2026 |
| #96 | Dashboard: sub-agent edges not rendered | Mar 18, 2026 |

## Open Issues
| # | Title | Notes |
|---|-------|-------|
| #31 | feat: evals, prompt management, and review framework | Post-core — deferred |
| #2 | ci: publish to PyPI on version tags via GitHub Actions | Nice-to-have |
| #91 | ci: npm publish workflow for TypeScript SDK | Nice-to-have |

---

## Repo
https://github.com/arniesaha/agentweave

## Local Path
`/home/Arnab/dev/agentweave`
