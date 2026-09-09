# OpenClaw native OpenTelemetry architecture

This document defines the target integration for OpenClaw. It supersedes the
proxy-first guidance in `openclaw-integration.md`: native OpenTelemetry and the
OpenClaw bridge are the primary observability path; the AgentWeave provider
proxy is optional enrichment and fallback.

## Data flow and ownership

```
OpenClaw neutral lifecycle/model events
  └─ native OTel spans + W3C trace context/baggage
       └─ AgentWeave bridge: provenance mapping
            └─ AgentWeave normalizer/collector: validation, pricing, dedup
                 ├─ Tempo / dashboard
                 └─ optional provider proxy spans (enrichment only)
```

OpenClaw owns neutral event names, `executionId`, `contextId`, model metadata,
and W3C propagation. It does not emit `prov.*` names or inspect prompt/request
bodies. The bridge maps trusted diagnostic events to AgentWeave provenance; the
normalizer/collector owns cross-source normalization, cost calculation, and
deduplication. The proxy remains fail-open: model execution must continue when
the proxy, bridge, collector, or OTLP endpoint is unavailable.

## Identity contract

The bridge accepts trusted event fields, including fields nested under
`raw_data`/`rawData` during the staged OpenClaw rollout:

| OpenClaw field | AgentWeave field | Meaning |
|---|---|---|
| `contextId` | `session.id`, `prov.session.id`, `prov.openclaw.context.id` | Stable conversation/run grouping; preferred session identity. |
| `executionId` | `prov.openclaw.execution.id` | One attempt/turn; never used as a static fallback. |
| `parentContextId` | `prov.parent.session.id` | Delegating context/session. |
| `parentExecutionId` | `prov.parent.execution.id` | Delegating attempt. |
| `sessionId` | `prov.session.uuid` (and session fallback) | Legacy canonical transcript ID when native context is absent. |
| `sessionKey` | `prov.session.key` | Route/debug correlation only, never the preferred identity. |

`contextId` is preferred over `sessionId`, and `sessionId` is preferred over
`sessionKey`. If none is present, the bridge retains the unique `sessionKey` as
the compatibility fallback. It never assigns a shared static identity such as
`nix-main`. Model events are matched by `executionId` before session keys, so a
late completion cannot attach to a concurrently active session merely because
its route data is incomplete.

Agent IDs/types are bridge configuration or trusted upstream context, not
request-body-derived values. W3C `traceparent` carries causal trace parenting;
OTel baggage may carry small, non-sensitive routing context only. AgentWeave
specific mapping remains confined to the bridge, normalizer, and collector.

## Capability parity

| Path | Native bridge coverage | Optional proxy enrichment |
|---|---|---|
| Direct Anthropic, OpenAI, Gemini | Lifecycle, model identity, native usage/latency when emitted | Provider response usage and provider-specific pricing. |
| Codex ChatGPT OAuth | Lifecycle, Codex diagnostics, W3C parenting; works when proxy is bypassed | None required; OAuth must not be routed through a custom proxy. |
| Cron / isolated runs | `contextId`/`executionId`, task label, lifecycle span | Provider usage where routing is available. |
| Compaction / recovery / resumed sessions | New `executionId` with preserved `contextId` and parent execution link | Provider transport timing only. |
| ACP / native subagents | Parent context/execution relationships and independent agent-turn span | Per-provider request/response details. |

Fields available from each path are intentionally distinct. Native events can
supply neutral lifecycle, execution identity, model/provider, status, latency,
and any usage/cache values emitted by OpenClaw. Proxy spans can supply direct
provider model IDs, response token/cache values, transport latency, and
provider-priced cost. The normalizer computes cost only from a trusted model
and token tuple; unknown pricing stays explicitly unknown rather than zero.

## Deduplication

Native and proxy spans may describe the same model call. The collector keys a
candidate pair by trace identity when available, otherwise by
`prov.openclaw.execution.id` plus provider/model and an overlapping time
window. Native lifecycle/agent-turn spans are always retained. For duplicate
model calls, native data is authoritative for topology and identity; the proxy
may fill absent provider response fields. Exactly one span contributes tokens
and cost to aggregate `llm_call` metrics, marked with `prov.source` as
`native` or `proxy`. If correlation is uncertain, retain both spans but mark
them unaggregated rather than guessing and silently losing telemetry.

## Migration and rollback

1. Deploy bridge support for canonical IDs and export native OTel alongside the
   existing bridge/proxy path.
2. Run the parity matrix with concurrent main, cron, ACP/subagent, compaction,
   recovery/resume, direct-provider, and Codex OAuth runs. Assert distinct
   contexts remain distinct and verify a late/finished model span by
   `executionId`.
3. Enable collector deduplication and compare session count, token totals,
   costs, and parent relationships against the legacy path.
4. Stop sending `x-agentweave-session-key`; leave the proxy enabled only for
   selected direct-provider enrichment.
5. Remove the header plumbing after the parity evidence is retained.

Rollback is configuration-only: re-enable the keyed proxy enrichment and keep
native export on. No model request depends on AgentWeave headers, the bridge,
or collector availability, so observability failures cannot block execution.
