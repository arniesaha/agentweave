# Demo Traces

AgentWeave's strongest public demo is dogfooding: AgentWeave observing agents
that are improving AgentWeave. Public demos must use sanitized fixtures rather
than private production traces.

The developer-preview fixture lives in:

```text
examples/05-dogfood-demo/demo-trace.json
```

It is designed to support screenshot/demo work for:

- overview pages showing total agents, spans, LLM calls, cost, and errors
- session views showing parent/child agent causality
- replay views showing LLM calls, tool calls, and a recoverable tool error

## Local Preview

From the repo root:

```bash
python -m http.server 8050
```

Open:

```text
http://localhost:8050/examples/05-dogfood-demo/
```

This preview works offline except for browser loading itself. It does not need
AgentWeave services, Grafana, Tempo, private URLs, or API keys.

## Fixture Contract

The fixture is not a full OpenTelemetry export. It is a stable, public demo
contract that dashboard demo-mode or docs tooling can adapt:

- `privacy` documents what has been removed
- `trace.summary` powers overview metrics
- `agents` powers session graph nodes and parent/child edges
- `spans` powers replay timelines
- `attributes["prov.llm.model"]`, token fields, and `cost.usd` power model and
  cost attribution
- `events` carries sanitized exception details

Prefer additive changes. Existing keys should remain stable so screenshots and
demo-mode code do not churn.

## Sanitization Rules

Public demo traces must not include:

- raw prompt or completion text
- private hostnames, tunnels, dashboards, or LAN/Kubernetes IPs
- absolute local paths
- API keys, access tokens, account ids, customer data, or real private trace ids

Use summaries such as `input_summary` and `output_summary` when a screenshot
needs to explain intent.

## Validation

Run these checks before using the fixture in public material:

```bash
python -m json.tool examples/05-dogfood-demo/demo-trace.json >/tmp/agentweave-demo-trace.json
python - <<'PY'
from pathlib import Path
needles = ["arnab" + "saha.com", "192" + ".168.", "10" + ".43.", "/" + "home/", "/" + "Users/", "sk" + "-"]
for root in [Path("examples/05-dogfood-demo"), Path("docs/demo-traces.md")]:
    paths = root.rglob("*") if root.is_dir() else [root]
    for path in paths:
        if path.is_file() and any(needle in path.read_text(errors="ignore") for needle in needles):
            raise SystemExit(f"private value found in {path}")
PY
```

The privacy scan should exit cleanly. If it fails, replace the value with
`localhost`, `example.invalid`, a relative path, or a redacted summary.
