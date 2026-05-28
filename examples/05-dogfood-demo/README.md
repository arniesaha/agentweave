# Example 05: Public Dogfood Demo

This package is the public-safe version of the "AgentWeave building
AgentWeave" story. It is meant for developer preview screenshots, docs, and
conference/demo recordings without depending on private NAS infrastructure or
raw production traces.

The fixture shows:

- parent/child agent causality from a maintainer agent to three child agents
- model attribution across Anthropic, OpenAI, and Google-style calls
- token and cost attribution per LLM span
- one recoverable tool error that demonstrates debugging value
- redacted input/output summaries instead of private prompt text

## Files

- `demo-trace.json` - sanitized fixture data for dashboard/demo-mode import
- `index.html` - small static viewer for staging public screenshots
- `screenshot-checklist.md` - screenshot checklist for overview/session/replay assets

## Run The Local Demo

From the repo root:

```bash
python -m http.server 8050
```

Then open:

```text
http://localhost:8050/examples/05-dogfood-demo/
```

No AgentWeave proxy, Grafana, Tempo, private tunnel, or cloud credentials are
required. The viewer reads only `demo-trace.json`.

## Use As Dashboard Fixture Source

Dashboard/demo-mode work can consume `demo-trace.json` directly. The fixture is
structured around stable public concepts:

- `agents[]` defines session graph nodes and `parent_session_id` edges
- `spans[]` defines replay order, status, duration, and redacted IO summaries
- `attributes["prov.llm.*"]` defines provider/model/token fields
- `attributes["cost.usd"]` defines per-call cost attribution
- `events[]` carries the sanitized exception event for the error span

Keep any renderer tolerant of extra fields so this fixture can evolve without
breaking screenshots.

## Privacy Boundary

The fixture intentionally does not include:

- raw prompt or completion text
- private hostnames, tunnels, dashboards, or NAS URLs
- absolute local file paths
- API keys, tokens, account ids, or customer data
- real trace ids from private dogfooding runs

If you update the fixture, run:

```bash
python -m json.tool examples/05-dogfood-demo/demo-trace.json >/tmp/agentweave-demo-trace.json
python - <<'PY'
from pathlib import Path
needles = ["arnab" + "saha.com", "192" + ".168.", "10" + ".43.", "/" + "home/", "/" + "Users/", "sk" + "-"]
for path in Path("examples/05-dogfood-demo").rglob("*"):
    if path.is_file() and any(needle in path.read_text(errors="ignore") for needle in needles):
        raise SystemExit(f"private value found in {path}")
PY
```

The privacy scan should exit cleanly.

## Screenshot Story

Use this fixture to capture three public assets:

- Overview: AgentWeave can summarize an agentic run across agents, tokens,
  cost, and errors.
- Session: AgentWeave preserves causality across delegated child agents.
- Replay: AgentWeave makes a tool validation failure explainable without
  leaking private prompt text.
