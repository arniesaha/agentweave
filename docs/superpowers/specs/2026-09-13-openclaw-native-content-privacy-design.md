# OpenClaw native content privacy (#291)

## Decision and scope

Disable native OpenClaw content capture at the source and strip native
content-bearing span attributes at the AgentWeave collector before any
exporter. This protects new native traces even if the gateway setting is
accidentally re-enabled. It does not rewrite historical Tempo data or change
the separate AgentWeave proxy and bridge preview policy.

The live evidence is a native `openclaw.model.call` span carrying
`gen_ai.input.messages`, `gen_ai.output.messages`,
`gen_ai.tool.definitions`, and `openclaw.content.*` keys. The gateway has
`diagnostics.otel.captureContent=true`; the collector currently strips only
five account-identity keys. No captured values belong in issues, tests, or
deployment logs.

## Components and data flow

1. On the live gateway, back up `openclaw.json`, set
   `diagnostics.otel.captureContent=false`, validate the config, and restart
   the gateway once in a controlled window. Keep the native trace exporter,
   bridge, and model routing enabled.
2. In `deploy/k8s/monitoring/otel-collector.yaml`, add an attributes
   processor scoped by an exact `service.name=openclaw` match. Delete the
   observed native content keys and their known tool/input/output variants:
   `openclaw.content.*`, `gen_ai.input.messages`,
   `gen_ai.output.messages`, `gen_ai.tool.definitions`,
   `gen_ai.tool.call.arguments`, `gen_ai.tool.call.result`,
   `input.value`, and `output.value`. Preserve provider, model, token,
   latency, trace-parent, and resource identity fields. Place this processor
   after the existing PII strip and before batch/export so both Tempo and
   debug exporters see only stripped native spans.
3. Deploy the collector config through the existing `scripts/deploy.sh`
   rollout path. Its config-hash annotation forces a collector restart when
   the manifest changes. The proxy image deployment is an existing script
   side effect, not part of the privacy behavior.

The collector rule is a safety net, not permission to re-enable source
capture. Existing non-OpenClaw telemetry is unaffected by the scoped rule.

## Failure behavior and rollback

Collector config must be validated with the pinned contrib image before
rollout. A bad collector rollout stops the deployment and requires restoring
the previous manifest; do not disable native export or reroute model requests
as a workaround. Gateway config changes are backed up before mutation. If
the gateway fails to restart, restore only that config and retry the gateway
service; the collector strip remains safe to keep. Observability failures
must not block model execution.

## Verification

- Exercise the collector processor with synthetic OpenClaw and non-OpenClaw
  spans. Forbidden keys disappear only from OpenClaw spans, while model,
  provider, usage, and trace identifiers survive.
- Validate the collector YAML against the pinned
  `otel/opentelemetry-collector-contrib:0.126.0` image and verify its rollout.
- Confirm the gateway reports the exporter `started` in `configured` mode
  and the bridge still loads after the restart.
- Send one disposable low-content gateway turn. Check in Tempo that its new
  native span lacks every forbidden content key, still has model and token
  attributes, and that the bridge span still carries its session and model
  attribution. Inspect keys and aggregate metadata only, never values.
- Run `scripts/verify.sh`; it must exit 0. Record any checks that remain
  outside this issue, especially historical retention and proxy previews.

## Deferred work

Historical Tempo traces may already contain captured content; access and
retention remediation needs a separate decision. Proxy and bridge previews
are also outside this native-only guard. Session identity and collector
`openclaw.*` to `prov.*` mapping remain #281 and #286, not part of #291.
