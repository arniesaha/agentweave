# OpenClaw Native Content Privacy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent new native OpenClaw prompt, response, and tool content from reaching Tempo while preserving model, token, and trace telemetry.

**Architecture:** Disable OpenClaw `diagnostics.otel.captureContent` on the gateway, then add a service-scoped attributes processor at the collector ingest boundary. A synthetic OTLP probe verifies that content keys disappear only on native OpenClaw spans and that model/usage metadata survives.

**Tech Stack:** OpenClaw 2026.9.2, OpenTelemetry Collector Contrib 0.126.0, OTLP/HTTP JSON, Python standard library, Kubernetes, Tempo.

**Spec:** `docs/superpowers/specs/2026-09-13-openclaw-native-content-privacy-design.md`

## Global Constraints

- Scope collector stripping to resource `service.name=openclaw`; keep unrelated producers unchanged.
- Process before both Tempo and debug exporters.
- Never print real captured attribute values in issues, logs, tests, or reports.
- Keep provider, model, token, latency, trace-parent, and resource identity attributes.
- Existing Tempo traces and proxy/bridge previews are outside this issue.
- Do not change model routing; observability remains fail-open.

---

### Task 1: Synthetic collector privacy probe

**Files:**
- Create: `scripts/verify-native-content-privacy.py`

**Interfaces:**
- Consumes: OTLP endpoint from `AGENTWEAVE_OTLP_ENDPOINT` (default `http://10.43.221.47:4318`) and Tempo endpoint from `AGENTWEAVE_TEMPO_URL` (default `http://192.168.1.70:31989`).
- Produces: exit 0 only when `openclaw` content keys are absent, an unrelated service's matching key is retained, and the native model/token fields survive.

- [ ] **Step 1: Write the probe.** Use only Python standard-library `json`, `os`, `time`, `urllib.request`, and `uuid`. Generate one trace ID, two span IDs, and two `resourceSpans` (`openclaw`, `privacy-probe`). Each span has a harmless literal marker in `gen_ai.input.messages`; the OpenClaw span also has `openclaw.content.input_messages`, a novel `openclaw.content.*` key, `gen_ai.tool.definitions`, `openclaw.model`, and `gen_ai.usage.input_tokens`. POST JSON to `/v1/traces`, then poll `/api/traces/{trace_id}` for at most 60 seconds to allow Tempo ingestion. Extract attribute keys by span name. Assert the OpenClaw forbidden keys are absent, its model/token fields are present, and the unrelated span retains all marker keys. Print only pass/fail and the synthetic trace ID. The assertion core is:

```python
for span_name, keys in spans_by_name.items():
    if span_name == "privacy-probe.openclaw":
        assert "gen_ai.input.messages" not in keys
        assert "openclaw.content.input_messages" not in keys
        assert "gen_ai.tool.definitions" not in keys
        assert {"openclaw.model", "gen_ai.usage.input_tokens"} <= keys
    elif span_name == "privacy-probe.other":
        assert "gen_ai.input.messages" in keys
```
- [ ] **Step 2: Run red against the current collector.** `python3 scripts/verify-native-content-privacy.py`. Expected: nonzero exit with `native content attributes reached Tempo` because the deployed collector does not strip these fields.
- [ ] **Step 3: Commit the probe.** `git add scripts/verify-native-content-privacy.py && git commit -m 'test(k8s): probe native content stripping (#291)'`.

### Task 2: Collector ingress safeguard

**Files:**
- Modify: `deploy/k8s/monitoring/otel-collector.yaml`
- Test: `scripts/verify-native-content-privacy.py`

**Interfaces:**
- Consumes: OTLP spans with resource `service.name`.
- Produces: native OpenClaw span attributes without known content keys; other services and non-content metadata unchanged.

- [ ] **Step 1: Add `attributes/strip_openclaw_content`** under `processors` with the following shape, then add it between `attributes/strip_pii` and `batch` in `service.pipelines.traces.processors`:

```yaml
attributes/strip_openclaw_content:
  include:
    match_type: strict
    services: [openclaw]
  actions:
    - pattern: '^openclaw\.content\..*'
      action: delete
    - key: gen_ai.input.messages
      action: delete
    - key: gen_ai.output.messages
      action: delete
    - key: gen_ai.tool.definitions
      action: delete
    - key: gen_ai.tool.call.arguments
      action: delete
    - key: gen_ai.tool.call.result
      action: delete
    - key: input.value
      action: delete
    - key: output.value
      action: delete
```

