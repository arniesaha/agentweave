#!/usr/bin/env bash
# Wrapper that launches Claude Code through AgentWeave with consistent,
# explicit per-session attribution headers. Use this for any DELEGATED
# Claude Code work (Nix → Claude Code, manual sub-tasks, dryruns) so model
# calls are correctly tagged in AgentWeave instead of inheriting the global
# settings.json defaults.
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
# Defaults that can be overridden via env:
#   AGENTWEAVE_PROXY_URL  (default: http://192.168.1.70:30400)
#   CLAUDE_BIN            (default: claude)
#
# Anything after `--` is forwarded to claude as-is.

set -euo pipefail

PROXY_URL="${AGENTWEAVE_PROXY_URL:-http://192.168.1.70:30400}"
CLAUDE_BIN="${CLAUDE_BIN:-claude}"

agent_id=""
session_id=""
parent=""
project=""
task_label=""

usage() {
  sed -n '2,22p' "$0" >&2
  exit 2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --agent-id)   agent_id="$2";   shift 2 ;;
    --session-id) session_id="$2"; shift 2 ;;
    --parent)     parent="$2";     shift 2 ;;
    --project)    project="$2";    shift 2 ;;
    --task)       task_label="$2"; shift 2 ;;
    --proxy-url)  PROXY_URL="$2";  shift 2 ;;
    -h|--help)    usage ;;
    --)           shift; break ;;
    *)            echo "claude-delegate: unknown arg: $1" >&2; usage ;;
  esac
done

if [[ -z "$agent_id" || -z "$session_id" ]]; then
  echo "claude-delegate: --agent-id and --session-id are required" >&2
  usage
fi

# Build the multiline ANTHROPIC_CUSTOM_HEADERS block. claude reads this and
# emits each line as an HTTP header on every Anthropic API request — that
# is the only knob the Claude Code CLI exposes for per-process attribution.
headers="X-AgentWeave-Agent-Id: ${agent_id}
X-AgentWeave-Session-Id: ${session_id}"
[[ -n "$parent"     ]] && headers+=$'\n'"X-AgentWeave-Parent-Session-Id: ${parent}"
[[ -n "$project"    ]] && headers+=$'\n'"X-AgentWeave-Project: ${project}"
[[ -n "$task_label" ]] && headers+=$'\n'"X-AgentWeave-Task-Label: ${task_label}"

ANTHROPIC_BASE_URL="$PROXY_URL" \
ANTHROPIC_CUSTOM_HEADERS="$headers" \
  exec "$CLAUDE_BIN" "$@"
