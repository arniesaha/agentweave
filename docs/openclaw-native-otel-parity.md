# OpenClaw Native OTel Parity Report

**Issue:** [#285](https://github.com/arniesaha/agentweave/issues/285)
**Observation started:** 2026-09-12T11:59:08-07:00
**Status:** Native trace exporter active; observed parity and limits recorded

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
had no active `openclaw.turn` span and logged that its usage lookup found no active span. A later
normal-ingress probe supplies the missing bridge-turn evidence below.

### Normal-ingress bridge comparison

At approximately 2026-09-12 13:18 PDT, `chat.send` created the disposable session
`agent:coder:issue-285-chat-ingress` with no external delivery. `chat.history` confirmed one user
and one assistant message. The bridge exported `openclaw.turn` in trace
`a4009ed2856bff7d52c302afc20c9069`, span `bb3165ef344f488f`; that span carries the
disposable key as `session.id`/`prov.session.id` plus `prov.agent.id=nix-v1` and
`prov.project=nix`. The native exporter produced trace
`cacd52ab1cfeb61df4511e44f2631635` over the same 8.2-second window:

```text
openclaw.message.processed
└─ openclaw.harness.run
   ├─ openclaw.run
   │  └─ openclaw.model.call
   │     └─ agentweave-proxy llm.gpt-5.5
   └─ openclaw.model.usage
```

Here the native usage span **is** parented under the harness, unlike the CLI RPC usage root.
The bridge turn and native tree have separate trace IDs; the turn timestamp and synthetic
session establish side-by-side path activity, not a single connected cross-path tree.

## Parity Matrix

All trace IDs below are Tempo IDs from the observation window. Synthetic prompts requested only an
exact marker response; captured prompt and response bodies are intentionally omitted.

| Scenario | Native evidence | Bridge/proxy evidence | Result | Caveat |
|---|---|---|---|---|
| Direct Anthropic | `78a4e7da405115ce91958d520ba84fb4`: harness → run → model.call, 5,347 ms, `claude-opus-4-6`; separate usage root `8624bbfbcdb2b9b222de72d202291dda` | `llm.claude-opus-4-6` is a child of native model.call in the same trace | Observed | CLI RPC turn did not create `openclaw.turn` |
| Direct OpenAI API | None | None | Blocked | No distinct API-key route was established; Codex OAuth is not evidence for the API-key path |
| Direct Gemini | `839affbf5f058073407de9397097fadf`: harness → run → model.call, 2,970 ms, `gemini-2.5-pro`; separate usage root `bfe3e2c31c6b7ae337d8f928b1bb1934` | No proxy child: this request used the direct Gemini route | Observed native only | First attempt rejected unsupported `minimal` thinking before a model call; rerun with thinking off succeeded |
| Normal chat ingress | `cacd52ab1cfeb61df4511e44f2631635`: message.processed → harness → run → model.call → proxy child; usage under harness | `openclaw.turn` span `bb3165ef344f488f` in `a4009ed2856bff7d52c302afc20c9069` with the disposable session key | Both paths observed | Separate traces; native spans lack exported session key |
| Codex ChatGPT OAuth | `37568843a5fb88287b0fc9d363ce7b7a`: harness `90572f6686f15367` → run `750e10db11d9ae0e` → model.call `f6b0f7241a7be9fa`; usage root `e90807c5522f5f090c1e6f6e8aa8e41b` | Proxy LLM span `a093f5ca87e09708` is a child of model.call in the same trace | Connected in current fork | Does not prove fork-specific Codex traceparent carry is removable |
| Cron / isolated run | Headless `agent exec` synthetic turn returned `ok` after default-model fallback, but no corresponding native trace was found in the gateway exporter window | No attributable bridge turn | Unproven | A separate headless process is not the gateway exporter; existing cron jobs were not changed or manually run |
| Resumed session | Second turn in `agent:coder:issue-285-smoke` reused session UUID `4dc5de4e-7497-4aa0-8ee9-64c16014b134`; new lifecycle trace `49282f0ddd021ab2813f042a92d1f99d`, usage root `be48c2b7c187ae8384637df7c8a0731a` | CLI RPC did not create a bridge turn | Observed native only | Native exported spans cannot be grouped by that session UUID; compaction/recovery was not forced |
| ACP / native subagent | None | None | Blocked | No configured ACP binding or safe disposable native-subagent entry point was established |
| Concurrent sessions | `e0b6b6f21851b9f8c2b63274bed214c8` (Codex) and `4a703e39c84cecb44721338ad86e62ae` (Anthropic) overlapped, completed with distinct run IDs and distinct native trace IDs | Each has a same-trace proxy LLM child of its own native model.call | Observed | Native spans omit session IDs; distinct sessions verified from command results, not span attributes |

The bridge remained enabled and exported a normal-ingress turn span. AgentWeave proxy spans
continued to be exported as native model-call children. **Bridge/native turn correlation is not
yet lossless** because the two trees have different trace IDs and the native export omits the
bridge's session key. Neither path was removed or deduplicated.

## Field Inventory

This comparison uses the Codex trace `37568843a5fb88287b0fc9d363ce7b7a`, Anthropic trace
`78a4e7da405115ce91958d520ba84fb4`, and direct Gemini trace
`839affbf5f058073407de9397097fadf`; it compares exported attributes, not schema promises.

| Field family | Native OpenClaw exporter | AgentWeave proxy/bridge path |
|---|---|---|
| Service and scope | `service.name=openclaw`, instrumentation scope `openclaw` | Proxy child has `service.name=agentweave-proxy`, scope `agentweave`; bridge turn scope is `openclaw-agentweave-bridge` |
| Trace and parent relationships | Connected harness → run → context/model tree; CLI usage is a separate root, while normal-ingress usage is under the harness | Codex and Anthropic proxy spans are children of native model.call; bridge `openclaw.turn` is in a separate trace from the normal-ingress native tree |
| Session, run, and call identity | No exported `openclaw.sessionKey`, session ID, run ID, or call ID on observed spans | Bridge turn has the exact disposable `session.id`/`prov.session.id`; CLI proxy has a generic `nix-main` session unrelated to the disposable OpenClaw UUID |
| Agent and project identity | Static `prov.agent.id=nix-v1`, `prov.project=nix` on the resource via `OTEL_RESOURCE_ATTRIBUTES` | Proxy has `prov.agent.id`, `prov.project`, `prov.agent.type`, and `prov.cwd` as span attributes |
| Provider, model, operation, outcome | `openclaw.provider`, `openclaw.model`, `gen_ai.*` request/operation fields; `openclaw.outcome` on run/harness | `prov.llm.provider`, `prov.llm.model`, `prov.activity.type`, `gen_ai.*`, stop reason on observed Anthropic proxy child |
| Token categories | Input, output, cache-read, cache-creation, reasoning, prompt, total fields on model.call when supplied; duplicate usage-root observation | Anthropic proxy has prompt/completion/total and `tokens.cache_read`/`tokens.cache_write`; not all categories are identical |
| Duration and TTFB | Span duration and `openclaw.model_call.time_to_first_byte_ms` | Proxy has `agentweave.latency_ms`; no TTFB on observed proxy child |
| Tool and subagent lineage | Not exercised in this matrix; native recorder exists but trace-level sufficiency unproven | Not exercised; bridge lineage sufficiency unproven |
| Content and redaction | With existing `captureContent=true`, native model.call exported input/output and tool definitions, including a provider diagnostic stack. This is a sensitive-data risk. | Anthropic proxy child exported prompt/response previews and Langfuse input/output; contents excluded from this report |
| Cost and derived fields | No `cost.usd`, `prov.llm.*`, or AgentWeave-derived cost on observed native model spans | Anthropic proxy child has `cost.usd`, `cache.hit_rate`, and AgentWeave/Provenance fields |

`openclaw.sessionKey` is **not a session grouping field in the native export**. In OpenClaw
`extensions/diagnostics-otel/src/service-constants.ts`, it is in
`DROPPED_OTEL_ATTRIBUTE_KEYS` alongside raw session/run/call IDs. In
`extensions/diagnostics-otel/src/service-traces.ts`, `addRunAttrs` accepts `sessionKey`,
`sessionId`, and `runId` but writes only provider/model/channel/trigger. The resumed turn's same
session UUID therefore cannot be recovered from the exported native spans. #286 needs an explicit,
privacy-reviewed correlation strategy rather than assuming the key already exports.

## Codex Parenting Decision

**Connected under the current fork, not yet safe to remove the carry.** In trace
`37568843a5fb88287b0fc9d363ce7b7a`, native `openclaw.model.call`
`f6b0f7241a7be9fa` has parent `openclaw.run` `750e10db11d9ae0e`, whose parent is
`openclaw.harness.run` `90572f6686f15367`. The AgentWeave proxy LLM span
`a093f5ca87e09708` is a child of that native model span in the same trace. A second Codex
concurrent turn had its proxy child in its own distinct native lifecycle trace.

The fork's `codexModelCallTraceparent` transport remains enabled during both observations.
The traces prove that the deployed combination works, not that native OTel alone transports
the context across the Codex app-server boundary. No controlled no-carry A/B run was made;
removal is **undecidable from this evidence**. Keep that carry until an isolated no-carry
Codex test demonstrates the same model-to-proxy parent relationship. The orphan
`openclaw.model.usage` root is a separate diagnostic-event limitation, not an orphaned
`openclaw.model.call`.

## Rollback

The two owner-readable backups listed above are the pre-change rollback artifacts. No rollback
was performed: the gateway and model calls remained healthy, and the bridge configuration was
left in place. The native exporter can be disabled by restoring the backed-up OpenClaw config
and removing the dedicated OTel systemd drop-in, followed by a gateway restart.

## Verification and Deployment Gate

At 2026-09-12 13:15 PDT, `bash scripts/verify.sh` passed 4/4 checks: proxy health,
authenticated health, recent Tempo trace data, and non-zero LLM call metrics (230 in the queried
series). `python3 scripts/trace_quality_gate.py --tempo-url
http://192.168.1.70:31989 --range 1h --json` returned `warn` with 0 failures, 28 LLM
records, and 38 warnings. The warnings were missing AgentWeave `prov.llm` token and cost fields
on native `openclaw.harness.run` search results, not an exporter outage; the quality gate's
classification/mapping needs updating before native spans can satisfy its existing semantics.

Fresh repository suites on the issue worktree: Python 613 passed, TypeScript/Jest 51 passed,
Go 4 passed. The first Python run had 2 failures because an unrelated empty `/tmp/.git`
directory made the repository-detection tests identify `/tmp` as a repo; with
`TMPDIR=/var/tmp`, the full suite passed. No repository-detection code was changed.

The repository change is documentation only. `scripts/deploy.sh` would rebuild, push, and restart
the live AgentWeave proxy even though this branch changes no proxy or collector code. It was not
run before merge; the Nix post-merge checklist requires deploy plus verify, then issue closure
only if both pass. #285 is not deployment-complete while that gate and the blocked parity rows
remain open.

## Caveats

- The pre-change `openclaw` service-name query establishes absence of the native exporter path; it
  does not assert bridge content correctness.
- Provider or runtime scenarios unavailable on the live host will be marked blocked with concrete
  configuration evidence, never inferred as passing.
- The headless isolated probe used the default route after an explicit Anthropic override was
  rejected by the main agent's model policy; the default `gpt-5.6-sol` attempt fell back to
  `claude-sonnet-4-6`. Its `ok` result is not evidence of native gateway export.
- `captureContent=true` predates this change, but enabling native export now sends full captured
  model content to the collector. Review this separately before declaring native telemetry
  a replacement for the bridge.
