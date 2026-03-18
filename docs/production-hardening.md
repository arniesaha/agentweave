# Production Hardening Guide

This guide covers security, reliability, observability, scaling, and upgrade practices for deploying the AgentWeave proxy in production.

---

## Security

### API Keys
- **Never commit API keys** to source control. Keys are forwarded from the calling agent via `x-api-key` (Anthropic) or `Authorization` (OpenAI/Google) headers — the proxy never stores them.
- Store provider API keys only in your agent's environment, not in proxy config.
- The proxy's own access token (`AGENTWEAVE_PROXY_TOKEN`) should be stored in a Kubernetes Secret, not a ConfigMap:

```bash
kubectl create secret generic agentweave-proxy-token \
  --from-literal=token=$(openssl rand -hex 32) \
  -n agentweave
```

### Rotate the Proxy Token
The `AGENTWEAVE_PROXY_TOKEN` authenticates calls to the proxy. Rotate it periodically:
1. Generate a new token: `openssl rand -hex 32`
2. Update the k8s Secret: `kubectl create secret generic agentweave-proxy-token --from-literal=token=<new> -n agentweave --dry-run=client -o yaml | kubectl apply -f -`
3. Restart the proxy deployment: `kubectl rollout restart deployment/agentweave-proxy -n agentweave`
4. Update `AGENTWEAVE_PROXY_TOKEN` in all calling agents

### Network Policy
Restrict which pods can reach the proxy:

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: agentweave-proxy-ingress
  namespace: agentweave
spec:
  podSelector:
    matchLabels:
      app: agentweave-proxy
  ingress:
  - from:
    - namespaceSelector:
        matchLabels:
          agentweave-client: "true"
```

Label agent namespaces with `agentweave-client: "true"` to grant access.

### TLS
For external access, terminate TLS at the ingress layer. The proxy itself speaks plain HTTP — wrap it with an nginx ingress or Cloudflare tunnel (as used in the reference deployment).

---

## Reliability

### Liveness and Readiness Probes
The proxy k8s manifests include probes on `GET /health`:

```yaml
livenessProbe:
  httpGet:
    path: /health
    port: 4000
  initialDelaySeconds: 5
  periodSeconds: 15

readinessProbe:
  httpGet:
    path: /health
    port: 4000
  initialDelaySeconds: 3
  periodSeconds: 10
```

`/health` returns `{"status": "ok", "version": "0.2.0"}`. The probe ensures the pod is replaced if FastAPI crashes.

### When Tempo is Unavailable
The proxy is designed to degrade gracefully:
- All LLM requests are still **forwarded and responded to** normally
- Spans that cannot be exported are **dropped silently** (not buffered indefinitely)
- The proxy does not block or fail requests due to telemetry export errors
- Agent workloads continue uninterrupted; you simply lose observability for that window

To detect Tempo outages, alert on missing span ingestion in Grafana rather than proxy errors.

### Resource Limits
Recommended pod resource requests/limits for the proxy:

```yaml
resources:
  requests:
    cpu: 50m
    memory: 128Mi
  limits:
    cpu: 500m
    memory: 256Mi
```

The proxy is lightweight — it streams bytes between caller and provider with minimal processing.

---

## Observability

### Verify Spans Are Landing in Tempo
Run this TraceQL query in Grafana Explore (Tempo datasource):

```
{ resource.service.name = "agentweave-proxy" } | count() > 0
```

Or check the AgentWeave dashboard at your deployed URL — the "Total LLM Calls" stat card reflects span count.

### Key Metrics to Alert On

| Metric | Query | Alert Threshold |
|--------|-------|-----------------|
| LLM call rate | `rate(traces_spanmetrics_calls_total[5m])` | Drop to 0 (agents stopped) |
| Error rate | `rate(traces_spanmetrics_calls_total{status_code="ERROR"}[5m])` | > 5% |
| P95 latency | `histogram_quantile(0.95, traces_spanmetrics_duration_seconds_bucket)` | > 30s |
| Span export errors | Check proxy pod logs for `Failed to export` | Any |

### Dashboard Health Check
The AgentWeave dashboard (`/`) loads panels from Tempo and Prometheus. If panels show "No data":
1. Check Tempo is running: `kubectl get pods -n monitoring`
2. Verify the OTLP endpoint is reachable from the proxy pod
3. Check proxy logs: `kubectl logs -n agentweave deploy/agentweave-proxy --tail=50`

---

## Scaling

### One Proxy Per Agent Identity
Each proxy instance has a fixed `AGENTWEAVE_AGENT_ID` (e.g., `nix-v1`, `max-v1`). This is intentional — it ties all spans from that agent to a stable identity in traces.

Do **not** share a proxy instance across different agent identities. Deploy separate proxies (as separate Deployments) for each agent.

### Horizontal Scaling
The proxy is stateless — session context (`POST /session`) is held in memory per-instance. For single-replica deployments (current default), this is fine.

For multi-replica deployments, session context would need to be externalized (Redis or similar). At current scale (1-2 agents), single-replica is recommended.

### When to Add a Second Proxy
- You have a new agent with a different identity
- You want cost/call attribution separated (e.g., main session vs sub-agents)
- See `deploy/k8s/nix-subagent-proxy-deployment.yaml` for the sub-agent proxy pattern

---

## Upgrades

### Zero-Downtime Rollout
```bash
# 1. Build new image
cd /path/to/agentweave
docker build -t localhost:5000/agentweave-proxy:latest -f deploy/docker/Dockerfile .

# 2. Push to registry
docker push localhost:5000/agentweave-proxy:latest

# 3. Restart deployment (rolling update)
kubectl rollout restart deployment/agentweave-proxy -n agentweave

# 4. Monitor rollout
kubectl rollout status deployment/agentweave-proxy -n agentweave
```

The default `imagePullPolicy: Always` ensures the new image is pulled on restart.

### Check Current Deployed Version
```bash
curl -s http://<proxy-host>:<port>/health | jq .version
# → {"status": "ok", "version": "0.2.0"}
```

Or from inside the cluster:
```bash
kubectl exec -n agentweave deploy/agentweave-proxy -- \
  curl -s localhost:4000/health
```

---

## Production Readiness Checklist

- [ ] `AGENTWEAVE_PROXY_TOKEN` stored in k8s Secret (not ConfigMap or env literal)
- [ ] Provider API keys only in agent environment, never in proxy config
- [ ] Network policy restricts proxy access to authorised agent namespaces
- [ ] TLS terminated at ingress/tunnel layer
- [ ] Liveness and readiness probes configured (included in `deployment.yaml`)
- [ ] Resource requests and limits set on proxy pod
- [ ] Tempo/OTLP endpoint reachable from proxy pod
- [ ] Grafana dashboard showing live data (`/health` returns 200)
- [ ] Alert configured for span drop-to-zero
- [ ] One proxy deployment per agent identity
- [ ] `imagePullPolicy: Always` set (default in manifests)
- [ ] Proxy token rotation process documented for your team
