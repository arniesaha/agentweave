#!/bin/bash
# AgentWeave Claude Code hook: SubagentStop
# Sends a single span when a subagent completes (fires once per subagent).

PROXY="${AGENTWEAVE_PROXY_URL:-http://localhost:4000}"
SID="${CLAUDE_SESSION_ID:-}"

INPUT=$(cat)

# Use jq for safe JSON composition; fall back to raw curl if unavailable.
if command -v jq >/dev/null 2>&1; then
  PAYLOAD=$(echo "$INPUT" | jq -c --arg sid "$SID" --arg psid "${CLAUDE_PARENT_SESSION_ID:-}" \
    '{span_name:"subagent.stop", session_id:$sid, attributes:{"prov.parent_session_id":$psid, "prov.agent.type":"subagent", hook_data:.}}')
  curl -s -X POST "$PROXY/hooks/span" \
    -H "Content-Type: application/json" \
    -d "$PAYLOAD" &
else
  curl -s -X POST "$PROXY/hooks/span" \
    -H "Content-Type: application/json" \
    -d "{\"span_name\":\"subagent.stop\",\"session_id\":\"${SID}\",\"attributes\":{\"prov.parent_session_id\":\"${CLAUDE_PARENT_SESSION_ID:-}\",\"prov.agent.type\":\"subagent\",\"hook_data\":$INPUT}}" &
fi
