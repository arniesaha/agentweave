# Trace Data-Quality Gate

AgentWeave dogfoods its own traces, so the trace data has to be clean enough to
trust before screenshots, demos, or public preview material use it. The
developer-preview gate lives in `scripts/trace_quality_gate.py` and checks the
same Tempo/Prometheus labels used by the dashboard.

## What It Checks

The gate fails on:

- LLM spans with `prov.agent.id` missing, `unknown`, or `unattributed`
- LLM spans without a usable `prov.llm.model`
- suspicious model labels that look like query syntax leaked into the label,
  such as `claude-opus-4-7[1m]`

The gate warns on:

- known-model LLM spans missing token fields
- known-model LLM spans missing `cost.usd` in Tempo data
- live query failures when another source still produced data

The gate does not fail blank-model lifecycle spans when they are not LLM calls,
for example hook, subagent, session, or OpenClaw lifecycle spans. Those are
reported as informational so the dashboard can stay honest without turning
non-LLM activity into false `unknown model` failures.

## Run Against Dogfood

```bash
python3 scripts/trace_quality_gate.py \
  --prometheus-url http://localhost:9090 \
  --tempo-url http://localhost:3200 \
  --range 6h \
  --json
```

Use a longer window before sharing public screenshots:

```bash
python3 scripts/trace_quality_gate.py \
  --prometheus-url http://localhost:9090 \
  --tempo-url http://localhost:3200 \
  --range 24h \
  --json
```

Exit code is non-zero when failures are present. Add `--fail-on-warn` in CI or
release checks when warnings should block the run too.

## Offline Fixtures

Tests and local debugging can run without live Prometheus or Tempo:

```bash
python3 scripts/trace_quality_gate.py --fixture /path/to/fixture.json --json
```

Fixture files may include Prometheus API responses, Tempo search/trace API
responses, or direct span records:

```json
{
  "prometheus": {
    "span_inventory": {
      "status": "success",
      "data": {
        "resultType": "vector",
        "result": [
          {
            "metric": {
              "service": "agentweave-proxy",
              "prov_activity_type": "llm_call",
              "prov_llm_model": "claude-sonnet-4-6",
              "prov_agent_id": "nix-v1"
            },
            "value": [1760000000, "3"]
          }
        ]
      }
    }
  },
  "spans": [
    {
      "source": "fixture",
      "service": "agentweave-proxy",
      "activity_type": "hook",
      "span_name": "hook.post_tool_use",
      "model": "",
      "agent_id": "nix-v1"
    }
  ]
}
```

## Launch-Clean Dogfood Data

A launch-clean window means:

- no real LLM calls are unattributed
- no real LLM calls have blank or malformed model labels
- lifecycle spans can be present but are not counted as model quality failures
- known-model calls have enough token and cost data for dashboard totals to be
  explainable

Run the gate before capturing dashboard screenshots or publishing AgentWeave
dogfood numbers.
