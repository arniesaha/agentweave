# AgentWeave — Deployment Runbook

*Everything not obvious from the repo. Last updated Mar 28, 2026.*

---

## The Big Picture

AgentWeave is a **transparent LLM observability layer**. It sits between your AI agents and the LLM APIs, recording every call as an OpenTelemetry span in Tempo, then visualizing them on a React dashboard backed by Prometheus spanmetrics.

```
Your Agent Code (with per-request X-AgentWeave-* headers)
    ↓
AgentWeave Proxy (single instance, port 30400)
    ├── forwards request to Anthropic/OpenAI/Gemini
    ├── writes OTel span → Tempo (:30418 OTLP)
    └── Tempo → Prometheus remote_write → Grafana
```

---

## Infrastructure (Home Lab K3s Cluster)

### Nodes
| Node | IP | Role | What runs here |
|------|----|------|----------------|
| `arnabsnas` | 192.168.1.70 | control-plane + worker | proxies, dashboard, registry |
| `ubuntu` (OrbStack VM on Mac Mini) | 192.168.139.77 | worker | Tempo, (future: Grafana) |

**Critical networking rule:** The OrbStack VM is on an isolated subnet. Two things must survive reboots:
- **NAS:** `/etc/network/interfaces.d/orbstack-route` → `up ip route add 192.168.139.0/24 via 192.168.1.149`
- **Mac Mini:** `/etc/sysctl.conf` → `net.inet.ip.forwarding=1`

If Tempo goes unreachable, check these first.

### Local Registry
All images are pushed to `localhost:5000` (running on NAS). The Mac Mini node cannot pull from `localhost:5000` — so **all deployments that use local images must pin to NAS** via `nodeSelector: {kubernetes.io/hostname: arnabsnas}`.

Tempo is the exception: it uses the official `grafana/tempo` image (public), so it can run on Mac Mini.

---

## What's Deployed

### Namespace: `agentweave`
| Deployment | NodePort | Purpose |
|------------|----------|---------|
| `agentweave-proxy` | 30400 | Single shared proxy — all clients use per-request headers for attribution |
| `agentweave-dashboard` | 30896 | React SPA dashboard (also at `agentweave.arnabsaha.com`) |

> **Consolidated (Mar 2026):** Previously there were 3 proxy instances (`agentweave-proxy` :30400, `agentweave-proxy-max` :30401, `agentweave-proxy-nix-subagent` :30402). These were consolidated into a single proxy. Agent attribution is now done via per-request `X-AgentWeave-Agent-Id` headers instead of separate deployments.

### Namespace: `monitoring`
| Deployment | Purpose |
|------------|---------|
| `tempo` | Trace storage (on Mac Mini node) |
| `minio` | S3-compatible storage (unused — MinIO config exists but we use local-path) |
| `kube-prometheus-stack` | Grafana + Prometheus (pre-existing, not AgentWeave-managed) |

---

## Proxy Auth Model

All proxies run in **passthrough mode** — no `AGENTWEAVE_PROXY_TOKEN`, no `AGENTWEAVE_ANTHROPIC_API_KEY`. Each caller's SDK handles authentication natively, and the proxy forwards headers untouched to Anthropic.

### How each client authenticates

All clients point at the single proxy at `http://192.168.1.70:30400` and use per-request headers for attribution.

| Client | SDK | Auth method | Attribution header |
|--------|-----|-------------|--------------------|
| **Nix/OpenClaw** (NAS) | OpenClaw embedded (Node.js `@anthropic-ai/sdk`) | OAuth token (`sk-ant-oat01-*`) via `Authorization: Bearer` | Static headers in `openclaw.json` |
| **Max/pi-agent** (Mac Mini) | `@anthropic-ai/sdk` with AgentWeave JS SDK | OAuth token via SDK | JS SDK sets headers per-request |
| **Claude Code** (Mac Mini) | Claude Code CLI | OAuth token via SDK | `ANTHROPIC_CUSTOM_HEADERS` in `settings.json` + hooks |
| **Claude Code** (NAS) | Claude Code CLI | OAuth token via SDK | `ANTHROPIC_CUSTOM_HEADERS` in `settings.json` |
| **Nix A2A Server** (NAS :8771) | Express server | Trace context propagation | Headers in general handler |
| **Max A2A Server** (Mac Mini :8770) | Express server | Trace context extraction | Proxy session API |

