#!/bin/bash
# AgentWeave Claude Code hook: PostToolUse
# Appends tool use events to a local JSONL buffer (fast, non-blocking).
# The buffer is batch-exported by stop.sh when the session ends.

BUFFER="${AGENTWEAVE_HOOKS_BUFFER:-$HOME/.agentweave/hooks_buffer.jsonl}"
mkdir -p "$(dirname "$BUFFER")"

INPUT=$(cat)
echo "{\"event\":\"post_tool_use\",\"ts\":$(date +%s%3N),\"session_id\":\"${CLAUDE_SESSION_ID:-}\",\"data\":$INPUT}" >> "$BUFFER"
