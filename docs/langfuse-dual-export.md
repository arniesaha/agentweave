# Langfuse Dual Export Spike

Issue: <https://github.com/arniesaha/agentweave/issues/218>

## Decision

Keep Tempo as the canonical AgentWeave trace backend. Add Langfuse as an
optional second sink for LLM/product workflows: prompt iteration, sessions,
datasets, human review, LLM-as-judge scores, and context-handoff evals.

AgentWeave stays backend-agnostic and continues emitting portable OpenTelemetry
spans with `prov.*` and `gen_ai.*` attributes.

## Current NAS State

Checked on 2026-05-30:

- Langfuse runs in namespace `apps` as `deploy/langfuse`.
- Public URL is `https://langfuse.arnabsaha.com` behind Cloudflare Access.
- LAN URL is `http://192.168.1.70:30893`.
- Runtime image is `langfuse/langfuse:2`; app logs report v2.95.x behavior.
- Storage is Postgres-only: `deploy/langfuse-postgres` and
  `pvc/langfuse-postgres-pvc`.
- No ClickHouse, Redis/Valkey, S3/blob storage, OpenTelemetry Collector, or
  Alloy deployment is currently in the trace path.

## Why Not Replace Tempo

Tempo is infrastructure tracing: raw OTLP storage, TraceQL, spanmetrics, and the
current AgentWeave dashboard. Langfuse is product/eval observability: sessions,
prompts, datasets, scores, and annotations.

Replacing Tempo would force a dashboard and storage migration before we know
whether Langfuse improves the Nexus/context-handoff review loop. Dual export
keeps the working system intact.

## Target Path

```text
AgentWeave SDK/proxy/bridge
  -> agentweave-otel-collector.monitoring.svc.cluster.local:4318
      -> tempo.monitoring.svc.cluster.local:4318
      -> langfuse-v3-web.apps.svc.cluster.local:3000/api/public/otel
```

## Current v3 State

Checked on 2026-05-30:

- Langfuse v2 remains live and untouched at `langfuse.arnabsaha.com`.
- Langfuse v3 is live side-by-side as Helm release `langfuse-v3` in `apps`.
- LAN URL is `http://192.168.1.70:30894`.
- Public URL is `https://langfuse-v3.arnabsaha.com` behind Cloudflare Access.
- v3 health reports `3.175.0`.
- The AgentWeave collector exports to both Tempo and Langfuse v3.
- Langfuse v3 ClickHouse contains fresh AgentWeave traces and `GENERATION`
  observations.

## OpenClaw/Mux Runtime Wiring

When dogfooding from OpenClaw, make sure every runtime exporter points at the
collector, not Tempo directly. Direct Tempo export bypasses Langfuse fanout.

Current NAS runtime wiring:

```bash
# Mux uses the TypeScript AgentWeave SDK, which expects the full OTLP traces URL.
AGENTWEAVE_OTLP_ENDPOINT=http://10.43.221.47:4318/v1/traces

# openclaw-agentweave-bridge appends /v1/traces itself, so use the collector base.
otlpEndpoint=http://10.43.221.47:4318
```

The collector ClusterIP can change if the Kubernetes Service is recreated, so
verify it before debugging missing Langfuse traces:

```bash
kubectl get svc -n monitoring agentweave-otel-collector
```

## Langfuse Upgrade Constraint

Do not upgrade the current Helm release by changing `image.tag` from `2` to `3`.
Langfuse v3 adds required infrastructure:

- Langfuse web container
- Langfuse worker container
- Postgres
- ClickHouse
- Redis/Valkey
- S3/blob storage

The safe path is staged:

1. Back up Postgres and the current Helm/Kubernetes state.
2. Keep v2 running.
3. Deploy the v3 stack separately, pinned first to the Langfuse-recommended
   bridge version for v2 migrations.
4. Validate the v3 UI and ingestion against the existing Postgres plus new
   ClickHouse/Redis/S3 dependencies.
5. Shift traffic only after new events land and background migrations are
   healthy.

## Backup Taken

Before any migration work, a logical Postgres backup was created:

```text
/home/Arnab/clawd/backups/langfuse/langfuse-postgres-20260530-123107.dump
```

`pg_restore -l` successfully listed the archive.

## Attributes to Add or Verify

Trace-level attributes should be repeated on every span for Langfuse filtering:

- `langfuse.trace.name`
- `langfuse.session.id`
- `langfuse.trace.metadata.project`
- `langfuse.trace.metadata.agent_id`
- `langfuse.trace.metadata.agent_type`
- `langfuse.trace.metadata.parent_session_id`
- `langfuse.trace.metadata.repository`

LLM observation attributes:

- `langfuse.observation.type=generation`
- `langfuse.observation.model.name`
- `langfuse.observation.usage_details`
- `langfuse.observation.cost_details`
- `langfuse.observation.input`
- `langfuse.observation.output`

Only send inputs/outputs when capture flags and the privacy boundary allow it.

## Nexus Evaluation Candidates

For context-handoff reviews, score:

- handoff completeness
- preserved constraints
- missing or hallucinated assumptions
- context size and compression ratio
- stale-context usage
- tool-result grounding
- downstream task success
- cost and latency impact of extra context

These scores belong in Langfuse. Raw trace trees and service-level metrics stay
in Tempo.

Detailed mapping: [Langfuse Eval Mapping](./langfuse-eval-mapping.md).

## Rollout Sequence

1. Apply the Tempo-only collector and point AgentWeave proxy at it.
2. Verify traces still land in Tempo and the dashboard stays healthy.
3. Upgrade Langfuse from v2 to v3 using a staged migration. Done side-by-side
   via `langfuse-v3`.
4. Create a Langfuse project API key and update
   `secret/agentweave-otel-collector` in `monitoring`. Done.
5. Apply `deploy/k8s/monitoring/otel-collector-langfuse-fanout.yaml`. Done.
6. Restart the collector and verify the same trace appears in Tempo and
   Langfuse. Done.