### Why passthrough mode is required for OAuth

Anthropic OAuth tokens (`sk-ant-oat01-*`) require specific handling by the Node.js `@anthropic-ai/sdk` to work with Sonnet/Opus models. The SDK adds:
- `anthropic-beta: claude-code-20250219,oauth-2025-04-20` headers
- `?beta=true` query parameter
- TLS/HTTP fingerprint headers (`X-Stainless-*`, `user-agent: claude-cli/*`, `x-app: cli`)

These cannot be replicated by the Python proxy's `httpx` client. If the proxy injects its own `x-api-key` or `Authorization` header (`AGENTWEAVE_ANTHROPIC_API_KEY`), it overrides the SDK's native OAuth flow and breaks authentication.

**DO NOT set `AGENTWEAVE_ANTHROPIC_API_KEY` to an OAuth token** — it will fail with 400/401 for all non-Haiku models.

### Fallback: API key injection

If OAuth stops working or you need to bypass it, you can inject a standard Anthropic API key (`sk-ant-api03-*` from console.anthropic.com):

```bash
kubectl create secret generic agentweave-proxy -n agentweave \
  --from-literal=anthropic-api-key='sk-ant-api03-YOUR_KEY' \
  --from-literal=proxy-token='' \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl delete pod -n agentweave -l app=agentweave-proxy
```

This costs API credits instead of using the Max subscription. To revert to passthrough:

```bash
kubectl create secret generic agentweave-proxy -n agentweave \
  --from-literal=proxy-token='' \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl delete pod -n agentweave -l app=agentweave-proxy
```

### Incident history (Mar 19-20, 2026)

1. **Root cause:** `AGENTWEAVE_ANTHROPIC_API_KEY` was set to an OAuth token (`sk-ant-oat01-*`). The proxy injected it as `x-api-key`, which Anthropic rejected (OAuth tokens must use `Authorization: Bearer` + specific beta headers that only the Node.js SDK provides).
2. **Misdiagnosis:** Initially appeared as an OpenClaw update issue (2026.3.11 → 2026.3.13), then as an Anthropic API restriction on OAuth tokens. Multiple config changes (removing `baseUrl`, regenerating tokens, downgrading OpenClaw) did not fix it.
3. **Resolution:** Removed `AGENTWEAVE_ANTHROPIC_API_KEY` from the proxy secret, making it a pure passthrough. OpenClaw's embedded Node.js SDK handles OAuth natively, same as Max's pi-mono setup.
4. **Key learning:** OAuth tokens only work when the Anthropic Node.js SDK makes the request end-to-end. Python `httpx`, raw `curl`, and even Node.js with manual header construction all fail with 400 "Error" for Sonnet/Opus models. The SDK adds TLS-level fingerprinting that Anthropic validates server-side.

---

## Tempo Configuration

Config lives in `ConfigMap/tempo-config` in `monitoring` namespace. Key lessons from today:

**What works (v2.10.1):**
```yaml
overrides:
  defaults:
    metrics_generator:
      processors: [span-metrics]   # DO NOT add local-blocks — it crashes on fresh PVCs
```

**What doesn't:**
- `local-blocks` processor: requires traces WAL to be initialized. Fails silently on fresh PVCs with "instance creation in backoff" → no spanmetrics → empty dashboard panels.
- `overrides.defaults.metrics_generator.max_active_series` field: ignored in v2.10.1 (limit still 0). Doesn't matter since removing `local-blocks` fixes the 0-series issue.
- `querier.search.prefer_self`: invalid field in arm64 build, causes crash.

