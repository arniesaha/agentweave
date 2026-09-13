# OpenClaw Native OTel Parity Rollout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable OpenClaw's bundled native OTel exporter alongside the AgentWeave bridge and record trace-level parity evidence, especially for Codex parenting.

**Architecture:** Register `diagnostics-otel` in the live OpenClaw configuration, point it at the existing collector, and provide static AgentWeave identity through an isolated systemd resource-attribute drop-in. Preserve the bridge and model request path, then use timestamped workloads and Tempo trace retrieval to compare both telemetry paths without deduplication.

**Tech Stack:** OpenClaw 2026.9.2 CLI and bundled `diagnostics-otel`, systemd user services, OTLP/HTTP protobuf, AgentWeave collector, Tempo HTTP API/TraceQL, Markdown evidence report

**Spec:** `docs/superpowers/specs/2026-09-12-openclaw-native-otel-parity-design.md`

## Global Constraints

- This phase deletes nothing: the AgentWeave bridge and proxy remain enabled and unchanged.
- Native telemetry must fail open and must not alter model execution behavior.
- The collector endpoint is exactly `http://10.43.221.47:4318`.
- Static resource identity is exactly `prov.agent.id=nix-v1,prov.project=nix`.
- Duplicate native and bridge spans are expected and must not be deduplicated in #285.
- Attribute-level Tempo evidence is required; source inspection and HTTP success alone are insufficient.
- Never print or commit the complete OpenClaw configuration, environment file, credentials, or prompt content.
- A gateway restart is an intentional, user-approved live-session interruption; capture rollback state first.

---

### Task 1: Establish the Parity Report and Preflight Baseline

**Files:**
- Create: `docs/openclaw-native-otel-parity.md`
- Reference: `HANDOFF-openclaw-native-otel.md`
- Reference: `scripts/verify.sh:1`
- Reference: `scripts/trace_quality_gate.py:1`

**Interfaces:**
- Consumes: redacted OpenClaw config fragments, gateway status, collector reachability, recent Tempo inventory
- Produces: a timestamped pre-change baseline and empty evidence tables for all acceptance criteria

- [ ] Create `docs/openclaw-native-otel-parity.md` with sections for environment, preflight, exporter startup, smoke test, parity matrix, field inventory, Codex conclusion, rollback, and caveats.
- [ ] Record the OpenClaw commit/version, AgentWeave branch/commit, gateway PID/start time, and collector/Tempo endpoints without recording secrets.
- [ ] Re-read only `.diagnostics.otel` plus the `diagnostics-otel` and `agentweave-bridge` plugin entries with `jq`; record that native registration and endpoint are absent before rollout.
- [ ] Confirm `http://10.43.221.47:4318/v1/traces` is reachable from the host; treat any HTTP response as network reachability and a timeout/refusal as failure.
- [ ] Query Tempo over `http://192.168.1.70:31989` for recent `resource.service.name = "openclaw"` spans and record the pre-change count/absence.
- [ ] Run `scripts/verify.sh` to establish that proxy, bridge-backed tracing, Tempo, and Grafana are healthy before the change.
- [ ] Review the report for secrets and commit only the baseline document with `docs(proxy): add native OTel parity baseline (#285)`.

### Task 2: Enable Native Export with a Reversible Live Configuration Change

**Files:**
- Modify operational state: `/home/Arnab/.openclaw/openclaw.json`
- Create operational state: `/home/Arnab/.config/systemd/user/openclaw-gateway.service.d/agentweave-otel.conf`
- Modify: `docs/openclaw-native-otel-parity.md`

**Interfaces:**
- Consumes: validated preflight and current exact config values
- Produces: a running gateway with both telemetry paths and persistent OTel resource identity

- [ ] Create timestamped, owner-readable backups of `openclaw.json`, `gateway.systemd.env`, and any pre-existing `agentweave-otel.conf`; record backup paths outside git.
- [ ] Run `openclaw config set ... --dry-run --expect-current-absent` for `diagnostics.otel.endpoint` and `plugins.entries["diagnostics-otel"]` so drift aborts rather than overwrites an unexpected value.
- [ ] Apply `diagnostics.otel.endpoint = "http://10.43.221.47:4318"` and `plugins.entries["diagnostics-otel"] = {"enabled":true}` with the OpenClaw 2026.9.2 CLI.
- [ ] Use `systemctl --user edit openclaw-gateway --drop-in=agentweave-otel.conf --stdin` to add only `Environment="OTEL_RESOURCE_ATTRIBUTES=prov.agent.id=nix-v1,prov.project=nix"`.
- [ ] Run `jq empty /home/Arnab/.openclaw/openclaw.json`, re-read only the redacted relevant fragment, and inspect the effective systemd unit before restart.
- [ ] Run `systemctl --user daemon-reload` and `systemctl --user restart openclaw-gateway` once.
- [ ] Require `systemctl --user is-active openclaw-gateway` and the gateway's native status probe to pass; if either fails, execute the documented rollback immediately.
- [ ] Record the post-restart PID/start time and redacted effective configuration in the report.

### Task 3: Prove Exporter Startup and Fail-Open Behavior

**Files:**
- Modify: `docs/openclaw-native-otel-parity.md`

**Interfaces:**
- Consumes: live gateway journal and Tempo HTTP API
- Produces: exporter-start and first-native-span evidence before broader test traffic

