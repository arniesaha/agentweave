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
    curl -s -X POST "$PROXY/hooks/batch" \
      -H "Content-Type: application/json" \
      -d "{\"session_id\":\"${SID}\",\"traceparent\":\"${TP}\",\"events\":$(echo "$EVENTS" | jq -s .)}"
  fi
  rm -f "$EXPORT_FILE"
fi
