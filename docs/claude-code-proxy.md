# Claude Code + AgentWeave Proxy

Route Claude Code sessions through the AgentWeave proxy to get
LLM call tracing in the AgentWeave dashboard alongside all other agent spans.

## Prerequisites

- Single `agentweave-proxy` k8s service deployed on NAS (`192.168.1.70:30400`)
- Tempo OTLP endpoint reachable at `192.168.1.70:30418`

## Setup — Mac Mini (192.168.1.149)

Add the `env` block to `~/.claude/settings.json`:

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "http://192.168.1.70:30400",
    "ANTHROPIC_CUSTOM_HEADERS": "X-AgentWeave-Agent-Id: claude-code-mac\nX-AgentWeave-Session-Id: claude-code-main\nX-AgentWeave-Project: claude-code"
  }
}
```

The Mac Mini setup also uses hooks (SubagentStop, Stop, PostToolUse) for session tracking — these are configured separately in `settings.json`.

## Setup — NAS (192.168.1.70)

Add the `env` block to `~/.claude/settings.json`:

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "http://192.168.1.70:30400",
    "ANTHROPIC_CUSTOM_HEADERS": "X-AgentWeave-Agent-Id: claude-code-nas\nX-AgentWeave-Session-Id: claude-code-main\nX-AgentWeave-Project: claude-code"
  }
}
```

## How attribution works

All Claude Code instances share the single proxy on port 30400. Attribution is done via per-request headers:

- `X-AgentWeave-Agent-Id` — identifies the Claude Code instance (e.g., `claude-code-mac`, `claude-code-nas`)
- `X-AgentWeave-Session-Id` — groups spans into a session
- `X-AgentWeave-Project` — project-level grouping

Multiple headers are `\n`-separated in the JSON string.

Changes take effect on **new** Claude Code sessions only.

## How it works

- `ANTHROPIC_BASE_URL` redirects API calls from `api.anthropic.com` to the proxy
- `ANTHROPIC_CUSTOM_HEADERS` sends `X-AgentWeave-Agent-Id`, `X-AgentWeave-Session-Id`,
  and `X-AgentWeave-Project` with every request so the proxy tags spans correctly
- The proxy forwards requests to Anthropic, emits OTel spans to Tempo,
  and returns the response transparently
- The proxy is pass-through — OAuth tokens from the Claude Code SDK pass through untouched
- `AGENTWEAVE_CAPTURE_PROMPTS=1` is enabled, so prompt/response previews are captured

## Rollback — Mac Mini

If Claude Code stops working (proxy down, NAS unreachable, network issue):

```bash
# SSH to Mac Mini:
ssh arnabmac@arnabs-mac-mini.local

# Edit ~/.claude/settings.json:
#   Option A: Remove the "env" block entirely
#   Option B: Set "ANTHROPIC_BASE_URL": "https://api.anthropic.com" and remove ANTHROPIC_CUSTOM_HEADERS

# Next Claude Code session will use direct Anthropic API.
```

Quick override for a single session (no file edit needed):

```bash
ANTHROPIC_BASE_URL="" claude
```

**Verify:** Start a new Claude Code session and confirm it responds. The session will NOT appear in the AgentWeave dashboard (proxy is bypassed).

**Restore:** Re-add the `env` block with the proxy URL and custom headers.

## Rollback — NAS

```bash
# On the NAS (192.168.1.70):
# Edit ~/.claude/settings.json — remove the "env" block.
# Next Claude Code session will use direct Anthropic API.
```

Quick override:

```bash
ANTHROPIC_BASE_URL="" claude
```

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Claude Code hangs or errors on startup | Proxy unreachable | Revert settings (see Rollback above) |
| Spans show `agent.id = unknown` | Missing custom headers | Ensure `ANTHROPIC_CUSTOM_HEADERS` is set with all three headers |
| Spans show wrong `agent.id` | Header not forwarded | Check proxy version reads `X-AgentWeave-Agent-Id` from request |
| Connection refused on `:30400` | Proxy pod down | Check `kubectl get pods -n agentweave -l app=agentweave-proxy` |

## Agent IDs in AgentWeave Dashboard

All agents share the single proxy on port 30400. Attribution is via per-request headers.

| Agent ID | Source |
|----------|--------|
| `nix-v1` | Nix/OpenClaw agent (NAS) |
| `max-v1` | Max/pi-agent (Mac Mini) |
| `claude-code-mac` | Claude Code sessions (Mac Mini) |
| `claude-code-nas` | Claude Code sessions (NAS) |
| `unattributed` | Fallback when no `X-AgentWeave-Agent-Id` header is sent |
