#!/bin/bash
# AgentWeave Claude Code hook: SubagentStop
# Sends a single span when a subagent completes (fires once per subagent).

PROXY="${AGENTWEAVE_PROXY_URL:-http://localhost:4000}"

INPUT=$(cat)

curl -s -X POST "$PROXY/hooks/span" \
  -H "Content-Type: application/json" \
  -d "{\"span_name\":\"subagent.stop\",\"session_id\":\"${CLAUDE_SESSION_ID:-}\",\"attributes\":{\"prov.parent_session_id\":\"${CLAUDE_PARENT_SESSION_ID:-}\",\"prov.agent.type\":\"subagent\",\"hook_data\":$INPUT}}" &
