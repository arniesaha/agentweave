# Langfuse v3 Hybrid Spike Runbook

Issue: <https://github.com/arniesaha/agentweave/issues/219>

## Goal

Bring up a side-by-side Langfuse v3 stack without disturbing the current v2
service at `langfuse.arnabsaha.com`.

The first spike uses:

- `langfuse-v3` release in namespace `apps`
- Langfuse web/worker on the Mac/OrbStack worker (`host=mac-mini`)
- separate v3 Postgres on the Mac/OrbStack worker
- single-node ClickHouse on the Mac/OrbStack worker (`host=mac-mini`)
- standalone Valkey on the Mac/OrbStack worker
- existing MinIO in namespace `monitoring`, bucket `langfuse`
- temporary NodePort `30894`

## Safety Checks

Do not mutate or delete the current v2 release/PVC until these exist:

- logical Postgres dump
- physical PVC/state tarball
- saved Helm values/manifest
- saved Deployment/Service/Secret/PVC/PV YAML

Current verified backups:

- `/home/Arnab/clawd/backups/langfuse/langfuse-postgres-20260530-123107.dump`
- `/home/Arnab/clawd/backups/langfuse/langfuse-v2-state-20260530-134541/`

The old OrbStack internal data directory is still intentionally retained as a
rollback backup after moving OrbStack storage to `/Volumes/M2 1/orbstack`.

## Install Preflight

Create the runtime secret in `apps` from generated values and duplicated MinIO
credentials. Do not commit the rendered secret or local values file.

```bash
kubectl create secret generic langfuse-v3-secrets -n apps \
  --from-literal=NEXTAUTH_SECRET="$(openssl rand -base64 32)" \
  --from-literal=SALT="$(openssl rand -base64 32)" \
  --from-literal=ENCRYPTION_KEY="$(openssl rand -hex 32)" \
  --from-literal=POSTGRES_PASSWORD="$(openssl rand -base64 24)" \
  --from-literal=CLICKHOUSE_PASSWORD="$(openssl rand -base64 24)" \
  --from-literal=REDIS_PASSWORD="$(openssl rand -hex 24)" \
  --from-literal=MINIO_ROOT_USER="minioadmin" \
  --from-literal=MINIO_ROOT_PASSWORD="minioadmin123"
```

Use a URL-safe Redis password. Langfuse builds `REDIS_CONNECTION_STRING`; a raw
base64 password with `/` can be parsed as a path segment.

Create the MinIO bucket:

```bash
kubectl exec -n monitoring deploy/minio -- sh -lc \
  'mc alias set local http://127.0.0.1:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" && mc mb --ignore-existing local/langfuse'
```

Render before installing:

```bash
helm template langfuse-v3 langfuse/langfuse \
  --namespace apps \
  --values deploy/k8s/langfuse-v3-values.example.yaml >/tmp/langfuse-v3-rendered.yaml
```

## Install

```bash
helm upgrade --install langfuse-v3 langfuse/langfuse \
  --namespace apps \
  --values deploy/k8s/langfuse-v3-values.example.yaml \
  --wait --timeout 10m
```

## Verify

```bash
kubectl get pods -n apps -l app.kubernetes.io/instance=langfuse-v3 -o wide
kubectl get pvc -n apps | grep langfuse-v3
curl -fsS http://192.168.1.70:30894/api/public/health
curl -i http://192.168.1.70:30894/api/public/otel/v1/traces
```

Expected result:

- UI/API health returns OK
- `/api/public/otel/v1/traces` exists and returns `401` without auth, not `404`
- Langfuse v3 web/worker, Postgres, ClickHouse, and Valkey pods land on
  `ubuntu`
- v2 service on NodePort `30893` remains unchanged

## Current Spike State

As of 2026-05-30 14:08 PDT:

- `langfuse-v3` is deployed and healthy on NodePort `30894`
- health endpoint returns `{"status":"OK","version":"3.29.0"}`
- unauthenticated OTLP POST returns `401`, confirming the endpoint exists
- v2 remains healthy on NodePort `30893`, version `2.95.11`
- all v3 pods are scheduled on `ubuntu`
- Mac/OrbStack VM has ~16 GiB free disk and ~6.3 GiB available RAM after deploy

## Next Migration Step

After the empty v3 stack is healthy, restore the v2 logical dump into the
separate v3 Postgres and run the v3.29.0 bridge with
`LANGFUSE_ENABLE_BACKGROUND_MIGRATIONS=false`. Only switch
`langfuse.arnabsaha.com` after the UI, ingestion, and rollback path are proven.
