# Cross-SDK Compatibility Matrix

This table shows which proxy versions are compatible with which SDK versions.

## Current State

| Component | Version | Notes |
|-----------|---------|-------|
| Proxy | `0.3.0` | Supports `/session` endpoint, sub-agent attribution headers |
| Python SDK | `0.3.0` | `@trace_tool`, `@trace_agent`, `@trace_llm`, `W3C` propagation |
| TypeScript SDK | `0.3.0` | Same decorator API as Python SDK |
| Go SDK | `0.1.0` | `TraceTool`, `TraceAgent`, `TraceLlm` |

---

## Compatibility Table

The proxy and SDKs communicate via:
1. **HTTP headers** — `X-AgentWeave-*` headers from SDK to proxy
2. **OTLP spans** — proxy emits to Tempo; SDKs optionally emit directly
3. **W3C TraceContext** — `traceparent` header for distributed tracing

| Proxy Version | Python SDK | TypeScript SDK | Go SDK | Notes |
|---------------|-----------|----------------|--------|-------|
| `0.3.x` | `0.3.x` | `0.3.x` | `0.1.x` | Current. `/session` endpoint available |
| `0.2.x` | `0.2.x` | `0.2.x` | `0.1.x` | `/session` endpoint available |
| `0.1.x` | `0.1.x` | `0.1.x` | `0.1.x` | No `/session` endpoint, no sub-agent attribution |

### Minimum Compatible Versions

To use sub-agent session attribution (PR #90):
- Proxy `>= 0.2.0`
- Any SDK version (headers are passed through transparently)

To use parent session attribution headers (`X-AgentWeave-Parent-Session-Id`):
- Proxy `>= 0.2.0` (added in PR #81)

---

## The Stable Interface

The proxy HTTP API is the compatibility boundary between the proxy and SDKs:

```
GET  /health          → {"status": "ok", "version": "x.y.z"}
POST /session         → set session context for span attribution
GET  /session         → return current session context
POST /v1/messages     → Anthropic pass-through
POST /v1/chat/completions → OpenAI pass-through
POST /v1beta/models/* → Google Gemini pass-through
```

SDKs do not call these endpoints directly — they set environment variables that point agents at the proxy. The proxy intercepts the standard provider API calls transparently.

---

## Pre-1.0 Compatibility Note

Until `1.0.0`, any version bump may introduce breaking changes. Pin to an exact proxy version in production:

```yaml
# deploy/k8s/deployment.yaml
image: localhost:5000/agentweave-proxy:0.3.0  # pin exact version
```

After `1.0.0`, minor versions will be backward-compatible within a major version.