**Spanmetrics flow:**
Tempo → Prometheus remote_write → `traces_spanmetrics_calls_total`, `traces_spanmetrics_latency_*` → Dashboard queries these for "LLM Calls over Time", "P95 Latency", "Calls by Model" panels.

---

## Dashboard Deploy

**Always use the deploy script** — never push to `:latest` and rollout restart:
```bash
bash /home/Arnab/dev/agentweave/scripts/deploy-dashboard.sh
```

This script:
1. Reads current image tag from k8s (e.g. `v18`)
2. Builds with `--no-cache` (critical — Docker layer cache hides new JS bundles)
3. Tags as `v19`, pushes to registry
4. `kubectl set image` to versioned tag
5. Verifies new JS bundle hash is live

**Why not `:latest`:** The k8s deployment was pinned to `:v15`. Pushing to `:latest` does nothing if the deployment spec doesn't use that tag. Always increment the version.

---

## Session Attribution

Every proxy call can carry session metadata via headers or env vars:

```
X-AgentWeave-Session-Id: nix-main-abc123
X-AgentWeave-Parent-Session-Id: nix-main-abc123  (for sub-agents)
X-AgentWeave-Agent-Id: nix-v1
X-AgentWeave-Agent-Type: main | subagent
X-AgentWeave-Task-Label: "fix issue #99"
```

The session graph in the dashboard draws edges from `parent_session_id` → `session_id`. For the graph to show nesting, sub-agents MUST set `parent_session_id` pointing to their parent's session.

**All clients use the single proxy:**
- All agents: `http://192.168.1.70:30400`
- Attribution is done via per-request `X-AgentWeave-Agent-Id` headers, not separate proxy instances

---

## Cloudflare Tunnel

The tunnel (`nas-tunnel`) exposes `agentweave.arnabsaha.com` → `localhost:30896`.

It runs as a systemd user service on the NAS:
```bash
systemctl --user status cloudflared
systemctl --user restart cloudflared
```

Binary location: `/home/Arnab/.local/bin/cloudflared` (NOT `/usr/local/bin/`).
Config: `~/.cloudflared/config.yml`

**The tunnel dies frequently** due to Cloudflare-side connection drops. The systemd service auto-restarts with `Restart=always, RestartSec=5`. If it's not running, just `systemctl --user start cloudflared`.

---

## Cost Tracking

The proxy calculates `cost.usd` per span using a pricing table in `sdk/python/agentweave/pricing.py`.

**Bug fixed Mar 19:** Cache token counts weren't passed to `compute_cost`, causing ~7x inflated costs for sessions with high cache hit rates (like Nix's 99% cache hit rate). Fix: both streaming and non-streaming paths now pass `cache_read_tokens` and `cache_write_tokens`.

Current pricing table covers: Claude Sonnet/Haiku/Opus (all major versions), GPT-4o, Gemini 2.0/2.5 Flash/Pro.

---

## What's NOT in the Repo

Things you need to know that aren't documented elsewhere:

1. **The NAS registry at `localhost:5000`** is not a k8s service — it's a Docker container running directly on the NAS host. Check with `docker ps | grep registry` if images fail to push.

2. **The `agentweave-proxy` secret has NO `anthropic-api-key`** — this is intentional. The proxy runs in passthrough mode. The caller's SDK handles auth. See "Proxy Auth Model" above.

3. **There is only one proxy deployment now** — `agentweave-proxy` on port 30400. The old `agentweave-proxy-max` (:30401) and `agentweave-proxy-nix-subagent` (:30402) have been removed. All clients use per-request headers for attribution.

4. **Tempo data is ephemeral.** Local-path PVCs on the Mac Mini node are stored in OrbStack's VM filesystem. If the VM is recreated or the PVC deleted, all trace history is lost. There is no backup. For production use, migrate to MinIO (config already exists as `tempo-config-minio`).

