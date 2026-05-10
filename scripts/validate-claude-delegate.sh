#!/usr/bin/env bash
# End-to-end validation for scripts/claude-delegate.sh.
#
# Runs a short Claude Code dry-call through the wrapper with a unique
# session id, then queries Tempo for a span carrying that exact
# prov.session.id. Fails the script if Tempo never sees the requested
# session id within the timeout, or if the most recent matching span
# instead shows the legacy nix-v1 / claude-code-nas-main attribution.
#
# Usage:
#   scripts/validate-claude-delegate.sh [--prompt "..."] [--model claude-sonnet-4-6]
#
# Env:
#   AGENTWEAVE_PROXY_URL  default: http://192.168.1.70:30400
#   TEMPO_API_URL         default: http://192.168.1.70:31989
#   CLAUDE_BIN            optional; otherwise wrapper autodetects
#   VALIDATE_TIMEOUT      default: 60 (seconds to wait for Tempo to index)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROXY_URL="${AGENTWEAVE_PROXY_URL:-http://192.168.1.70:30400}"
TEMPO_URL="${TEMPO_API_URL:-http://192.168.1.70:31989}"
TIMEOUT="${VALIDATE_TIMEOUT:-60}"

PROMPT="say only the word OK"
MODEL="claude-sonnet-4-6"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prompt) PROMPT="$2"; shift 2 ;;
    --model)  MODEL="$2"; shift 2 ;;
    -h|--help)
      sed -n '2,18p' "$0" >&2; exit 2 ;;
    *) echo "validate-claude-delegate: unknown arg: $1" >&2; exit 2 ;;
  esac
done

ts() { printf "[%s] %s\n" "$(date '+%H:%M:%S')" "$*"; }
fail() { ts "FAIL: $*" >&2; exit 1; }

SESSION_ID="claude-code-validate-$(date +%Y%m%d-%H%M%S)-$$"
AGENT_ID="claude-code-nas-subagent"
PROJECT="agentweave"
PARENT="nix-validate"
TASK="validate-claude-delegate dry run"

ts "session_id=${SESSION_ID}"
ts "running claude via wrapper"

# Run the wrapper inline so CLAUDE_BIN / CLAUDE_REAL_HOME are inherited
# when set, and otherwise the wrapper's autodetection picks up `claude`
# from PATH. Calling through `env` with an empty arg list misbehaves on
# systems whose user-level `env` is a wrapper script (e.g.
# ~/.local/bin/env that re-exports PATH) — it can swallow the command.
"$REPO_ROOT/scripts/claude-delegate.sh" \
  --agent-id "$AGENT_ID" \
  --session-id "$SESSION_ID" \
  --parent "$PARENT" \
  --project "$PROJECT" \
  --task "$TASK" \
  --proxy-url "$PROXY_URL" \
  -- --print --model "$MODEL" "$PROMPT" \
  > /tmp/validate-claude-delegate.out 2>&1 \
  || fail "claude exited non-zero: $(head -c 500 /tmp/validate-claude-delegate.out)"

ts "claude returned: $(head -c 200 /tmp/validate-claude-delegate.out | tr -d '\n')"

# Tempo indexes traces with a few-second delay. Poll until we see the
# requested session id or hit the timeout.
ts "polling Tempo for prov.session.id=${SESSION_ID}"
deadline=$(( $(date +%s) + TIMEOUT ))
trace_json=""
while [[ $(date +%s) -lt $deadline ]]; do
  trace_json=$(curl -s "${TEMPO_URL}/api/search" --get \
    --data-urlencode "q={resource.service.name=\"agentweave-proxy\" && .prov.session.id=\"${SESSION_ID}\"}" \
    --data-urlencode "limit=5" 2>/dev/null || true)
  match_count=$(printf '%s' "$trace_json" | jq -r '.traces | length // 0' 2>/dev/null || echo 0)
  if [[ "$match_count" -gt 0 ]]; then
    break
  fi
  sleep 3
done

match_count=$(printf '%s' "$trace_json" | jq -r '.traces | length // 0' 2>/dev/null || echo 0)
if [[ "$match_count" -eq 0 ]]; then
  fail "Tempo never indexed a span with prov.session.id=${SESSION_ID} within ${TIMEOUT}s — wrapper attribution did NOT reach the proxy"
fi

# Pull the first matching span's full attribute set to confirm attribution.
trace_id=$(printf '%s' "$trace_json" | jq -r '.traces[0].traceID')
ts "found trace ${trace_id}; fetching span attrs"
span_json=$(curl -s "${TEMPO_URL}/api/traces/${trace_id}" 2>/dev/null || true)
attrs=$(printf '%s' "$span_json" | jq -r '
  [ .batches[]?.scopeSpans[]?.spans[]?
      | select(.attributes? != null)
      | (.attributes[] | {key,value:(.value.stringValue // .value.intValue // .value.boolValue // null)})
  ] | from_entries
' 2>/dev/null || echo '{}')

want() {
  local key="$1" want="$2"
  local got
  got=$(printf '%s' "$attrs" | jq -r --arg k "$key" '.[$k] // empty')
  if [[ "$got" != "$want" ]]; then
    fail "$key expected=${want} got=${got:-<missing>}"
  fi
  ts "  OK ${key}=${got}"
}

want "prov.agent.id"          "$AGENT_ID"
want "prov.session.id"        "$SESSION_ID"
want "prov.parent.session.id" "$PARENT"
want "prov.project"           "$PROJECT"
want "prov.task.label"        "$TASK"

ts "PASS — delegated attribution propagated end-to-end"
