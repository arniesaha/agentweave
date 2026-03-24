#!/bin/bash
# AgentWeave Claude Code hook: Stop
# Batch-exports all buffered tool use events when the session ends.

PROXY="${AGENTWEAVE_PROXY_URL:-http://localhost:4000}"
BUFFER="${AGENTWEAVE_HOOKS_BUFFER:-$HOME/.agentweave/hooks_buffer.jsonl}"

if [ -f "$BUFFER" ]; then
  EVENTS=$(cat "$BUFFER")
  if [ -n "$EVENTS" ]; then
    curl -s -X POST "$PROXY/hooks/batch" \
      -H "Content-Type: application/json" \
      -d "{\"session_id\":\"${CLAUDE_SESSION_ID:-}\",\"events\":$(echo "$EVENTS" | jq -s .)}"
  fi
  rm "$BUFFER"
fi