5. **The demo script** at `/home/Arnab/clawd/scripts/agentweave_graph_demo.py` does real Nix→Max A2A calls (Max's A2A server at `http://192.168.1.149:8770`). It requires `NIX_A2A_SECRET` from `.secrets`.

6. **`KUBECONFIG=/home/Arnab/.kube/config`** must be set explicitly — `sudo kubectl` doesn't work on this NAS.

7. **OAuth tokens only work via the Node.js SDK.** Raw `curl`, Python `httpx`, or manual header construction will fail with 400 "Error" for Sonnet/Opus models. Anthropic validates TLS-level SDK fingerprinting for consumer OAuth tokens. If you need to test the proxy from the command line, use a standard API key (`sk-ant-api03-*`), not OAuth.

8. **OpenClaw's `openclaw.json` must have `models.providers.anthropic.baseUrl`** set to the proxy URL (`http://192.168.1.70:30400`) for traces to flow through AgentWeave. Without it, OpenClaw calls Anthropic directly and bypasses tracing.

---

## Client Configuration

All clients point at the single proxy (`http://192.168.1.70:30400`) and send per-request headers for attribution. `AGENTWEAVE_CAPTURE_PROMPTS=1` is enabled on the proxy, so prompt/response previews are captured in spans.

The proxy is **pass-through** — clients with real API keys (OAuth or `sk-ant-api03-*`) pass through untouched. Key injection only fires when the client sends no real key (e.g., a placeholder like `"dummy"`).

The health endpoint at `/health` shows `key_injection` status.

| Client | Host | Config location | Key settings |
|--------|------|-----------------|--------------|
| **Nix/OpenClaw** | NAS (192.168.1.70) | `~/.openclaw/openclaw.json` | `models.providers.anthropic.baseUrl` = proxy URL; static `X-AgentWeave-*` headers |
| **Max/pi-agent** | Mac Mini (192.168.1.149) | `~/max/projects/agent-max/.env` | `ANTHROPIC_BASE_URL` = proxy URL; JS SDK handles headers |
| **Claude Code** | Mac Mini (192.168.1.149) | `~/.claude/settings.json` | `ANTHROPIC_BASE_URL` + `ANTHROPIC_CUSTOM_HEADERS` in `env` block; hooks (SubagentStop, Stop, PostToolUse) |
| **Claude Code** | NAS (192.168.1.70) | `~/.claude/settings.json` | `ANTHROPIC_BASE_URL` + `ANTHROPIC_CUSTOM_HEADERS` in `env` block |
| **Nix A2A Server** | NAS :8771 | Express server source | Trace context propagation in general handler |
| **Max A2A Server** | Mac Mini :8770 | Express server source | Trace context extraction + proxy session API |

> **OAuth tokens (`sk-ant-oat*`) MUST NOT be stored in k8s secrets** for proxy key injection. They expire and require the SDK-level auth flow to refresh. Only standard API keys (`sk-ant-api03-*`) should be used for key injection.

---

## Rollback Procedures

If the proxy goes down or becomes unreachable, each client can be reverted to call the Anthropic API directly. This bypasses all tracing but restores LLM functionality.

### Nix/OpenClaw (NAS)

**Current working config:** `~/.openclaw/openclaw.json` has `models.providers.anthropic.baseUrl` set to `http://192.168.1.70:30400` with static `X-AgentWeave-*` headers.

**Rollback to direct API:**

```bash
# On the NAS (192.168.1.70):
# Edit ~/.openclaw/openclaw.json — change models.providers.anthropic.baseUrl:
#   FROM: "http://192.168.1.70:30400"
#   TO:   "https://api.anthropic.com"
# Then restart the gateway:
openclaw gateway restart
```

**Verify:** Run a prompt through OpenClaw and confirm it gets a response. Check that no new spans appear in the AgentWeave dashboard (since the proxy is bypassed).

**Restore:** Change `baseUrl` back to `"http://192.168.1.70:30400"` and run `openclaw gateway restart`.

### Max/pi-agent (Mac Mini)

**Current working config:** `~/max/projects/agent-max/.env` has `ANTHROPIC_BASE_URL=http://192.168.1.70:30400`. The AgentWeave JS SDK handles per-request attribution headers. A2A server runs on port 8770.

**Rollback to direct API:**

```bash
# SSH to Mac Mini:
ssh arnabmac@arnabs-mac-mini.local

# Edit ~/max/projects/agent-max/.env:
#   Change ANTHROPIC_BASE_URL=http://192.168.1.70:30400
#   To:    ANTHROPIC_BASE_URL=https://api.anthropic.com

# Restart the service:
launchctl stop com.arnab.agent-max && launchctl start com.arnab.agent-max
```

**Verify:** Trigger a Max agent action and confirm it completes. Check logs in `~/max/projects/agent-max/` for successful API responses.

**Restore:** Set `ANTHROPIC_BASE_URL=http://192.168.1.70:30400` in `.env` and restart the service.

### Claude Code (Mac Mini)

**Current working config:** `~/.claude/settings.json` has an `env` block with `ANTHROPIC_BASE_URL` pointing to the proxy and `ANTHROPIC_CUSTOM_HEADERS` for attribution. Hooks (SubagentStop, Stop, PostToolUse) are configured for session tracking.

**Rollback to direct API:**

```bash
# SSH to Mac Mini:
ssh arnabmac@arnabs-mac-mini.local

# Edit ~/.claude/settings.json:
#   Option A: Remove the "env" block entirely
#   Option B: Set "ANTHROPIC_BASE_URL": "https://api.anthropic.com" and remove ANTHROPIC_CUSTOM_HEADERS

# No restart needed — the next Claude Code session will use the direct Anthropic API.
```

**Quick override (single session, no file edit):**

```bash
ANTHROPIC_BASE_URL="" claude
```

**Verify:** Start a new Claude Code session and confirm it responds. Check that the session does not appear in the AgentWeave dashboard.

**Restore:** Re-add the `env` block with `ANTHROPIC_BASE_URL` and `ANTHROPIC_CUSTOM_HEADERS` to `settings.json`.

### Claude Code (NAS)

**Current working config:** `~/.claude/settings.json` has an `env` block with `ANTHROPIC_BASE_URL` and `ANTHROPIC_CUSTOM_HEADERS`.

**Rollback to direct API:**

```bash
# On the NAS (192.168.1.70):
# Edit ~/.claude/settings.json — remove the "env" block entirely.
# The next Claude Code session will use the direct Anthropic API.
```

**Quick override (single session):**

```bash
ANTHROPIC_BASE_URL="" claude
```

**Verify:** Start a new Claude Code session and confirm it responds normally.

**Restore:** Re-add the `env` block to `settings.json`.

### Proxy-side recovery

If the proxy pod itself needs recovery (not a client-side issue):

```bash
# Check proxy pod status
kubectl get pods -n agentweave -l app=agentweave-proxy

# Check proxy logs
kubectl logs -n agentweave -l app=agentweave-proxy --tail=50

# Restart the proxy pod
kubectl delete pod -n agentweave -l app=agentweave-proxy

# Check health endpoint
curl -s http://192.168.1.70:30400/health | python3 -m json.tool

# Nuclear option: redeploy
kubectl rollout restart deployment agentweave-proxy -n agentweave
```

---

## Quick Diagnostics

```bash
# Is the dashboard serving fresh code?
curl -s http://192.168.1.70:30896/index.html | grep -o 'assets/index-[^"]*\.js'

# Is Tempo healthy?
curl -s http://$(kubectl get pod -n monitoring -l app=tempo -o jsonpath='{.items[0].status.podIP}'):3200/ready

# Are spanmetrics flowing to Prometheus?
curl -s "http://10.43.3.20:9090/api/v1/query?query=sum(traces_spanmetrics_calls_total)" | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['result'])"

# Is the tunnel up?
systemctl --user status cloudflared | grep Active

# Check image tag deployed vs what exists
kubectl get deployment agentweave-dashboard -n agentweave -o jsonpath='{.spec.template.spec.containers[0].image}'
```
