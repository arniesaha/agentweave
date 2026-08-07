#!/bin/bash
# AgentWeave Claude Code hook: Stop
# Batch-exports all buffered tool use events when the session ends.
# Uses atomic rename to avoid race conditions with concurrent sessions.

PROXY="${AGENTWEAVE_PROXY_URL:-http://localhost:4000}"
SID="${CLAUDE_SESSION_ID:-default}"
BUFFER="${AGENTWEAVE_HOOKS_BUFFER:-$HOME/.agentweave/hooks_buffer_${SID}.jsonl}"
EXPORT_FILE="${BUFFER}.exporting"

if [ -f "$BUFFER" ]; then
  # Atomic rename prevents race with PostToolUse appending during export
  mv "$BUFFER" "$EXPORT_FILE" 2>/dev/null || exit 0
  EVENTS=$(cat "$EXPORT_FILE")
  if [ -n "$EVENTS" ]; then
    # Read traceparent from env if the agentweave-bridge plugin set it
    TP="${AGENTWEAVE_TRACEPARENT:-}"
    # Agent identity so hook spans are attributable and joinable (#247).
    # Empty values are dropped proxy-side rather than written as empty attrs.
    AID="${AGENTWEAVE_AGENT_ID:-}"
    ATYPE="${AGENTWEAVE_AGENT_TYPE:-}"
    PROJ="${AGENTWEAVE_PROJECT:-}"
    curl -s -X POST "$PROXY/hooks/batch" \
      -H "Content-Type: application/json" \
      -d "{\"session_id\":\"${SID}\",\"traceparent\":\"${TP}\",\"agent_id\":\"${AID}\",\"agent_type\":\"${ATYPE}\",\"project\":\"${PROJ}\",\"cwd\":\"${PWD}\",\"events\":$(echo "$EVENTS" | jq -s .)}"
  fi
  rm -f "$EXPORT_FILE"
fi
