# AgentWeave Proxy — Kubernetes Deployment

## Architecture

```
All clients (Nix, Max, Claude Code, A2A servers)
        │  (per-request X-AgentWeave-* headers for attribution)
        ▼ NodePort :30400
┌───────────────────────────────┐
│  agentweave-proxy pod         │
│  namespace: agentweave        │
│  (single instance)            │
│                               │
│  ClusterIP :4000 ◄── in-cluster agents
└───────────────────────────────┘
        │
        ▼ OTLP HTTP
  tempo.monitoring.svc.cluster.local:4318
        │
        ▼
   api.anthropic.com (upstream)
```

> **Note:** Previously there were 3 proxy instances (30400, 30401, 30402). These have been consolidated into a single proxy. The `nix-subagent-proxy` and `proxy-max` manifests have been removed.

## Deploy

### 1. Generate a proxy token

```bash
openssl rand -hex 32
```

### 2. Set the token in secret.yaml

Edit `secret.yaml` and replace `CHANGE_ME` with your generated token.

> **Never commit real tokens.** Use `kubectl create secret` or a secrets manager in production.

```bash
# Alternative: create secret directly (keeps token out of YAML)
kubectl create namespace agentweave
kubectl create secret generic agentweave-proxy \
  --namespace agentweave \
  --from-literal=proxy-token=$(openssl rand -hex 32)
```

### 3. Apply manifests

```bash
kubectl apply -f deploy/k8s/namespace.yaml
kubectl apply -f deploy/k8s/configmap.yaml
kubectl apply -f deploy/k8s/secret.yaml      # or use kubectl create secret above
kubectl apply -f deploy/k8s/deployment.yaml
kubectl apply -f deploy/k8s/service.yaml
kubectl apply -f deploy/k8s/dashboard-deployment.yaml
```

### Dashboard nginx credentials

The dashboard nginx proxy uses `envsubst` at container startup to inject
credentials from environment variables. **Never hardcode credentials in
`nginx.conf` or `nginx.conf.template`.**

To set dashboard proxy auth (e.g. for Grafana/Tempo basic-auth):

```bash
kubectl create secret generic agentweave-dashboard \
  --from-literal=GRAFANA_AUTH_HEADER="Basic <base64-user:pass>" \
  -n agentweave --dry-run=client -o yaml | kubectl apply -f -
kubectl rollout restart deployment agentweave-dashboard -n agentweave
```

To run without auth (default — uses k8s DNS direct):

```bash
kubectl create secret generic agentweave-dashboard \
  --from-literal=GRAFANA_AUTH_HEADER="" \
  -n agentweave --dry-run=client -o yaml | kubectl apply -f -
```

See `deploy/k8s/dashboard-secret.yaml` for the template (placeholder only, do
not apply directly).

### 4. Verify

```bash
kubectl get pods -n agentweave
kubectl logs -n agentweave -l app=agentweave-proxy
```

## Configure agents

### In-cluster agents (ClusterIP)

```bash
ANTHROPIC_BASE_URL=http://agentweave-proxy.agentweave.svc.cluster.local:4000
```

No auth header needed if the agent is inside the same cluster (firewall boundary is sufficient). Or add token for defence-in-depth.

### LAN agents (NodePort :30400)

All clients point at the single proxy. Attribution is via per-request headers.

```bash
# All agents use the same proxy:
ANTHROPIC_BASE_URL=http://192.168.1.70:30400
```

For the Anthropic SDK, add the agent ID header for attribution:

```python
import anthropic

client = anthropic.Anthropic(
    base_url="http://192.168.1.70:30400",
    default_headers={
        "X-AgentWeave-Agent-Id": "my-agent-v1",
    },
)
```

### Claude Code

Claude Code uses `ANTHROPIC_CUSTOM_HEADERS` for attribution headers. See [claude-code-proxy.md](../../docs/claude-code-proxy.md) for full setup on both Mac Mini and NAS.

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "http://192.168.1.70:30400",
    "ANTHROPIC_CUSTOM_HEADERS": "X-AgentWeave-Agent-Id: claude-code-mac\nX-AgentWeave-Session-Id: claude-code-main"
  }
}
```

## Security model

| Exposure | Auth | Notes |
|---|---|---|
| ClusterIP | Optional token | Firewall boundary = cluster network |
| NodePort (LAN) | Passthrough | Proxy forwards caller's auth headers to Anthropic untouched |
| Public ingress | **Not recommended** | Don't expose publicly |

The proxy runs in **passthrough mode** — it forwards the caller's `Authorization` / `x-api-key` headers to Anthropic unchanged. Each client's SDK handles its own authentication (OAuth or API key). Key injection only fires when the client sends no real key.

> **OAuth tokens (`sk-ant-oat*`) MUST NOT be stored in k8s secrets** for key injection. They expire and require the SDK-level auth flow. Only standard API keys (`sk-ant-api03-*`) should be used if key injection is needed.

## Update configmap

```bash
# Change OTLP endpoint
kubectl edit configmap agentweave-proxy -n agentweave
kubectl rollout restart deployment agentweave-proxy -n agentweave
```
