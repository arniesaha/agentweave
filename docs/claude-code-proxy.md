# Claude Code + AgentWeave Proxy

Route local Claude Code sessions through the AgentWeave proxy to get
LLM call tracing in Grafana alongside Max and Nix agent spans.

## Prerequisites

- `proxy-max` k8s service deployed on NAS (`192.168.1.70:30401`)
- Tempo OTLP endpoint reachable at `192.168.1.70:30418`

## Setup

Add the `env` block to `~/.claude/settings.json`:

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "http://192.168.1.70:30401",
    "ANTHROPIC_CUSTOM_HEADERS": "X-AgentWeave-Agent-Id: claude-code-mac"
  }
}
```

This routes all Claude Code API calls through the proxy with agent ID
`claude-code-mac`, which appears in Grafana spans as
`prov.agent.id = claude-code-mac`.

Changes take effect on **new** Claude Code sessions only.

## How it works

- `ANTHROPIC_BASE_URL` redirects API calls from `api.anthropic.com` to the proxy
- `ANTHROPIC_CUSTOM_HEADERS` sends `X-AgentWeave-Agent-Id: claude-code-mac`
  with every request so the proxy tags spans with the correct agent ID
- The proxy forwards requests to Anthropic, emits OTel spans to Tempo,
  and returns the response transparently

## Revert

If Claude Code stops working (proxy down, NAS unreachable, network issue),
remove the `env` block from `~/.claude/settings.json`:

```json
{
  "statusLine": {
    "type": "command",
    "command": "bash /Users/arnabmac/.claude/statusline-command.sh"
  }
}
```

Or override for a single session without editing the file:

```bash
ANTHROPIC_BASE_URL="" claude
```

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Claude Code hangs or errors on startup | Proxy unreachable | Revert settings (see above) |
| Spans show `agent.id = unknown` | Missing custom header | Ensure `ANTHROPIC_CUSTOM_HEADERS` is set |
| Spans show `agent.id = max-v1` not `claude-code-mac` | Header not forwarded | Check proxy version strips/reads `X-AgentWeave-Agent-Id` |
| Connection refused on `:30401` | proxy-max not deployed | Deploy `proxy-max.yaml` on NAS k8s first |

## Agent IDs in Grafana

| Agent ID | Source |
|----------|--------|
| `nix-v1` | Nix agent (NAS, NodePort 30400) |
| `max-v1` | Max agent (Mac Mini) |
| `claude-code-mac` | Claude Code sessions (Mac Mini) |
