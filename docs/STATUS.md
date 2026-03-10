# AgentWeave — Project Status
*Last updated: March 10, 2026*

---

## What's Done (v0.1.0 ✅)

### Python SDK
- `@trace_tool`, `@trace_agent`, `@trace_llm` decorators
- W3C PROV-O compatible OTel spans
- OTLP exporter — works with Grafana Tempo, Langfuse, Jaeger
- Published to PyPI as `agentweave-sdk` (name `agentweave` was taken)

### Proxy Layer
- Multi-provider transparent proxy (Anthropic + Google Gemini)
- Auto-detects provider from request path
- Emits OTel spans for every LLM call — token counts, latency, model
- Dockerfile ready
- k8s manifests drafted (not end-to-end validated yet)

### CI/CD
- GitHub Actions: test workflow on PR
- PyPI publish pipeline on version tags (issue #2 — needs validation)

### Docs & Screenshots
- README with architecture diagram (Mermaid)
- Grafana Tempo trace screenshots
- Proxy setup guide

### GitHub Issues Open
| # | Title | Status |
|---|-------|--------|
| 1 | feat: sub-agent span linking — propagate trace context across spawned agents | Open |
| 2 | ci: publish to PyPI on version tags via GitHub Actions | Open |
| 3 | feat: JavaScript / TypeScript SDK | Open |
| 4 | test: validate Dockerfile + k8s manifests end-to-end | Open |

---

## Phase 1 Complete ✅ (Mar 10, 2026)
- #2 PyPI CI validated — `agentweave-sdk==0.1.1` live
- #4 k8s deploy validated — proxy running at 192.168.1.70:30400, Tempo connected
- Bug fixed: k8s env var collision (`AGENTWEAVE_PROXY_PORT` → `AGENTWEAVE_LISTEN_PORT`)
- Notification pipeline: `nix-notify.sh` uses hardcoded Node v24 path

### Lessons from Phase 1
- NAS SIGTERMs exec sessions after ~2-3 min — use `sessions_spawn` for sub-agents, not `exec + background`
- Inline exec results: Nix must message Arnab proactively on completion, not wait to be asked
- `nix-notify.sh` works but only if the sub-agent process isn't killed first

## Roadmap (discussed Mar 10, 2026)

### SDKs
- [ ] **TypeScript/JavaScript SDK** — publish to npm (issue #3)
- [ ] **Go SDK** — publish to pkg.go.dev

### Infrastructure
- [ ] **Proxy layer deployment** — validate as standalone deployment option
- [ ] **Kubernetes manifests** — fix and validate end-to-end (issue #4)

### Agentic Dev Lifecycle (Big Idea)
- [ ] Structure GitHub issues with proper labels and milestones by sub-feature
- [ ] Thinker/coder sub-agent picks up issues → implements → opens PR
- [ ] Max (Mac Mini) acts as reviewer agent
- [ ] All agent interactions traced through AgentWeave → lands in Grafana
- [ ] **Dogfooding**: agents building AgentWeave *using* AgentWeave

### Dogfooding Environment
- Nix (NAS) + Max (Mac Mini) as reference multi-agent setup
- All A2A calls instrumented via AgentWeave proxy
- Traces → Grafana Tempo at 192.168.1.70
- Use real Launchpad/Cortex pipelines as test workloads

---

## Repo
https://github.com/arniesaha/agentweave

## Local Path
`/home/Arnab/dev/agentweave`
