#!/bin/bash
# AgentWeave Claude Code hook: PostToolUse
# Appends tool use events to a local JSONL buffer (fast, non-blocking).
# The buffer is batch-exported by stop.sh when the session ends.
# Buffer is per-session to avoid cross-session data leaks.

SID="${CLAUDE_SESSION_ID:-default}"
BUFFER="${AGENTWEAVE_HOOKS_BUFFER:-$HOME/.agentweave/hooks_buffer_${SID}.jsonl}"
mkdir -p "$(dirname "$BUFFER")"

INPUT=$(cat)
# Use jq for safe JSON composition if available, fall back to raw echo.
# The fallback is acceptable because Claude Code controls the input format.
if command -v jq >/dev/null 2>&1; then
  echo "$INPUT" | jq -c --arg ts "$(date +%s%3N)" --arg sid "$SID" \
    '{event:"post_tool_use", ts:($ts|tonumber), session_id:$sid, data:.}' >> "$BUFFER"
else
  echo "{\"event\":\"post_tool_use\",\"ts\":$(date +%s%3N),\"session_id\":\"${SID}\",\"data\":$INPUT}" >> "$BUFFER"
fi
