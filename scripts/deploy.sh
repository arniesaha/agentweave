#!/usr/bin/env bash
# AgentWeave deploy script — build, push, deploy, verify
set -euo pipefail

# --- Configuration (override via env) ---
REGISTRY="${AGENTWEAVE_REGISTRY:-localhost:5000}"
IMAGE="${REGISTRY}/agentweave-proxy:latest"
NAMESPACE="${AGENTWEAVE_NAMESPACE:-agentweave}"
NODE_IP="${AGENTWEAVE_NODE_IP:-192.168.1.70}"
PROXY_NODEPORT="${AGENTWEAVE_PROXY_NODEPORT:-30400}"
HEALTH_URL="http://${NODE_IP}:${PROXY_NODEPORT}/health"
ROLLOUT_TIMEOUT="${AGENTWEAVE_ROLLOUT_TIMEOUT:-120s}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

ts() { printf "[%s] %s\n" "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }

fail() { ts "ERROR: $*" >&2; exit 1; }

# --- Step 1: Build Docker image ---
ts "Building Docker image: ${IMAGE}"
docker build -t "${IMAGE}" -f "${REPO_ROOT}/deploy/docker/Dockerfile" "${REPO_ROOT}" \
  || fail "Docker build failed"
ts "Build complete"

# --- Step 2: Push to local registry ---
ts "Pushing image to ${REGISTRY}"
docker push "${IMAGE}" \
  || fail "Docker push failed — is the registry at ${REGISTRY} running?"
ts "Push complete"

# --- Step 3: Deploy to k8s ---
ts "Deploying to k8s namespace '${NAMESPACE}'"

# Apply base manifests first (namespace, configmap, secret, service, deployment)
kubectl apply -f "${REPO_ROOT}/deploy/k8s/namespace.yaml"
kubectl apply -f "${REPO_ROOT}/deploy/k8s/configmap.yaml"
kubectl apply -f "${REPO_ROOT}/deploy/k8s/secret.yaml"
kubectl apply -f "${REPO_ROOT}/deploy/k8s/service.yaml"
kubectl apply -f "${REPO_ROOT}/deploy/k8s/deployment.yaml"

# Restart deployment to pick up :latest image
kubectl rollout restart deployment/agentweave-proxy -n "${NAMESPACE}"
ts "Manifests applied, rollout restarting"

# --- Step 4: Wait for rollout ---
ts "Waiting for rollout to complete (timeout: ${ROLLOUT_TIMEOUT})"
kubectl rollout status deployment/agentweave-proxy -n "${NAMESPACE}" --timeout="${ROLLOUT_TIMEOUT}" \
  || fail "Rollout did not complete within ${ROLLOUT_TIMEOUT}"
ts "Rollout complete"

# --- Step 5: Health check ---
ts "Checking proxy health at ${HEALTH_URL}"
for i in $(seq 1 10); do
  if curl -sf --max-time 5 "${HEALTH_URL}" >/dev/null 2>&1; then
    HEALTH_RESP=$(curl -sf --max-time 5 "${HEALTH_URL}")
    ts "Health check passed: ${HEALTH_RESP}"
    ts "Deploy successful"
    exit 0
  fi
  ts "Health check attempt ${i}/10 — retrying in 3s"
  sleep 3
done

fail "Health check failed after 10 attempts at ${HEALTH_URL}"
