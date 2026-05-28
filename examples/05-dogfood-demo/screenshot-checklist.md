# Public Demo Screenshot Checklist

Use `demo-trace.json` as the source for public developer-preview screenshots.
Do not capture screenshots from Arnab's private Grafana, Tempo, NAS, or tunnel
hosts for public materials.

## Before Capture

- Run the local viewer with `python -m http.server 8050`.
- Open `http://localhost:8050/examples/05-dogfood-demo/`.
- Confirm the page loads from `demo-trace.json`, not production traces.
- Confirm the browser address bar, bookmarks bar, and private extensions are
  hidden or cropped out.
- Confirm no private hostnames, IPs, file paths, prompts, or tokens are visible.

## Overview Asset

Target filename: `screenshots/public-demo-overview.png`

Must show:

- title: `AgentWeave building AgentWeave`
- agent count
- span count
- LLM call count
- total cost
- error count

Avoid:

- raw JSON
- terminal panes containing local paths
- private Grafana sidebar or data-source names

## Session Asset

Target filename: `screenshots/public-demo-session.png`

Must show:

- `maintainer-agent` as orchestrator/root
- `docs-worker`, `fixture-worker`, and `review-worker` as child agents
- `parent_session_id` edges or equivalent visual grouping
- task labels that are generic enough for public docs

Avoid:

- private agent names from live dogfooding
- real user conversation text
- private project paths

## Replay Asset

Target filename: `screenshots/public-demo-replay.png`

Must show:

- ordered agent, LLM, and tool spans
- at least two different model labels
- token/cost attribution on LLM spans
- `tool.validate_fixture_privacy` as a recoverable error
- redacted input/output summaries, not raw prompts

Avoid:

- full prompts or completions
- real stack traces
- screenshots that imply the error is an AgentWeave product failure

## Final Privacy Pass

Before committing new screenshot files, inspect them manually and rerun:

```bash
python -m json.tool examples/05-dogfood-demo/demo-trace.json >/tmp/agentweave-demo-trace.json
python - <<'PY'
from pathlib import Path
needles = ["arnab" + "saha.com", "192" + ".168.", "10" + ".43.", "/" + "home/", "/" + "Users/", "sk" + "-"]
for root in [Path("examples/05-dogfood-demo"), Path("screenshots/README.md")]:
    paths = root.rglob("*") if root.is_dir() else [root]
    for path in paths:
        if path.is_file() and any(needle in path.read_text(errors="ignore") for needle in needles):
            raise SystemExit(f"private value found in {path}")
PY
```

The privacy scan should exit cleanly. Existing legacy screenshot docs may still
mention older private demo context until they are separately cleaned up.
