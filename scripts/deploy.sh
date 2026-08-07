#!/usr/bin/env bash
# AgentWeave deploy script — build, push, deploy, verify
set -euo pipefail

# --- Configuration (override via env) ---
REGISTRY="${AGENTWEAVE_REGISTRY:-localhost:5000}"
NAMESPACE="${AGENTWEAVE_NAMESPACE:-agentweave}"
MONITORING_NAMESPACE="${AGENTWEAVE_MONITORING_NAMESPACE:-monitoring}"
NODE_IP="${AGENTWEAVE_NODE_IP:-192.168.1.70}"
PROXY_NODEPORT="${AGENTWEAVE_PROXY_NODEPORT:-30400}"
HEALTH_URL="http://${NODE_IP}:${PROXY_NODEPORT}/health"
ROLLOUT_TIMEOUT="${AGENTWEAVE_ROLLOUT_TIMEOUT:-120s}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

ts() { printf "[%s] %s\n" "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }

fail() { ts "ERROR: $*" >&2; exit 1; }

# --- Step 0: Resolve and verify the version being deployed ---
# The proxy ran as :latest for months, so the running build was not
# identifiable from the cluster and a rollout could not be correlated to a
# commit. The SDK version is the single source of truth; the manifest pins it
# and this check refuses to deploy if the two have drifted.
MANIFEST="${REPO_ROOT}/deploy/k8s/deployment.yaml"
VERSION="$(grep -E '^version *= *"' "${REPO_ROOT}/sdk/python/pyproject.toml" \
  | head -1 | sed -E 's/.*"([^"]+)".*/\1/')"
[ -n "${VERSION}" ] || fail "Could not read version from sdk/python/pyproject.toml"

IMAGE="${REGISTRY}/agentweave-proxy:${VERSION}"
MANIFEST_IMAGE="$(grep -E 'image: .*agentweave-proxy:' "${MANIFEST}" \
  | head -1 | sed -E 's/.*image: *//')"

if [ "${MANIFEST_IMAGE}" != "${IMAGE}" ]; then
  fail "Version drift: pyproject.toml is ${VERSION} (image ${IMAGE}) but
${MANIFEST} pins ${MANIFEST_IMAGE}.
Bump both together, or set AGENTWEAVE_REGISTRY to match."
fi
ts "Deploying version ${VERSION}"

# Captured before any apply — afterwards the spec already holds the new tag.
RUNNING_IMAGE="$(kubectl get deployment/agentweave-proxy -n "${NAMESPACE}" \
  -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null || true)"

# --- Step 1: Build Docker image ---
ts "Building Docker image: ${IMAGE}"
docker build -t "${IMAGE}" -t "${REGISTRY}/agentweave-proxy:latest" \
  -f "${REPO_ROOT}/deploy/docker/Dockerfile" "${REPO_ROOT}" \
  || fail "Docker build failed"
ts "Build complete"

# --- Step 2: Push to local registry ---
# :latest is still pushed so anything pinned to it keeps resolving, but the
# deployment tracks the versioned tag.
ts "Pushing image to ${REGISTRY}"
docker push "${IMAGE}" \
  || fail "Docker push failed — is the registry at ${REGISTRY} running?"
docker push "${REGISTRY}/agentweave-proxy:latest" \
  || fail "Docker push of :latest failed"
ts "Push complete"

# --- Step 3: Deploy to k8s ---
ts "Deploying to k8s namespace '${NAMESPACE}'"

# Apply base manifests (namespace, configmap, service, deployment)
# ⚠️  secret.yaml is intentionally NEVER applied here — it contains a CHANGE_ME placeholder.
#     Applying it would overwrite the live proxy token and break all LAN agent comms.
#     The proxy secret is managed out-of-band (empty = LAN-open, no auth required).
if kubectl get secret agentweave-proxy -n "${NAMESPACE}" &>/dev/null; then
  ts "Secret 'agentweave-proxy' already exists — skipping (managed out-of-band)"
