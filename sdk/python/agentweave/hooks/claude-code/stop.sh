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
    # Build the payload with jq so quotes or backslashes in a path, project
    # name, or agent id can't corrupt the JSON body. jq is already required
    # here for `jq -s`, so this adds no new dependency.
    # Agent identity so hook spans are attributable and joinable (#247);
    # empty values are dropped proxy-side rather than written as empty attrs.
    PAYLOAD=$(echo "$EVENTS" | jq -s \
      --arg sid "$SID" \
      --arg tp "${AGENTWEAVE_TRACEPARENT:-}" \
      --arg aid "${AGENTWEAVE_AGENT_ID:-}" \
      --arg atype "${AGENTWEAVE_AGENT_TYPE:-}" \
      --arg proj "${AGENTWEAVE_PROJECT:-}" \
      --arg cwd "$PWD" \
      '{session_id:$sid, traceparent:$tp, agent_id:$aid, agent_type:$atype, project:$proj, cwd:$cwd, events:.}')
    curl -s -X POST "$PROXY/hooks/batch" \
      -H "Content-Type: application/json" \
      -d "$PAYLOAD"
  fi
  rm -f "$EXPORT_FILE"
fi
