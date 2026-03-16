# AgentWeave Proxy — Kubernetes Deployment

## Architecture

```
LAN agents (Mac Mini/pi-mono)
        │
        ▼ NodePort :30400 (Bearer token auth)
┌───────────────────────────────┐
│  agentweave-proxy pod         │
│  namespace: agentweave        │
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

### LAN agents — Mac Mini / pi-mono (NodePort)

```bash
# In your agent env or pi-mono config:
ANTHROPIC_BASE_URL=http://192.168.1.70:30400

# Auth token required — set the same token from secret.yaml
AGENTWEAVE_PROXY_TOKEN=<your-token>
```

For the Anthropic SDK, pass the token as a custom header (NOT as the API key):

```python
import anthropic

client = anthropic.Anthropic(
    base_url="http://192.168.1.70:30400",
    default_headers={
        "Authorization": f"Bearer {proxy_token}",
        "X-AgentWeave-Agent-Id": "max-v1",
    },
)
```

### Claude Code

```bash
export ANTHROPIC_BASE_URL=http://192.168.1.70:30400
# Claude Code doesn't natively support custom auth headers to the base URL,
# so for Claude Code keep it on localhost (ClusterIP via port-forward or local service).
kubectl port-forward -n agentweave svc/agentweave-proxy 4000:4000
export ANTHROPIC_BASE_URL=http://localhost:4000
```

## Security model

| Exposure | Auth | Notes |
|---|---|---|
| ClusterIP | Optional token | Firewall boundary = cluster network |
| NodePort (LAN) | **Required token** | Protects your Anthropic API key |
| Public ingress | **Not recommended** | Don't expose publicly |

The proxy never stores API keys — it forwards `x-api-key` from the calling agent to Anthropic unchanged. The `Authorization: Bearer` header is the proxy access token only and is stripped before forwarding.

## Sub-agent Proxy (Nix)

Nix (the NAS Claude Code agent) uses two proxy instances to separate main session traces from sub-agent traces:

| Proxy | NodePort | AGENTWEAVE_AGENT_ID | Purpose |
|---|---|---|---|
| agentweave-proxy-nodeport | 30400 | nix-v1 | Main session |
| agentweave-proxy-nix-subagent-nodeport | 30402 | nix-subagent-v1 | Sub-agents (worktree agents) |

When spawning Claude Code sub-agents, set:

```bash
export ANTHROPIC_BASE_URL=http://192.168.1.70:30402/v1
```

This ensures sub-agent traces are tagged with `nix-subagent-v1` and can be filtered separately in the dashboard.

### Deploy sub-agent proxy

```bash
kubectl apply -f deploy/k8s/nix-subagent-proxy-configmap.yaml
kubectl apply -f deploy/k8s/nix-subagent-proxy-deployment.yaml
kubectl apply -f deploy/k8s/nix-subagent-proxy-service.yaml
```

## Update configmap

```bash
# Change OTLP endpoint
kubectl edit configmap agentweave-proxy -n agentweave
kubectl rollout restart deployment agentweave-proxy -n agentweave
```
