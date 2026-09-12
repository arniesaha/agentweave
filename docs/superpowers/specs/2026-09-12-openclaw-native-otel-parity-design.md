# OpenClaw Native OTel Parity Rollout — Design

**Issue:** [#285](https://github.com/arniesaha/agentweave/issues/285)  
**Date:** 2026-09-12  
**Status:** Approved for specification review

## Context

OpenClaw includes the bundled `diagnostics-otel` exporter, but the live gateway does not load it.
The current configuration enables `diagnostics.otel` without registering the plugin or setting an
endpoint, so the custom AgentWeave bridge remains the only active OpenClaw telemetry path.

Issue #285 is a measurement phase. It enables native OTLP export alongside the bridge and preserves
the current model request path. Duplicate spans are expected until later migration work establishes
deduplication and source authority.

## Goals

- Load the bundled exporter and send native traces to the existing AgentWeave collector.
- Keep the bridge and proxy unchanged and fail-open.
- Supply the deployment's static agent and project identity as OTel resource attributes.
- Compare native and bridge telemetry on the same representative turns.
- Establish whether Codex ChatGPT OAuth model-call spans remain connected without relying on the
  fork's `codexModelCallTraceparent` carry.
- Record reproducible, attribute-level evidence for every row in the parity matrix.

## Non-goals

- Removing the bridge, proxy, fork carries, or duplicate spans.
- Adding collector-side `openclaw.*` to `prov.*` mappings; that is #286.
- Changing model routing or execution behavior; that is #287.
- Declaring the native path authoritative before parity is measured.
- Modifying the OpenClaw exporter implementation.

## Architecture

Both paths run concurrently and terminate at the same collector:

```text
OpenClaw diagnostic bus
  ├─ bundled diagnostics-otel ── OTLP/HTTP ──┐
  └─ AgentWeave bridge       ── OTLP/HTTP ──┴─> AgentWeave collector ─> Tempo

OpenClaw model request ── existing proxy/routing path (unchanged)
```

The live OpenClaw configuration registers `diagnostics-otel` and sets
`diagnostics.otel.endpoint` to `http://10.43.221.47:4318`. The plugin has an empty plugin-specific
schema; exporter settings remain under `diagnostics.otel`.

`OTEL_RESOURCE_ATTRIBUTES` supplies `prov.agent.id=nix-v1,prov.project=nix`. The exporter runs the
OpenTelemetry environment resource detector and merges those attributes with its configured
`service.name`. This replaces no event attributes during the parity phase.

## Repository and Operational Artifacts

The AgentWeave branch adds a parity runbook/report that contains:

- the exact enablement, verification, and rollback procedure;
- safe Tempo queries and expected native/bridge discriminators;
- one evidence row per parity scenario;
- a field-level native-versus-bridge inventory;
- the explicit Codex-parenting conclusion;
- timestamps and trace IDs sufficient to reproduce each observation.

The OpenClaw configuration and user-service environment are machine-local operational state. They
are not copied into the repository because the full files may contain credentials. Only the relevant
redacted fragments and observed results enter the report.

## Rollout

1. Capture redacted pre-change configuration, service state, and recent exporter logs.
2. Validate collector reachability from the gateway host.
3. Back up the live OpenClaw configuration and user-service environment using timestamped,
   owner-readable files.
4. Add the `diagnostics-otel` plugin entry, configured collector endpoint, and static resource
   attributes without altering the bridge entry or proxy settings.
5. Validate the resulting JSON before replacing the live file.
6. Restart the gateway once; immediately check service health and exporter diagnostics.
7. Require an exporter-started event for traces with `endpointMode: "configured"`.
8. Require native spans in Tempo before generating the full parity workload.
9. Exercise and document the parity matrix.

The gateway currently owns active Codex child processes, so the restart is treated as a scheduled,
user-approved interruption rather than an incidental configuration reload.

## Parity Matrix

Each scenario uses a timestamped test label and records native and bridge trace/span IDs:

| Scenario | Required observation |
|---|---|
| Direct Anthropic / OpenAI / Gemini | Model, provider, token usage, and connected turn tree |
| Codex ChatGPT OAuth | Model-call span is connected or demonstrably orphaned |
| Cron / isolated run | Stable session grouping across both paths |
| Compaction / recovery / resumed | Attribution survives the transition |
| ACP / native subagent | Parent-to-child agent lineage is represented |
| Concurrent sessions | Sessions remain distinct without identity collapse |

An unavailable provider or host feature is recorded as blocked evidence, not silently marked as a
pass. No row is inferred from source code alone.

## Evidence and Field Comparison

For each observed span set, the report compares at least:

- service name and instrumentation scope;
- trace ID, span ID, parent span ID, and span name;
- session ID/key and run/call identifiers where present;
- agent/project identity;
- provider, requested/response model, operation, and outcome;
- input, output, cache-read, cache-write, reasoning, prompt, and total tokens;
- duration and time-to-first-byte;
- tool and subagent lineage;
- content capture behavior and redaction;
- bridge-only cost or derived attributes.

Queries assert attribute values, not merely the presence of traces or successful HTTP responses.

## Failure Handling and Rollback

Native telemetry must fail open: exporter startup or delivery failure must not change model execution.
If gateway startup or model execution regresses, restore the timestamped configuration/environment
backups and restart the gateway. If only export fails, preserve the bridge, capture exporter-health
evidence, and either correct a proven configuration error or roll back native enablement.

Rollback success requires the gateway to be healthy and bridge spans to continue arriving. Native
span absence after rollback is expected.

## Testing and Verification

- Validate edited JSON before installation and re-read only the redacted relevant fragment.
- Confirm gateway active state and unchanged model routing after restart.
- Confirm `diagnostics-otel` reports trace export with `endpointMode: "configured"`.
- Query Tempo for native spans alongside bridge spans.
- Run a basic model request immediately after startup as the fail-open smoke test.
- Complete the parity matrix and field inventory with trace-level evidence.
- Run repository tests relevant to changed documentation or helper code.
- Run `scripts/deploy.sh` and `scripts/verify.sh` before issue closure, as required by the repository
  definition of done.

## Follow-up Decisions

The report feeds later issues without implementing them:

- #286 owns collector-side vocabulary mapping.
- #287 owns removal of the proxy from OpenClaw's default model path.
- #280 owns deduplication and retirement criteria.
- A connected Codex result permits retirement analysis of `codexModelCallTraceparent`; an orphaned
  result makes that carry a candidate for focused upstreaming.

