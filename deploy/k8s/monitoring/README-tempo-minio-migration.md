# Tempo MinIO Migration Runbook

## Why

Tempo's local storage backend has a known compaction bug that causes trace data
corruption and eventual disk pressure under sustained write loads. Moving to
MinIO (S3-compatible object storage) resolves this by offloading block storage
to an object store, which Tempo's compactor handles reliably.

Ref: AgentWeave issue #32

## Prerequisites

- `kubectl` access to the cluster
- `monitoring` namespace exists
- Tempo is currently running in the `monitoring` namespace

## Migration Steps

### 1. Deploy MinIO

```bash
kubectl apply -k deploy/k8s/monitoring/
```

### 2. Wait for MinIO to be ready

```bash
kubectl -n monitoring rollout status deployment/minio
```

### 3. Verify the init job created the bucket

```bash
kubectl -n monitoring wait --for=condition=complete job/minio-init-bucket --timeout=120s
kubectl -n monitoring logs job/minio-init-bucket -c create-bucket
```

### 4. (Optional) Migrate existing trace blocks

If you need to preserve existing local blocks, port-forward the MinIO console
and use `mc mirror` to copy them into the `tempo` bucket:

```bash
# Port-forward MinIO API
kubectl -n monitoring port-forward svc/minio 9000:9000 &

# Configure mc alias
mc alias set local http://localhost:9000 minioadmin minioadmin123

# Copy existing blocks into MinIO
mc mirror /var/tempo/blocks/ local/tempo/
```

### 5. Apply the new Tempo ConfigMap

```bash
kubectl -n monitoring apply -f deploy/k8s/monitoring/tempo-configmap-minio.yaml
```

If Tempo is currently using a ConfigMap named `tempo-config`, update the Tempo
deployment to reference `tempo-config-minio` instead, or replace the existing
ConfigMap contents.

### 6. Restart Tempo

```bash
kubectl -n monitoring rollout restart deployment/tempo
kubectl -n monitoring rollout status deployment/tempo
```

### 7. Verify

```bash
# Port-forward Tempo HTTP API
kubectl -n monitoring port-forward svc/tempo 3200:3200 &

# Check Tempo is healthy
curl -s http://localhost:3200/ready

# Check storage is working (should return 200)
curl -s http://localhost:3200/status

# Send a test trace and query it back to confirm end-to-end
```

## Rollback

To revert to local storage, re-apply the original Tempo ConfigMap and restart:

```bash
kubectl -n monitoring apply -f <original-tempo-configmap.yaml>
kubectl -n monitoring rollout restart deployment/tempo
```
