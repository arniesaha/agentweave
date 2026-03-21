# Project-Level Tracking

AgentWeave can group spans by project so you can answer: *"How much did the agentweave project cost today? How many sub-agents were involved?"*

## How it works

Every span can carry `prov.project`. Set it once per task — all LLM calls, tool spans, and sub-agent spans inherit it automatically.

## Environment variables

| Variable | Header equivalent | Description |
|---|---|---|
| `AGENTWEAVE_PROJECT` | `X-AgentWeave-Project` | Project tag stamped on all spans |
| `AGENTWEAVE_SESSION_ID` | `X-AgentWeave-Session-Id` | Session identifier |
| `AGENTWEAVE_PARENT_SESSION_ID` | `X-AgentWeave-Parent-Session-Id` | Parent session for sub-agents |
| `AGENTWEAVE_TASK_LABEL` | `X-AgentWeave-Task-Label` | Human-readable task description |
| `AGENTWEAVE_AGENT_ID` | `X-AgentWeave-Agent-Id` | Agent identifier (e.g. `nix-v1`, `max-v1`) |

**Header takes precedence over env var** when both are set. This lets you override project per-request without changing process env.

---

## Proxy mode (zero-code)

Any process routed through the AgentWeave proxy picks up project automatically from env:

```bash
AGENTWEAVE_PROJECT=agentweave \
AGENTWEAVE_SESSION_ID=nix-main-$(date +%Y%m%d) \
AGENTWEAVE_TASK_LABEL="fix issue #101" \
ANTHROPIC_BASE_URL=http://192.168.1.70:30400/v1 \
claude --dangerously-skip-permissions --print "your task"
```

Or via the `/session` endpoint:

```bash
curl -X POST http://192.168.1.70:30400/session \
  -H "Content-Type: application/json" \
  -d '{"session_id": "nix-main-abc", "project": "agentweave", "task_label": "fix issue #101"}'
```

---

## Python SDK

```python
from agentweave import AgentWeaveConfig, trace_agent
import os

AgentWeaveConfig.setup(
    agent_id="nix-v1",
    agent_model="claude-sonnet-4-6",
    otel_endpoint="http://192.168.1.70:30418",
)

# Option 1: env var (picked up automatically)
os.environ["AGENTWEAVE_PROJECT"] = "agentweave"

# Option 2: pass explicitly to the decorator
@trace_agent(name="nix", session_id="nix-main-...", project="agentweave")
def my_agent(task: str): ...
```

---

## TypeScript / JavaScript SDK

```typescript
import { AgentWeaveConfig } from 'agentweave-sdk'

AgentWeaveConfig.setup({
  agentId: 'max-v1',
  agentModel: 'gemini-2.0-flash',
  otelEndpoint: 'http://192.168.1.70:30418',
  project: 'agentweave',   // set once at startup
})

// Or via env var — same as Python
process.env.AGENTWEAVE_PROJECT = 'agentweave'
```

---

## A2A / cross-agent passthrough (Max → Nix)

When Max delegates to Nix via A2A, pass `X-AgentWeave-Project` so Nix's spans inherit the same project:

```bash
curl -X POST http://192.168.1.70:8771/tasks?sync=true \
  -H "Authorization: Bearer $NIX_A2A_SECRET" \
  -H "Content-Type: application/json" \
  -H "X-AgentWeave-Project: agentweave" \
  -H "X-AgentWeave-Parent-Session-Id: max-main-abc" \
  -H "X-AgentWeave-Task-Label: summarize agentweave work" \
  -d '{"id": "task-1", "skill_id": "general", "message": {"parts": [{"type": "text", "text": "..."}]}}'
```

Nix's A2A server extracts the header and stamps it on all downstream spans automatically.

---

## Multi-project agents

Nix and Max work on many projects. The pattern is:

- **Agent ID** (`AGENTWEAVE_AGENT_ID`): set once at process start — never changes
- **Project** (`AGENTWEAVE_PROJECT`): set per task at spawn time — changes every session

```bash
# Working on AgentWeave
AGENTWEAVE_PROJECT=agentweave \
AGENTWEAVE_TASK_LABEL="implement issue #101" \
  claude --dangerously-skip-permissions --print "your coding task"

# Working on Launchpad
AGENTWEAVE_PROJECT=launchpad \
AGENTWEAVE_TASK_LABEL="refresh pipeline" \
  claude --dangerously-skip-permissions --print "your coding task"

# Working on Recall
AGENTWEAVE_PROJECT=recall \
AGENTWEAVE_TASK_LABEL="index new notes" \
  claude --dangerously-skip-permissions --print "your task"
```

Sub-agents spawned by these processes automatically inherit `AGENTWEAVE_PROJECT` from the environment.

---

## Dashboard usage

After tagging spans with a project, use the **Project** dropdown in the Overview tab header to filter all panels:

- Total cost for `agentweave` today
- LLM calls and model breakdown per project  
- Sub-agent count per project (Session Explorer)
- Session nodes show a project badge (indigo pill)

Default is **All projects** — no filter applied.
