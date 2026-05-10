# Attribution Runbook

How AgentWeave assigns `prov.agent.id`, `prov.session.id`, etc. to every LLM
span, and how to diagnose missing/wrong attribution.

## Resolution order (`sdk/python/agentweave/proxy.py:_resolve_attrs`)

For each attribute (`agent_id`, `session_id`, `task_label`,
`parent_session_id`, `agent_type`, `project`), the proxy resolves in this
order — first non-empty value wins:

1. **Per-key forced context** — if the request carries
   `X-AgentWeave-Session-Key: <k>` and `<k>` is in
   `_forced_session_contexts`, the stored value wins. This is the
   bridge-driven subagent path: caller opts in by sending the matching key.
2. **Subagent env vars** — if `AGENTWEAVE_AGENT_TYPE=subagent` is set in
   the proxy process env, `AGENTWEAVE_<ATTR>` is used.
3. **Explicit request header** — `X-AgentWeave-Agent-Id`,
   `X-AgentWeave-Session-Id`, `X-AgentWeave-Task-Label`,
   `X-AgentWeave-Parent-Session-Id`, `X-AgentWeave-Agent-Type`,
   `X-AgentWeave-Project`.
4. **Legacy global forced context** — if `_session_context_force` is set
   globally (legacy callers that POST `/session` without `session_key`),
   the stored value is used as a low-priority fallback. This intentionally
   ranks BELOW explicit headers so unrelated callers are not hijacked
   (issue #189).
5. **Proxy process env** — `AGENTWEAVE_<ATTR>` without subagent mode.
6. **Static config** — `agentweave.yaml`.
7. **Sentinel** — `"unattributed"` for `agent_id`; `None` for the rest.

## `unattributed` vs `unknown` on the dashboard

| Bucket | Meaning | What to look at |
|---|---|---|
| `unattributed` | Proxy explicitly stamped this — the resolution chain reached step 7 because nothing was supplied. | Caller is not sending attribution. Either set `AGENTWEAVE_AGENT_ID` env or use `scripts/claude-delegate.sh`. |
| `unknown` | Dashboard / Prometheus side — the label was dropped or the metric was emitted before `prov.agent.id` existed on the span. | spanmetrics processor config, Prometheus relabel rules, or a span that bypassed the proxy entirely. Pull the trace from Tempo to confirm. |

## Finding offending traces

```bash
# Traces with no prov.agent.id at all (last 1h)
curl -s 'http://192.168.1.70:30418/api/search' \
  --get \
  --data-urlencode 'q={resource.service.name="agentweave-proxy" && !prov.agent.id}' \
  --data-urlencode 'limit=20' | jq '.traces[]|{traceID,rootServiceName,startTimeUnixNano}'

# Traces stamped as `unattributed`
curl -s 'http://192.168.1.70:30418/api/search' \
  --get \
  --data-urlencode 'q={prov.agent.id="unattributed"}' \
  --data-urlencode 'limit=20' | jq '.traces[]|.traceID'
```

If `prov.agent.id="unattributed"` appears for a known caller, the caller
is not sending attribution at all. If `unknown` appears on the dashboard
but Tempo shows `prov.agent.id` on the span, the gap is in
spanmetrics/Prometheus — check the collector pipeline.

## Delegated Claude Code launches

For any work delegated to Claude Code (Nix → Claude Code, manual sub-tasks,
dryruns), use `scripts/claude-delegate.sh` rather than handcrafted
`ANTHROPIC_CUSTOM_HEADERS`. Static global settings in
`~/.claude/settings.json` are fine for interactive use but are too coarse
for delegated work — every delegated session would collapse into the same
`claude-code-main`.

```bash
scripts/claude-delegate.sh \
  --agent-id   claude-code-nas-subagent \
  --session-id "claude-code-mux-67-$(date +%Y%m%d-%H%M%S)" \
  --parent     nix-main \
  --project    mux \
  --task       "mux issue 67 openclaw routing" \
  -- --dangerously-skip-permissions --model claude-sonnet-4-6 --print "<task>"
```