- [ ] Inspect the gateway journal from the restart timestamp for `diagnostics-otel`, OTLP, exporter-health, startup failure, and rejection messages.
- [ ] Record the exporter-started trace diagnostic and require `endpointMode: "configured"`; do not accept `default_endpoint`.
- [ ] Poll Tempo for `resource.service.name = "openclaw"` over the post-restart window until at least one native trace appears or a bounded timeout expires.
- [ ] Retrieve the full first native trace from `/api/traces/<traceID>` and record resource identity, instrumentation scope, span names, trace/parent IDs, session attributes, and model/token fields.
- [ ] Run a minimal tagged gateway turn with `openclaw agent --agent coder --session-key agent:coder:issue-285-smoke --message ... --json` and confirm the model response succeeds.
- [ ] Confirm bridge spans continue arriving for the smoke window and record both native and bridge trace IDs.
- [ ] If telemetry export fails while the model turn succeeds, record fail-open as passing but stop before the parity matrix to diagnose export; if model execution regresses, roll back immediately.
- [ ] Commit the startup and smoke evidence with `docs(proxy): record native OTel startup evidence (#285)`.

### Task 4: Execute the Provider and Session Parity Matrix

**Files:**
- Modify: `docs/openclaw-native-otel-parity.md`

**Interfaces:**
- Consumes: OpenClaw CLI test turns, cron/isolated/subagent interfaces available on the host, Tempo trace JSON
- Produces: observed pass/fail/blocked evidence for every #285 parity row

- [ ] Establish a unique `issue-285-<scenario>-<timestamp>` label/session key before each workload and record the exact start/end epoch window.
- [ ] Run direct Anthropic through agent `thinker` (`anthropic/claude-opus-4-6`) and direct Gemini through agent `researcher` (`google/gemini-2.5-pro`); run a direct OpenAI API-key path only if one is configured distinctly from Codex OAuth.
- [ ] Run Codex ChatGPT OAuth through agent `coder` and retrieve every native `model.call` span plus its expected turn/harness parent from Tempo.
- [ ] Run or trigger one existing cron/isolated flow without altering its production schedule; if none is safely invokable, use an isolated headless `openclaw agent exec` turn and document the substitution.
- [ ] Exercise a resumed session and a compaction/recovery path using a disposable issue-285 session; do not mutate a production conversation merely to force compaction.
- [ ] Exercise one ACP/native-subagent path through an existing configured interface; if unavailable, record the exact configuration evidence that blocks the row.
- [ ] Launch two disposable sessions concurrently, retrieve both trace sets, and assert that their session identifiers do not collapse.
- [ ] For every row, retrieve full trace JSON and record trace IDs, relevant span IDs, parent span IDs, timestamps, result, and caveats in the report.
- [ ] Mark unavailable scenarios `blocked` rather than `pass`; never infer parity from another provider or path.

### Task 5: Complete the Field Inventory and Codex Decision

**Files:**
- Modify: `docs/openclaw-native-otel-parity.md`

**Interfaces:**
- Consumes: full native and bridge trace payloads from Tasks 3–4
- Produces: the explicit migration evidence required by #285 and inputs for #280/#286/#287

- [ ] Build a native-versus-bridge table covering service/scope, trace relationships, session/run/call identity, agent/project identity, provider/model/operation/outcome, all token categories, latency/TTFB, tools/subagents, content/redaction, cost, and AgentWeave-derived fields.
- [ ] Trace how `openclaw.sessionKey` or its actual replacement lands on exported spans and state whether session grouping is sufficient.
- [ ] For Codex, compare each model span's trace ID and parent span ID with the expected turn/harness span; state `connected` or `orphaned` with IDs and no ambiguity.
- [ ] State whether `codexModelCallTraceparent` appears removable, remains load-bearing, or cannot yet be decided because of a named blocked condition.
- [ ] Summarize which fields native supplies, which only the bridge supplies, and which #286 must map into `prov.*`.
- [ ] Document duplicate-span behavior without changing deduplication.
- [ ] Re-run `scripts/verify.sh` and the relevant trace-quality gate against the final observation window.
- [ ] Review all evidence for prompt content, credentials, and personal data; redact before committing.
- [ ] Commit the completed report with `docs(proxy): complete OpenClaw native OTel parity report (#285)`.

### Task 6: Repository Verification, Deployment Gate, and Handoff

**Files:**
- Verify: `docs/openclaw-native-otel-parity.md`
- Verify: `docs/superpowers/specs/2026-09-12-openclaw-native-otel-parity-design.md`
- Verify: `docs/superpowers/plans/2026-09-12-openclaw-native-otel-parity.md`

**Interfaces:**
- Consumes: completed repository artifacts and live evidence
- Produces: pushed branch, PR, issue comment, and post-deploy verification status without merging

- [ ] Run `git diff --check`, scan tracked changes, and verify no machine-local backup/config files are staged.
- [ ] Run documentation/repository tests applicable to the changed files; record exact commands and results.
- [ ] Run `scripts/deploy.sh`; if it fails, stop and record logs rather than claiming the issue complete.
- [ ] Run `scripts/verify.sh`; if it fails, stop and record logs rather than claiming the issue complete.
- [ ] Push `fix/issue-285` and open a PR referencing #285 with the rollout result, parity summary, Codex conclusion, test/deploy evidence, and rollback status.
- [ ] Comment on #285 with what changed, caveats/blocked matrix rows, and the PR link.
- [ ] Do not merge. Issue closure remains post-merge and requires Nix to rerun deploy/verify and link the Grafana dashboard.
