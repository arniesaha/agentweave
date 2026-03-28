# AgentWeave Proxy — Setup Guide

The AgentWeave proxy is a transparent HTTP server that sits between your agents and the Anthropic API. Every LLM call gets an OTel span — no SDK changes required.

```
Claude Code (Mac Mini) ──┐
Claude Code (NAS)     ──┤
OpenClaw/Nix          ──┤──→ agentweave-proxy :4000 ──→ api.anthropic.com
Max / pi-agent        ──┤         │
Nix A2A Server        ──┤    OTel spans → Tempo
Max A2A Server        ──┘
```

All clients share a single proxy instance (k8s NodePort 30400). Per-request
`X-AgentWeave-Agent-Id` headers handle attribution.

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

### OpenClaw / Nix (NAS)

Add to `~/.openclaw/openclaw.json`:

```json
{
  "models": {
    "providers": {
      "anthropic": {
        "baseUrl": "http://192.168.1.70:30400",
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

Then reload: `openclaw gateway restart`

### Claude Code (Mac Mini or NAS)

Add to `~/.claude/settings.json`:

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "http://192.168.1.70:30400",
    "ANTHROPIC_CUSTOM_HEADERS": "X-AgentWeave-Agent-Id: claude-code-mac\nX-AgentWeave-Session-Id: claude-code-main\nX-AgentWeave-Project: claude-code"
  }
}
```

New Claude Code sessions will route through the proxy. See [claude-code-proxy.md](claude-code-proxy.md) for full details.

### Max / pi-agent (Mac Mini)

Set in `~/max/projects/agent-max/.env`:

```bash
ANTHROPIC_BASE_URL=http://192.168.1.70:30400
```

The AgentWeave JS SDK handles per-request attribution headers automatically.
Restart: `launchctl stop com.arnab.agent-max && launchctl start com.arnab.agent-max`

### Any Python agent (Anthropic SDK)

```python
import anthropic

client = anthropic.Anthropic(
    base_url="http://localhost:4000",  # proxy
)
```

### Tag calls by agent

With the single-proxy architecture, the `X-AgentWeave-Agent-Id` header is how every client identifies itself. Add it to attribute spans to specific agents:

```python
client = anthropic.Anthropic(
    base_url="http://192.168.1.70:30400",
    default_headers={"X-AgentWeave-Agent-Id": "my-agent-v1"},
)
```

If no `X-AgentWeave-Agent-Id` header is sent, spans are attributed to `unattributed`.

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
