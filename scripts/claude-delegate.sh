#!/usr/bin/env bash
# Wrapper that launches Claude Code through AgentWeave with consistent,
# explicit per-session attribution headers. Use this for any DELEGATED
# Claude Code work (Nix → Claude Code, manual sub-tasks, dryruns) so model
# calls are correctly tagged in AgentWeave instead of inheriting the global
# settings.json defaults.
#
# Why this exists at all: Claude Code's settings.json `env` block is
# applied AFTER process-env on startup, so an interactive
# `ANTHROPIC_CUSTOM_HEADERS=... claude ...` invocation silently loses to
# whatever is in `~/.claude/settings.json`. This wrapper sidesteps that by
# running Claude with a private HOME directory that contains a minimal
# settings.json (just our env block + skipAutoPermissionPrompt) and
# symlinks to the real auth/skills/plugins/agents so Claude still works.
#
# Usage:
#   scripts/claude-delegate.sh \
#     --agent-id   claude-code-nas-subagent \
#     --session-id "claude-code-mux-67-$(date +%Y%m%d-%H%M%S)" \
#     --parent     nix-main \
#     --project    mux \
#     --task       "mux issue 67 openclaw routing" \
#     -- --dangerously-skip-permissions --model claude-sonnet-4-6 --print "<task>"
#
# Defaults overridable via env:
#   AGENTWEAVE_PROXY_URL  (default: http://192.168.1.70:30400)
#   CLAUDE_BIN            (default: claude on PATH, then native install)
#   CLAUDE_REAL_HOME      (default: $HOME, then /home/Arnab if needed)
#
# Anything after `--` is forwarded to claude as-is.

set -euo pipefail

PROXY_URL="${AGENTWEAVE_PROXY_URL:-http://192.168.1.70:30400}"
REAL_HOME="${CLAUDE_REAL_HOME:-$HOME}"

if [[ -z "${CLAUDE_REAL_HOME:-}" && -e /home/Arnab/.claude.json && -e /home/Arnab/.claude/.credentials.json ]]; then
  REAL_HOME="/home/Arnab"
fi

if [[ -n "${CLAUDE_BIN:-}" ]]; then
  CLAUDE_CMD="$CLAUDE_BIN"
elif command -v claude >/dev/null 2>&1; then
  CLAUDE_CMD="claude"
elif [[ -d "$REAL_HOME/.local/share/claude/versions" ]]; then
  CLAUDE_CMD="$(ls -1t "$REAL_HOME/.local/share/claude/versions"/* 2>/dev/null | head -1 || true)"
elif [[ -d /home/Arnab/.local/share/claude/versions ]]; then
  CLAUDE_CMD="$(ls -1t /home/Arnab/.local/share/claude/versions/* 2>/dev/null | head -1 || true)"
else
  CLAUDE_CMD=""
fi

if [[ -z "$CLAUDE_CMD" || ! -x "$CLAUDE_CMD" ]]; then
  echo "claude-delegate: Claude Code binary not found; set CLAUDE_BIN" >&2
  exit 2
fi

agent_id=""
session_id=""
parent=""
project=""
task_label=""
keep_temp_home=0

usage() {
  sed -n '2,30p' "$0" >&2
  exit 2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --agent-id)        agent_id="$2";   shift 2 ;;
    --session-id)      session_id="$2"; shift 2 ;;
    --parent)          parent="$2";     shift 2 ;;
    --project)         project="$2";    shift 2 ;;
    --task)            task_label="$2"; shift 2 ;;
    --proxy-url)       PROXY_URL="$2";  shift 2 ;;
    --keep-temp-home)  keep_temp_home=1; shift ;;
    -h|--help)         usage ;;
    --)                shift; break ;;
    *)                 echo "claude-delegate: unknown arg: $1" >&2; usage ;;
  esac
done

if [[ -z "$agent_id" || -z "$session_id" ]]; then
  echo "claude-delegate: --agent-id and --session-id are required" >&2
  usage
fi

# Build the multiline ANTHROPIC_CUSTOM_HEADERS block.
headers="X-AgentWeave-Agent-Id: ${agent_id}
X-AgentWeave-Session-Id: ${session_id}"
[[ -n "$parent"     ]] && headers+=$'\n'"X-AgentWeave-Parent-Session-Id: ${parent}"
[[ -n "$project"    ]] && headers+=$'\n'"X-AgentWeave-Project: ${project}"
[[ -n "$task_label" ]] && headers+=$'\n'"X-AgentWeave-Task-Label: ${task_label}"

# Private HOME: contains only what Claude needs (auth + skills + plugins
# + agents + commands + the cache for OAuth refresh tokens) plus a fresh
# settings.json that sets our env block. This prevents the real
# ~/.claude/settings.json from clobbering the headers via its own env block.
TEMP_HOME="$(mktemp -d -t claude-delegate.XXXXXXXX)"
mkdir -p "$TEMP_HOME/.claude"

if [[ "$keep_temp_home" -ne 1 ]]; then
  trap 'rm -rf "$TEMP_HOME"' EXIT
fi

# Symlink everything needed for Claude to authenticate + behave normally.
# Whitelist rather than blanket-symlink so the real settings.json doesn't
# leak in through any indirect path.
for entry in .credentials.json .claude.json agents skills plugins commands mcp-needs-auth-cache.json statusline-command.sh hooks projects todos shell-snapshots; do
  src="$REAL_HOME/.claude/$entry"
  if [[ -e "$src" || -L "$src" ]]; then
    ln -s "$src" "$TEMP_HOME/.claude/$entry"
  fi
done

# Claude Code keeps the main account/session metadata at ~/.claude.json
# outside ~/.claude on native installs. Without this, the private HOME
# starts unauthenticated even when the real profile is logged in.
if [[ -e "$REAL_HOME/.claude.json" || -L "$REAL_HOME/.claude.json" ]]; then
  ln -s "$REAL_HOME/.claude.json" "$TEMP_HOME/.claude.json"
fi

# Minimal settings.json with our env block.
cat > "$TEMP_HOME/.claude/settings.json" <<JSON
{
  "env": {
    "ANTHROPIC_BASE_URL": "${PROXY_URL}",
    "ANTHROPIC_CUSTOM_HEADERS": $(printf '%s' "$headers" | jq -Rs .),
    "AGENTWEAVE_PROXY_URL": "${PROXY_URL}"
  },
  "skipAutoPermissionPrompt": true
}
JSON

ANTHROPIC_BASE_URL="$PROXY_URL" \
ANTHROPIC_CUSTOM_HEADERS="$headers" \
AGENTWEAVE_PROXY_URL="$PROXY_URL" \
HOME="$TEMP_HOME" \
  exec "$CLAUDE_CMD" "$@"