- [ ] **Step 2: Validate collector syntax before deployment.** Python's installed PyYAML parses the multi-document manifest and extracts the nested collector config into a temporary file. Run:

```bash
AGENTWEAVE_TEST_COLLECTOR_CONFIG=$(mktemp /tmp/agentweave-collector-issue-291.XXXXXX.yaml)
export AGENTWEAVE_TEST_COLLECTOR_CONFIG
python3 -c 'import os,yaml; from pathlib import Path; docs=yaml.safe_load_all(Path("deploy/k8s/monitoring/otel-collector.yaml").read_text()); cm=next(d for d in docs if d.get("kind")=="ConfigMap"); Path(os.environ["AGENTWEAVE_TEST_COLLECTOR_CONFIG"]).write_text(cm["data"]["collector.yaml"])'
docker run --rm -v "$AGENTWEAVE_TEST_COLLECTOR_CONFIG:/conf/collector.yaml:ro" otel/opentelemetry-collector-contrib:0.126.0 validate --config=/conf/collector.yaml
```

Expected: exit 0. The temporary file contains only collector configuration, never credentials.
- [ ] **Step 3: Verify green before production.** Run a temporary collector from the pinned image on `127.0.0.1:4319`, using the extracted config with only the Tempo exporter address changed to `http://192.168.1.70:30418`. Run `AGENTWEAVE_OTLP_ENDPOINT=http://127.0.0.1:4319 python3 scripts/verify-native-content-privacy.py`; it must exit 0. Stop and remove only that temporary container after the probe.
- [ ] **Step 4: Review the diff and commit.** `git diff --check && git diff -- deploy/k8s/monitoring/otel-collector.yaml`, then `git add deploy/k8s/monitoring/otel-collector.yaml docs/superpowers/plans/2026-09-13-openclaw-native-content-privacy.md && git commit -m 'fix(k8s): strip native OpenClaw content at ingress (#291)'`.

### Task 3: Source setting, rollout, and post-deploy evidence

**Files:**
- Modify external: `/home/Arnab/.openclaw/openclaw.json` through `openclaw config set` after owner-readable backup.
- Update: `docs/openclaw-native-otel-parity.md` with a dated, metadata-only privacy follow-up.

**Interfaces:**
- Consumes: merged collector manifest from Task 2 and current gateway config.
- Produces: live exporter with `captureContent=false` and clean new native traces.

- [ ] **Step 1: Preserve and change gateway config.** Back up `/home/Arnab/.openclaw/openclaw.json` to `/home/Arnab/.openclaw/backups/openclaw.json.issue-291.<timestamp>` with owner-only permissions. Set `diagnostics.otel.captureContent=false` using `openclaw config set`, then read back only that Boolean.
- [ ] **Step 2: Add the dated metadata-only privacy follow-up to the parity doc, run final tests, and open the PR.** Do not include real captured values; the dated note must state that old Tempo data and proxy previews remain outside this fix.
- [ ] **Step 3: Merge the green PR through the normal review path, update clean `main`, and run `bash scripts/deploy.sh`.** The collector config-hash annotation must roll the collector. Do not deploy a dirty or unmerged worktree.
- [ ] **Step 4: Restart the gateway once** with a supported Node runtime (`PATH=/home/Arnab/.nvm/versions/node/v24.19.0/bin:$PATH openclaw gateway restart`) and confirm the service active, native exporter `started`/`configured`, and bridge loaded.
- [ ] **Step 5: Run the synthetic probe again.** `python3 scripts/verify-native-content-privacy.py`. Expected: exit 0; retain only synthetic IDs and safe key presence in the report.
- [ ] **Step 6: Send one disposable low-content gateway turn, then query only its Tempo attribute keys.** Assert no forbidden native content keys and preserved model/token attributes; confirm bridge turn attribution remains present. Run `bash scripts/verify.sh` and require 4/4.
- [ ] **Step 7: Record verification and caveats.** Comment on and close #291 only after both deploy and verify pass, linking the Grafana dashboard. If either fails, keep/reopen #291 with failure details and rollback state.
