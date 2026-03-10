# AgentWeave Proxy — Multi-Provider Observability Gateway

The **AgentWeave proxy** enables transparent tracing for agents that interact with multiple LLM providers. It acts as a drop-in middleware to:
- Forward requests upstream (e.g., Anthropic, Google Gemini)
- Capture trace metadata (tokens, latency, provenance)
- Emit OpenTelemetry (OTLP) spans for visualization and debugging

## Docker Quickstart

To deploy the proxy via Docker:

```bash
docker run -e AGENTWEAVE_OTLP_ENDPOINT=http://tempo:4318 \
           -e AGENTWEAVE_PROXY_TOKEN=your-proxy-token \
           -p 4000:4000 \
           ghcr.io/arniesaha/agentweave:latest
```

### Environment Variables

| Variable                  | Description                                   |
|---------------------------|-----------------------------------------------|
| `AGENTWEAVE_LISTEN_PORT`  | Proxy listening port (default: `4000`)       |
| `AGENTWEAVE_OTLP_ENDPOINT`| OTLP HTTP endpoint for traces                |
| `AGENTWEAVE_PROXY_TOKEN`  | Token for securing proxy access              |
| `AGENTWEAVE_AGENT_ID`     | Default agent ID for traces                  |
| `AGENTWEAVE_CAPTURE_PROMPTS` | Enable prompt and response capture (default: `false`) |

## Kubernetes Deployment

Kubernetes manifests for deploying the AgentWeave Proxy can be found in the [deploy/k8s](../deploy/k8s) directory. Update the manifests as needed for your environment: