# OpenClaw Native Collector Mapping Design (#286)

## Decision and scope

The OpenTelemetry Collector enriches native OpenClaw model-call spans with
AgentWeave provenance fields. This is a declarative OTLP adapter at the ingest
boundary, not a change to OpenClaw or to the AgentWeave proxy. The adapter
preserves source attributes and applies only when `service.name=openclaw` and
`span.name=openclaw.model.call`.

The current native export contains no session, run, or call identity. It also
does not traverse AgentWeave's Claude Code cost normalizer. Therefore this
change does **not** emit `prov.session.*`, `cost.usd`, or a zero-valued cost.
Session correlation and native cost attribution require separate designs and
issues before they can be promised as parity. Static `prov.agent.id=nix-v1` and
`prov.project=nix` already arrive as resource attributes from the gateway;
the adapter must not invent per-session identity from them.

## Input contract and mapping

The input contract is the live native `openclaw.model.call` span observed in
[#285's parity report](../../openclaw-native-otel-parity.md), not fields merely
declared in an exporter constant table. In particular, the observed provider
source is `openclaw.provider`, not `gen_ai.provider.name`.

| Native span attribute | Added AgentWeave span attribute | Rule |
|---|---|---|
| `openclaw.provider` | `prov.llm.provider` | Copy when present; do not overwrite an existing target. |
| `gen_ai.request.model` | `prov.llm.model` | Copy when present; fallback to `openclaw.model` only when absent. |
| `gen_ai.usage.input_tokens` | `prov.llm.prompt_tokens` | Use the actual OpenClaw export convention: input plus cache-read plus cache-creation buckets when present, with absent buckets treated as zero. Do not add cache buckets if a fixture shows the input value already includes them. |
| `gen_ai.usage.output_tokens` | `prov.llm.completion_tokens` | Copy when present. |
| `gen_ai.usage.cache_read.input_tokens` | `tokens.cache_read` | Copy when present. |
| `gen_ai.usage.cache_creation.input_tokens` | `tokens.cache_write` | Copy when present. |

The prompt-token rule has a verification gate: the captured OpenClaw fixture
must show whether `gen_ai.usage.input_tokens` is uncached input or already
includes cache buckets. OpenTelemetry's semantic convention says it *should*
include cached tokens, but #285's observed counts suggest OpenClaw may export
an uncached input count. The implementation must pin the observed behavior
with a numerical test before choosing the expression; it must not silently
double-count cache. `prov.llm.total_tokens` is emitted only if the observed
input/output/cache convention yields an unambiguous total.

The adapter adds `prov.harness=openclaw` and `prov.source=native` to the model
span. It does not add `prov.activity.type=llm_call`: a same-trace proxy child
can already represent the billed LLM call, and adding that classification
would risk duplicate call counts. Native `openclaw.model.usage` is also excluded
because it can duplicate the call's usage and sometimes appears as a separate
root.

## Data flow and failure behavior

The existing traces pipeline remains `otlp receiver → memory limiter → PII
stripper → OpenClaw content stripper → mapping transform → batch → Tempo`.
The content stripper remains ahead of export. The transform uses conditional
statements that skip missing or malformed source values. It does not delete
source attributes, touch the proxy/bridge or other services, or block model
execution. A collector configuration parse failure must fail the deployment
gate rather than leave an apparently healthy but unconfigured collector.

This is the reusable adapter pattern: select one source service and span
contract, then map observed vendor attributes to shared `prov.*` fields in
the collector. A second runtime gets its own selected transform block and
fixture, not a patch inside that runtime.

## Verification

1. Build an OTLP fixture from the real native attribute contract, with
   synthetic values only. Include a cached model call, a no-cache model call,
   a native usage span, an unrelated service span, and an existing-target case.
2. Run the pinned Collector Contrib `0.126.0` configuration against the fixture
   locally. Assert exact mapped values and absence of session/cost/activity
   fields; assert unchanged source fields, untouched unrelated spans, and
   continued content stripping.
3. Run repository tests and a collector config validation before PR creation.
4. After merge, run `scripts/deploy.sh` and `scripts/verify.sh`, then send a
   disposable OTLP probe and query Tempo for exact mapped attributes. Close
   #286 only after both gates and the live assertion pass.

## Out of scope

- Exporting OpenClaw session identity or correlating native and bridge roots.
- Native cost computation, pricing policy, or deduplication of proxy charges.
- Removing the bridge, Codex traceparent carry, or proxy routing.
- The separate path-agnostic telemetry health canary in #282.
