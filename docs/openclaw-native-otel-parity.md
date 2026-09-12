# OpenClaw Native OTel Parity Report

**Issue:** [#285](https://github.com/arniesaha/agentweave/issues/285)  
**Observation started:** 2026-09-12T11:59:08-07:00  
**Status:** Native trace exporter active; parity matrix in progress

## Environment

| Component | Value |
|---|---|
| AgentWeave branch | `fix/issue-285` |
| AgentWeave baseline commit | `92541cc02417b75c468ae473849959b530f2e0a7` |
| OpenClaw version | `2026.9.2` |
| OpenClaw source commit | `bf598e8bbe1df8585310220aeae660c8b0ce381c` |
| Gateway unit | `openclaw-gateway.service` |
| Collector OTLP endpoint | `http://10.43.221.47:4318` |
| Tempo query endpoint | `http://192.168.1.70:31989` |
| Static identity target | `prov.agent.id=nix-v1`, `prov.project=nix` |

The report contains only redacted configuration fragments and trace metadata. Full configuration,
environment files, credentials, and captured prompt content are excluded.

## Pre-change Baseline

Captured before native exporter registration or any gateway restart:

- Gateway was `active (running)` with PID `27972`, started
  `2026-09-11 12:49:26 PDT`.
- `diagnostics.otel` was `{enabled: true, traces: true, captureContent: true}` with no configured
  endpoint.
- `plugins.entries` contained an enabled `agentweave-bridge` pointing at
  `http://10.43.221.47:4318`, with `agentId=nix-v1` and `project=nix`.
- `plugins.entries` had no `diagnostics-otel` entry.
- An HTTP request to the collector's `/v1/traces` route returned `405`, proving host-to-collector
  network reachability without submitting a payload.
- Tempo returned zero traces for `resource.service.name = "openclaw"` over the preceding 24 hours.
- `scripts/verify.sh` passed 4/4 checks: proxy health, proxy authentication/recent activity, recent
  Tempo data, and non-zero Grafana/Prometheus LLM metrics (`3114` observed calls).

Repository test baseline:

| Suite | Result | Notes |
|---|---|---|
| Python | 613 passed | Full suite; required execution outside the filesystem/network sandbox because a TestClient request hung inside it |
| TypeScript | 51 passed | 2 Jest suites |
| Go | 4 passed | Used isolated Go caches under `/tmp` |

## Exporter Startup

The live configuration was changed at 2026-09-12 12:39 PDT after creating owner-readable backups:

- `/home/Arnab/.openclaw/backups/openclaw.json.issue-285.20260912T120009-0700`
- `/home/Arnab/.openclaw/backups/gateway.systemd.env.issue-285.20260912T120009-0700`

The bridge entry and proxy URL were left unchanged. `diagnostics.otel.endpoint` is now
`http://10.43.221.47:4318`, `plugins.entries["diagnostics-otel"]` is enabled, and a dedicated
systemd drop-in supplies `prov.agent.id=nix-v1,prov.project=nix` through
`OTEL_RESOURCE_ATTRIBUTES`.

One gateway restart completed successfully. The gateway moved from PID `27972` to PID `1713137`,
became ready at 12:39:40 PDT, and passed the native deep connectivity probe on OpenClaw 2026.9.2.

The read-only `gateway stability --type telemetry.exporter` snapshot reported:

- exporter: `diagnostics-otel`
- signal: `traces`
- outcome: `started`
- transport: `otlp-http-protobuf`
- mode/reason: `configured`

The first configuration also activated metrics because `diagnostics.otel.metrics` defaults to true.
The collector has only a traces pipeline, so the stability snapshot recorded a metrics
`export_failed` event after 60 seconds. Setting `diagnostics.otel.metrics=false` hot-reloaded the
plugin without changing the gateway PID. The replacement snapshot contains one healthy configured
traces route and no metrics failure.

Tempo returned 19 native `resource.service.name = "openclaw"` traces immediately after restart,
versus zero in the pre-change 24-hour query. Trace
`41e354b60f16fde79ad615a84a2937bf` proved the exporter resource carries `service.name=openclaw`,
`prov.agent.id=nix-v1`, and `prov.project=nix`; its instrumentation scope is `openclaw`.

## Fail-open Smoke Test

At 2026-09-12T12:52:50-07:00, a disposable Codex gateway turn used session key
`agent:coder:issue-285-smoke`. It completed successfully in 7,037 ms with the exact synthetic reply
requested, proving model execution remained healthy.

Native trace `37568843a5fb88287b0fc9d363ce7b7a` contains this connected hierarchy:

```text
openclaw.harness.run (span 90572f6686f15367)
└─ openclaw.run (span 750e10db11d9ae0e)
   ├─ openclaw.context.assembled (span f62a823596ce12f5)
   └─ openclaw.model.call (span f6b0f7241a7be9fa)
      └─ agentweave-proxy llm.gpt-5.5 (span a093f5ca87e09708)
```

The proxy span's `prov.trace.parent` names the native model span and its actual parent span ID and
trace ID match. Native model attributes include provider/model, OpenAI ChatGPT Responses API,
request/response sizes, TTFB, prompt statistics, and full input/output/cache/reasoning/total token
breakdown.

Native usage trace `e90807c5522f5f090c1e6f6e8aa8e41b` is a separate root
`openclaw.model.usage` span for the same 818 input, 13 output, 39,424 cache-read, and 40,255 total
tokens. Thus the lifecycle/model-call tree is connected, but the duplicate usage observation is not.

The bridge received the smoke turn's `session.state`, `model.call.*`, and `model.usage` events, but
the CLI-driven RPC path emitted no `message.queued`/`message.processed` pair. Consequently the bridge
had no active `openclaw.turn` span and logged that its usage lookup found no active span. A normal
message-ingress workload is still required to prove native spans alongside a bridge turn span.

## Parity Matrix

| Scenario | Native evidence | Bridge evidence | Result | Caveat |
|---|---|---|---|---|
| Direct Anthropic | Pending | Pending | Pending | — |
| Direct OpenAI API | Pending | Pending | Pending | Run only if distinct API-key routing is configured |
| Direct Gemini | Pending | Pending | Pending | — |
| Codex ChatGPT OAuth | Pending | Pending | Pending | Must answer connected versus orphaned |
| Cron / isolated run | Pending | Pending | Pending | — |
| Compaction / recovery / resumed | Pending | Pending | Pending | Use a disposable session |
| ACP / native subagent | Pending | Pending | Pending | — |
| Concurrent sessions | Pending | Pending | Pending | Must prove identities do not collapse |

## Field Inventory

| Field family | Native | Bridge | Evidence |
|---|---|---|---|
| Service and instrumentation scope | Pending | Pending | Pending |
| Trace and parent relationships | Pending | Pending | Pending |
| Session, run, and call identity | Pending | Pending | Pending |
| Agent and project identity | Pending | Pending | Pending |
| Provider, model, operation, outcome | Pending | Pending | Pending |
| Token categories | Pending | Pending | Pending |
| Duration and TTFB | Pending | Pending | Pending |
| Tool and subagent lineage | Pending | Pending | Pending |
| Content capture and redaction | Pending | Pending | Pending |
| Cost and AgentWeave-derived fields | Pending | Pending | Pending |

## Codex Parenting Decision

Pending trace-level evidence. The conclusion will name native model span IDs, their trace IDs and
parent span IDs, and the expected turn/harness span.

## Rollback

Rollback artifacts and outcome will be recorded after the pre-change machine-local backups exist.
If model execution regresses, native enablement will be removed/restored immediately while the
bridge remains enabled.

## Caveats

- The pre-change `openclaw` service-name query establishes absence of the native exporter path; it
  does not assert bridge content correctness.
- Provider or runtime scenarios unavailable on the live host will be marked blocked with concrete
  configuration evidence, never inferred as passing.