else
  ts "WARNING: Secret 'agentweave-proxy' not found — creating with empty token (LAN-open mode)"
  kubectl create secret generic agentweave-proxy \
    --from-literal=proxy-token="" \
    -n "${NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f -
fi

kubectl apply -f "${REPO_ROOT}/deploy/k8s/namespace.yaml"

# Deploy the Tempo-only collector before repointing/restarting the proxy. The
# Langfuse fanout overlay is applied manually after Langfuse v3 migration and a
# real project API key are ready.
kubectl apply -f "${REPO_ROOT}/deploy/k8s/monitoring/otel-collector.yaml"
kubectl rollout status deployment/agentweave-otel-collector \
  -n "${MONITORING_NAMESPACE}" --timeout="${ROLLOUT_TIMEOUT}" \
  || fail "OTel collector rollout did not complete within ${ROLLOUT_TIMEOUT}"

kubectl apply -f "${REPO_ROOT}/deploy/k8s/configmap.yaml"
kubectl apply -f "${REPO_ROOT}/deploy/k8s/service.yaml"
kubectl apply -f "${REPO_ROOT}/deploy/k8s/deployment.yaml"

# Validate the agentweave-proxy secret. Warnings are non-fatal (empty fields
# only disable injection for that provider); OAuth tokens cause a hard fail
# because they expire and silently break injection after ~24h.
ts "Validating secret fields"
AGENTWEAVE_NAMESPACE="${NAMESPACE}" bash "${REPO_ROOT}/deploy/validate-secrets.sh" \
  || fail "Secret validation failed — refusing to continue deploy"

# A changed image tag makes `kubectl apply` roll the deployment on its own.
# Only force a restart when the tag is unchanged — redeploying the same
# version after a rebuild — so we don't trigger two rollouts back to back.
# RUNNING_IMAGE was captured before the apply above.
if [ "${RUNNING_IMAGE}" = "${IMAGE}" ]; then
  ts "Image tag unchanged (${IMAGE}) — forcing restart to pick up rebuild"
  kubectl rollout restart deployment/agentweave-proxy -n "${NAMESPACE}"
else
  ts "Image tag changed (${RUNNING_IMAGE:-none} -> ${IMAGE}) — apply will roll"
fi
ts "Manifests applied"

# --- Step 4: Wait for rollout ---
ts "Waiting for rollout to complete (timeout: ${ROLLOUT_TIMEOUT})"
kubectl rollout status deployment/agentweave-proxy -n "${NAMESPACE}" --timeout="${ROLLOUT_TIMEOUT}" \
  || fail "Rollout did not complete within ${ROLLOUT_TIMEOUT}"
ts "Rollout complete"

# --- Step 5: Health check ---
# The NodePort still resolves to the old pod while it drains, so a bare 200
# here can report the *previous* build as a successful deploy — observed on
# the 0.3.2 rollout, where this printed version 0.3.1 and exited 0. Require
# the reported version to match what we just built.
ts "Checking proxy health at ${HEALTH_URL} (expecting version ${VERSION})"
for i in $(seq 1 10); do
  if HEALTH_RESP=$(curl -sf --max-time 5 "${HEALTH_URL}" 2>/dev/null); then
    LIVE_VERSION="$(printf '%s' "${HEALTH_RESP}" \
      | sed -nE 's/.*"version"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/p')"
    if [ "${LIVE_VERSION}" = "${VERSION}" ]; then
      ts "Health check passed: ${HEALTH_RESP}"
      ts "Deploy successful"
      exit 0
    fi
    ts "Health check attempt ${i}/10 — serving ${LIVE_VERSION:-unknown}, waiting for ${VERSION}"
    sleep 3
    continue
  fi
  ts "Health check attempt ${i}/10 — retrying in 3s"
  sleep 3
done

fail "Health check failed after 10 attempts at ${HEALTH_URL}
Last response: ${HEALTH_RESP:-<none>}
Expected version ${VERSION}. A healthy endpoint serving an older version means
the rollout did not fully replace the previous pods."
