# Claude Code Hooks Integration

AgentWeave integrates with Claude Code's hooks system to capture tool calls, subagent spans, and session-level data that the proxy alone cannot observe.

## Overview

Claude Code supports a hooks system via `~/.claude/settings.json` that fires shell scripts at lifecycle events. AgentWeave uses three hooks:

| Hook | Event | Strategy |
|------|-------|----------|
| `PostToolUse` | After each tool call | Buffer to JSONL (fast, non-blocking) |
| `SubagentStop` | When a subagent completes | Single POST to proxy |
| `Stop` | When session ends | Batch export buffered events |

**Critical design note:** Hook scripts run synchronously and block Claude Code. The `PostToolUse` hook must be fast — it appends to a local file instead of making network calls.

## Quick Start

### 1. Install hooks

```bash
agentweave hooks install
```

This idempotently merges AgentWeave hooks into `~/.claude/settings.json`.

### 2. Start the proxy

```bash
agentweave proxy start --port 4000
```

### 3. Restart Claude Code

The hooks are loaded on startup.

## How It Works

### PostToolUse (Buffer Pattern)

The `PostToolUse` hook fires after every tool call. To avoid blocking Claude Code, it appends events to a local JSONL buffer:

```bash
# ~/.agentweave/hooks_buffer.jsonl
{"event":"post_tool_use","ts":1711234567890,"session_id":"abc123","data":{...}}
{"event":"post_tool_use","ts":1711234567891,"session_id":"abc123","data":{...}}
```

### Stop (Batch Export)

When the Claude Code session ends, the `Stop` hook reads the buffer and sends all events to the proxy in a single batch:

```bash
curl -X POST http://localhost:4000/hooks/batch \
  -H "Content-Type: application/json" \
  -d '{"session_id":"abc123","events":[...]}'
```

### SubagentStop (Single Span)

When a subagent completes, the `SubagentStop` hook sends a single span linking the subagent to its parent:

```bash
curl -X POST http://localhost:4000/hooks/span \
  -H "Content-Type: application/json" \
  -d '{"span_name":"subagent.stop","session_id":"...","attributes":{...}}'
```

## Proxy Endpoints

### POST /hooks/span

Creates a single span from hook data.

**Request:**
```json
{
  "span_name": "subagent.stop",
  "session_id": "abc123",
  "attributes": {
    "prov.parent_session_id": "parent-456",
    "prov.agent.type": "subagent"
  }
}
```

**Response:**
```json
{"ok": true, "span_name": "subagent.stop"}
```

### POST /hooks/batch

Creates spans for a batch of buffered events.

**Request:**
```json
{
  "session_id": "abc123",
  "events": [
    {
      "event": "post_tool_use",
      "ts": 1711234567890,
      "data": {
        "tool_name": "Read",
        "tool_input": "/path/to/file.py"
      }
    }
  ]
}
```

**Response:**
```json
{"ok": true, "spans_created": 1}
```

## CLI Commands

### agentweave hooks install

Installs AgentWeave hooks into Claude Code settings.

```bash
# Install hooks
agentweave hooks install

# Preview changes without modifying
agentweave hooks install --dry-run

# Custom settings path
agentweave hooks install --settings-path /path/to/settings.json
```

### agentweave hooks uninstall

Removes AgentWeave hooks from Claude Code settings.

```bash
agentweave hooks uninstall
```

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AGENTWEAVE_PROXY_URL` | `http://localhost:4000` | Proxy URL for hook callbacks |
| `AGENTWEAVE_HOOKS_BUFFER` | `~/.agentweave/hooks_buffer.jsonl` | Path to event buffer file |

### Claude Code Environment

These variables are set by Claude Code and available to hooks:

| Variable | Description |
|----------|-------------|
| `CLAUDE_SESSION_ID` | Current session ID |
| `CLAUDE_PARENT_SESSION_ID` | Parent session ID (for subagents) |

## Manual Installation

If you prefer to configure hooks manually, add this to `~/.claude/settings.json`:

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": ".*",
        "hooks": [
          {"type": "command", "command": "agentweave-hook-post-tool-use"}
        ]
      }
    ],
    "SubagentStop": [
      {
        "hooks": [
          {"type": "command", "command": "agentweave-hook-subagent-stop"}
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {"type": "command", "command": "agentweave-hook-stop"}
        ]
      }
    ]
  }
}
```

## Troubleshooting

### Hooks not firing

1. Verify hooks are installed: `cat ~/.claude/settings.json | jq .hooks`
2. Restart Claude Code after installing hooks
3. Check that the proxy is running: `curl http://localhost:4000/health`

### Buffer not being exported

1. Check buffer exists: `cat ~/.agentweave/hooks_buffer.jsonl`
2. Verify `jq` is installed (required by `stop.sh`)
3. Check proxy logs for errors

### Missing tool call data

The `PostToolUse` hook captures what Claude Code provides. Some fields may vary by tool type.

## Span Attributes

Spans created from hooks include these attributes:

| Attribute | Description |
|-----------|-------------|
| `prov.session.id` | Claude Code session ID |
| `prov.hook.source` | Always `"claude-code"` |
| `prov.hook.event_type` | Event type (e.g., `post_tool_use`) |
| `prov.hook.timestamp_ms` | Event timestamp in milliseconds |
| `prov.tool.name` | Tool name (if present) |
| `prov.tool.input_preview` | First 512 chars of tool input |
| `prov.tool.result_preview` | First 512 chars of tool result |
| `prov.parent_session_id` | Parent session ID (for subagents) |
| `prov.agent.type` | Agent type (e.g., `subagent`) |
