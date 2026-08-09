# Native OTel Normalization — Design

**Issue:** [#249](https://github.com/arniesaha/agentweave/issues/249)
**Date:** 2026-08-07
**Status:** Approved, not yet implemented

## Context

Claude Code ships first-party OpenTelemetry tracing. With `CLAUDE_CODE_ENABLE_TELEMETRY=1` and `CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1` it emits a full span hierarchy — `claude_code.interaction` → `claude_code.llm_request` / `claude_code.tool` / `claude_code.tool.execution` / `claude_code.tool.blocked_on_user`.

Those spans already reach Tempo on the NAS (enabled in #253, which needed native tracing on for the `traceparent` that joins proxy spans to the interaction trace). But the dashboard filters on `resource.service.name IN ("agentweave-proxy", "mux-router")` **and** `span.prov.activity.type = "llm_call"`, so native spans — `service.name = "claude-code"`, `gen_ai.*` attributes, no `prov.activity.type` — are invisible to every dashboard view despite being stored.

This design makes them legible.

### Why now

Anthropic shipping native tracing removes interception as AgentWeave's only data-collection route. What stays defensible is downstream: cross-harness normalization into one provenance model, cost attribution, and analysis. This work is the pivot from "AgentWeave collects" to "AgentWeave normalizes and explains".

### Scope decision

Two outcomes were considered and both accepted, in order:

- **B — Depth (this design).** Get tool-level and subagent structure into the dashboard for agents already on the proxy. The proxy stays authoritative for LLM data.
- **C — Migration (follow-up).** Native becomes the primary source; the proxy is reduced to a fallback for non-Claude-Code agents.

B is built so C is a switch, not a second migration. Coverage for agents that *can't* use the proxy (the Mac Mini, which loses Remote Control under `ANTHROPIC_BASE_URL`) falls out of B for free.

## Goals

- `claude_code.*` spans queryable through the same `prov.*` vocabulary as proxy and bridge spans.
- Tool-level detail — including human-in-the-loop wait time — visible in the dashboard.
- No double-counting of tokens or cost.
- C reachable by configuration, not by rewriting the mapping.

## Non-goals

- Removing or deprecating the proxy. B leaves it authoritative.
- Changing the OpenClaw bridge or its `openclaw.turn` spans.
- Fixing `openclaw.turn`'s flat single-span shape (remainder of #246).
- Any dashboard work beyond what B requires (below).

## Architecture

A separate `agentweave-normalizer` service:

```
Claude Code ──OTLP/JSON──> normalizer ──OTLP──> collector ──> Tempo
                                                (strip_pii)
proxy ─────────────────────OTLP──────────────> collector ──> Tempo
```

### Why a separate service rather than an endpoint on the proxy

- **The proxy is on the request hot path.** Telemetry ingest load must not be able to slow a live completion.
- **The proxy restarts often** — four times during the 2026-08-06/07 work alone. Every restart would drop spans in flight.
- **Blast radius.** A normalizer bug stops native spans; proxy telemetry is untouched. Bolted onto the proxy, a normalization bug is a proxy bug.

PII stripping stays at the collector, downstream of both paths, so it keeps applying regardless of what the normalizer does.

### Why a code path rather than a collector OTTL transform

OTTL can map attributes but cannot do lookups, so it cannot compute cost from tokens. B needs only mapping and would be cheaper in OTTL — but C needs cost, and building OTTL rules for B only to delete them at C is the second migration this design exists to avoid.

### Transport

Set `OTEL_EXPORTER_OTLP_PROTOCOL=http/json` on the client so the normalizer receives OTLP-JSON, parseable with the standard library — no protobuf codegen, no `opentelemetry-proto` dependency.

**This is unverified.** Claude Code documents `http/json` as a supported value but it has not been exercised end-to-end. Confirming it is implementation task 1; if it fails, fall back to `http/protobuf` and take the protobuf dependency.

## Mapping

Every normalized span gets `prov.harness = "claude-code"`. `prov.agent.id` and `prov.project` already arrive via `OTEL_RESOURCE_ATTRIBUTES` (set on the NAS in #253) and are passed through unchanged.

| Native span | `prov.activity.type` | Carried across |
|---|---|---|
| `claude_code.interaction` | `agent_turn` | `session.id` → `prov.session.id`; `interaction.sequence` → `prov.session.turn` |
| `claude_code.tool` | `tool_call` | `tool_name` → `prov.tool.name`; `tool_use_id` → `prov.tool.call_id` |
| `claude_code.tool.execution` | `tool_execution` | `success` → `outcome`; `duration_ms` |
| `claude_code.tool.blocked_on_user` | `permission_wait` | `decision`, `source` |
| `claude_code.llm_request` | **unset under B** | see Dedup below |

`claude_code.tool.blocked_on_user` measures how long the agent waited on a human decision. Neither the proxy nor the bridge can observe this today; it is new capability, not a re-expression of existing data.

### Token semantics — the correctness trap

Native and proxy count input tokens differently:

```
native:  input_tokens = 2        cache_read_tokens = 53673
proxy:   prov.llm.prompt_tokens = 53675
```

Native reports the **uncached** portion separately; the proxy reports the **sum**. The normalizer must compute:

```
prov.llm.prompt_tokens = input_tokens + cache_read_tokens
```

Mapping `input_tokens` straight to `prov.llm.prompt_tokens` would under-report by ~26,000× on a cache-heavy turn. This is the single highest-risk detail in the mapping and gets a dedicated test.

Also carried: `cache_creation_tokens` → `tokens.cache_write`, `cache_read_tokens` → `tokens.cache_read`, `output_tokens` → `prov.llm.completion_tokens`, `gen_ai.request.model` → `prov.llm.model`, `stop_reason` → `prov.llm.stop_reason`, `ttft_ms`.

## Dedup and the B→C switch

`claude_code.llm_request` and the proxy's `llm.*` span describe the **same model call**. `tempoSearchQuery` aggregates on `span.prov.activity.type = "llm_call"`, so giving the native span that value would double every token and dollar in the Overview.

Under B, `claude_code.llm_request` is normalized (attributes, tokens, harness) but **does not** receive `prov.activity.type = "llm_call"`. The proxy stays the sole source of `llm_call`.

Spans are tagged with their origin so C can switch on it:

- The normalizer sets `prov.source = "native"` on everything it emits.
- The proxy sets `prov.source = "proxy"` on its LLM spans — a one-line change, included in B so the switch is symmetric rather than "native is tagged, proxy is inferred from `service.name`".

C then flips which source claims `llm_call` and drops the other. No re-mapping, no different component.

Cost is computed in the normalizer via `pricing.py`. This depends on #256 (merged) adding the Claude 5 rows — without it, native spans would price at `-1` and C would look like a regression.

## Dashboard change

`TEMPO_SERVICE_FILTER` in `dashboard/src/lib/queries.ts` is currently `("agentweave-proxy", "mux-router")`. Widen it to include `"claude-code"`.

The normalizer **preserves** `service.name = "claude-code"` rather than masquerading as `agentweave-proxy`. Rewriting it would destroy the provenance C depends on and make a normalization bug indistinguishable from a proxy bug.

## Testing

- **Unit — mapping.** A recorded native OTLP-JSON payload (captured from a real session) in, normalized spans out. One test per row of the mapping table.
- **Unit — token arithmetic.** Explicit test that `prompt_tokens == input_tokens + cache_read_tokens`, using the observed `2 + 53673 = 53675` case.
- **Unit — no double count.** Assert `claude_code.llm_request` does not receive `prov.activity.type = "llm_call"` under B.
- **Unit — cost.** Native `claude-opus-5` request prices via `pricing.py` and is not `UNKNOWN_COST`.
- **E2E.** Run a tool-using `claude -p` on the NAS, then assert in Tempo that one trace contains the interaction, its tool spans, and the proxy LLM span, with cost counted exactly once.

Assert on emitted spans, never on HTTP status — the defect class that produced #246/#247/#248 was tests that checked status codes and never looked at span contents.

## Rollout

1. Verify `http/json` export end-to-end (blocks everything else).
2. Build the normalizer with the mapping and tests; no deployment.
3. Deploy the normalizer with **no traffic pointed at it**. Validate by replaying a captured native payload through the running service and diffing its output against the unit-test expectations — this checks packaging, config, and connectivity to the collector without putting live spans at risk.
4. Add `prov.source = "proxy"` to the proxy; widen the dashboard filter to include `"claude-code"`.
5. Point `OTEL_EXPORTER_OTLP_ENDPOINT` on the NAS at the normalizer **and set
   `OTEL_EXPORTER_OTLP_PROTOCOL=http/json`**. Native spans now arrive
   normalized instead of raw.

   Both variables, not just the endpoint. The NAS was on `http/protobuf` from
   #253, and repointing the endpoint alone produced a 415 from the normalizer
   — spans rejected rather than normalized. The 415 named the cause
   immediately, but the step should not have needed it.

Rollback is repointing that one env var back at the collector — native spans revert to raw-but-stored, which is today's behaviour.

Note that steps 3 and 5 are exclusive for a given client: the endpoint env var has one value, so a span either goes direct to the collector or through the normalizer, never both. Side-by-side comparison of live traffic would need a second client or a collector fan-out, and is deliberately not part of this plan.

## Risks

| Risk | Mitigation |
|---|---|
| `http/json` unsupported in practice | Task 1 verifies before anything is built; fall back to protobuf |
| Token-semantics mismatch mis-costs spans | Dedicated test with the observed values |
| Normalizer restart drops spans in flight | Isolated from the proxy; native path only; proxy telemetry unaffected |
| New k8s component to run | Accepted — the cost of not doing this twice |
| Hardcoded collector ClusterIP (`10.43.221.47`) inherited from #253 | Pre-existing; a NodePort would be sturdier. Out of scope, tracked separately |

## Open questions

- Does `OTEL_EXPORTER_OTLP_PROTOCOL=http/json` work end-to-end with Claude Code? (Task 1.)
- Should the normalizer own cost for **all** sources eventually, or only native? B does native only; C should revisit.

## References

- #249 — parent issue
- #253 — enabled native tracing; established the `traceparent` join
- #256 — Claude 5 pricing, prerequisite for cost in C
- #255 — remove the process-global session context (unrelated, but touches the same proxy attribution path)
