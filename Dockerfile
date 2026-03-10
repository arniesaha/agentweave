FROM python:3.11-slim

LABEL org.opencontainers.image.title="AgentWeave Proxy"
LABEL org.opencontainers.image.description="Transparent Anthropic API proxy with OTel tracing"
LABEL org.opencontainers.image.source="https://github.com/arniesaha/agentweave"
LABEL org.opencontainers.image.licenses="MIT"

WORKDIR /app

# Install dependencies (proxy extras)
COPY pyproject.toml README.md ./
COPY agentweave/ ./agentweave/
RUN pip install --no-cache-dir ".[proxy]"

# Non-root user
RUN useradd -m -u 1000 agentweave
USER agentweave

EXPOSE 4000

# Configuration via environment variables:
#   AGENTWEAVE_OTLP_ENDPOINT   — OTLP HTTP endpoint (required)
#   AGENTWEAVE_PROXY_TOKEN     — Bearer token for incoming auth (recommended)
#   AGENTWEAVE_LISTEN_PORT     — Port to listen on (default: 4000)
#                                NOTE: use AGENTWEAVE_LISTEN_PORT, not AGENTWEAVE_PROXY_PORT
#                                (k8s injects AGENTWEAVE_PROXY_PORT as a service discovery var)
#   AGENTWEAVE_AGENT_ID        — Default agent ID tag (optional)
#   AGENTWEAVE_CAPTURE_PROMPTS — Set to "1" to capture prompt previews (optional)

ENTRYPOINT ["sh", "-c", "\
  agentweave proxy start \
    --port ${AGENTWEAVE_LISTEN_PORT:-4000} \
    --endpoint ${AGENTWEAVE_OTLP_ENDPOINT:-http://localhost:4318} \
    ${AGENTWEAVE_AGENT_ID:+--agent-id $AGENTWEAVE_AGENT_ID} \
    ${AGENTWEAVE_CAPTURE_PROMPTS:+--capture-prompts} \
"]
