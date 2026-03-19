# AgentWeave Proxy — Setup Guide

The AgentWeave proxy is a transparent HTTP server that sits between your agents and the Anthropic API. Every LLM call gets an OTel span — no SDK changes required.

```
Claude Code  ─┐
OpenClaw/Nix ─┤──→ agentweave-proxy :4000 ──→ api.anthropic.com
Max / pi-mono─┘         │
                   OTel spans → Tempo / Langfuse
```

## Prerequisites

```bash
pip install "agentweave[proxy]"
```

## Start the proxy

```bash
agentweave proxy start \
  --port 4000 \
  --endpoint http://localhost:4318 \   # your OTLP backend
  --agent-id my-agent-v1
```

Options:
| Flag | Default | Description |
|---|---|---|
| `--port` | `4000` | Port to listen on |
| `--endpoint` | `http://localhost:4318` | OTLP HTTP endpoint |
| `--agent-id` | *(none)* | Default agent ID for all spans |
| `--capture-prompts` | `false` | Record prompt/response previews in spans |

## Run as a systemd service (Linux)

```ini
# ~/.config/systemd/user/agentweave-proxy.service
[Unit]
Description=AgentWeave Anthropic API Proxy
After=network-online.target

[Service]
ExecStart=agentweave proxy start --port 4000 --endpoint http://your-tempo:4318 --agent-id nix-v1
Restart=always
RestartSec=5
Environment=HOME=/home/youruser
Environment=PATH=/home/youruser/.local/bin:/usr/local/bin:/usr/bin:/bin

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now agentweave-proxy
```

## Wire up your agents

### OpenClaw / Nix

Add to `~/.openclaw/openclaw.json`:

```json
{
  "models": {
    "providers": {
      "anthropic": {
        "baseUrl": "http://localhost:4000",
        "headers": {
          "X-AgentWeave-Agent-Id": "nix-v1",
          "X-AgentWeave-Agent-Type": "main"
        },
        "models": []
      }
    }
  }
}
```

Then reload OpenClaw config.

### Claude Code

```bash
export ANTHROPIC_BASE_URL=http://localhost:4000
claude  # all calls now traced
```

### Max / pi-mono (remote agent)

Point at the NAS proxy from the Mac Mini:

```bash
export ANTHROPIC_BASE_URL=http://192.168.1.70:4000
```

Or set in pi-mono's environment config to persist across restarts.

### Any Python agent (Anthropic SDK)

```python
import anthropic

client = anthropic.Anthropic(
    base_url="http://localhost:4000",  # proxy
)
```

### Tag calls by agent

Add the `X-AgentWeave-Agent-Id` header to attribute spans to specific agents when multiple agents share the same proxy:

```python
client = anthropic.Anthropic(
    base_url="http://localhost:4000",
    default_headers={"X-AgentWeave-Agent-Id": "max-v1"},
)
```

## What each span captures

```
llm.claude-sonnet-4-6
  prov.activity.type      = llm_call
  prov.llm.provider       = anthropic
  prov.llm.model          = claude-sonnet-4-6
  prov.llm.prompt_tokens  = 4821
  prov.llm.completion_tokens = 312
  prov.llm.total_tokens   = 5133
  prov.llm.stop_reason    = end_turn
  prov.agent.id           = nix-v1
  prov.agent.type         = main
  agentweave.latency_ms   = 4348
  http.route              = /v1/messages
```

## Capture prompts (opt-in)

By default, prompt content is NOT captured. Enable with:

```bash
AGENTWEAVE_CAPTURE_PROMPTS=1 agentweave proxy start --port 4000 ...
```

This adds `prov.llm.prompt_preview` (first 512 chars) and `prov.llm.response_preview` to each span.

## Backends

The proxy emits standard OTLP HTTP — works with any compatible backend:

| Backend | Endpoint |
|---|---|
| Grafana Tempo | `http://tempo-host:4318` |
| Jaeger | `http://jaeger-host:4318` |
| Langfuse v3 | `https://cloud.langfuse.com/api/public/otel` |
| Any OTel Collector | Collector's OTLP HTTP receiver |
