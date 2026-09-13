# OpenClaw native OpenTelemetry integration

Native OpenClaw telemetry is the preferred observability path. The AgentWeave
bridge maps the diagnostic events that the installed OpenClaw runtime actually
exposes; the provider proxy remains optional enrichment. This is a migration
target, not a claim that native and bridge traces already have full parity.

## Observed diagnostic contract

OpenClaw's plugin-visible `model.call.started`, `model.call.completed`, and
`model.call.error` events carry `runId`, `callId`, provider, model, and optional
`sessionKey`/`sessionId`. A started event with an exact active session binds
its run and call IDs to that bridge turn. Later call events can match those IDs
when route fields are absent. A finished turn removes those bindings. If no
binding exists, a model call may match an exact active session key or an
unambiguous session ID. Unmatched events are left unattributed; the bridge
does not guess based on the latest subagent.

`model.usage` has optional `sessionKey`/`sessionId` but no run or call ID, so
it can only enrich an exact active session. The bridge records model/provider
on the turn span and retains usage/cost when `model.usage` provides them.
`model.call.completed` may include token usage, but this PR does not claim
cost or token parity from that event; collector-side normalization remains
separate work.

Lifecycle events use the canonical transcript `sessionId` when provided,
falling back to the qualified `sessionKey`. Trusted upstream
`agentweave.context.v1` may supply a separate session and explicit parent
identity. The bridge keeps the route key as `prov.session.key` for correlation.
OpenClaw's `contextId`, `executionId`, `parentContextId`, and
`parentExecutionId` belong to the default-off Execution Identity Audit
subsystem; they do not reach these plugin diagnostic events. The bridge does
not read them or infer them from `raw_data`/`rawData`.

## Ownership and parity boundaries

OpenClaw owns neutral events and native OTel export. The bridge owns
AgentWeave-specific `prov.*` mapping from plugin-visible diagnostics. The
normalizer/collector owns cross-source validation, pricing, and deduplication.
No AgentWeave component should be required for model execution.

Native-vs-bridge parity still needs live checks for direct providers, Codex
OAuth, cron/isolated runs, compaction/resume, and subagents. In particular,
verify that native model spans carry usable session grouping and trace
parenting before retiring the Codex traceparent carry. Do not remove the
proxy/header path until those checks and collector deduplication are proven.
If native export is unhealthy, keep the current bridge/proxy configuration;
telemetry failure must not block the model request.
