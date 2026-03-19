#!/usr/bin/env bash
# AgentWeave Dashboard deploy script — build, tag versioned, push, deploy
set -euo pipefail

REGISTRY="localhost:5000"
IMAGE="${REGISTRY}/agentweave-dashboard"
NAMESPACE="agentweave"
DASHBOARD_DIR="$(cd "$(dirname "$0")/../dashboard" && pwd)"

ts() { printf "[%s] %s\n" "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
fail() { ts "ERROR: $*" >&2; exit 1; }

# --- Get current version tag from k8s, increment ---
CURRENT_TAG=$(kubectl get deployment agentweave-dashboard -n "${NAMESPACE}" \
  -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null | grep -o 'v[0-9]*$' || echo "v0")
CURRENT_VER="${CURRENT_TAG#v}"
NEXT_VER=$((CURRENT_VER + 1))
NEXT_TAG="v${NEXT_VER}"

ts "Current tag: ${CURRENT_TAG} → Next tag: ${NEXT_TAG}"

# --- Build ---
ts "Building image: ${IMAGE}:${NEXT_TAG}"
docker build --no-cache -t "${IMAGE}:${NEXT_TAG}" -t "${IMAGE}:latest" "${DASHBOARD_DIR}" \
  || fail "Docker build failed"

# --- Verify new JS bundle hash ---
NEW_BUNDLE=$(docker run --rm "${IMAGE}:${NEXT_TAG}" ls /usr/share/nginx/html/assets/ | grep "index-.*\.js" | head -1)
ts "New bundle: ${NEW_BUNDLE}"

# --- Push ---
ts "Pushing ${IMAGE}:${NEXT_TAG}"
docker push "${IMAGE}:${NEXT_TAG}" -q || fail "Push failed"

# --- Deploy ---
ts "Updating deployment to ${NEXT_TAG}"
kubectl set image deployment/agentweave-dashboard dashboard="${IMAGE}:${NEXT_TAG}" -n "${NAMESPACE}" \
  || fail "kubectl set image failed"

# --- Wait for rollout ---
ts "Waiting for rollout..."
kubectl rollout status deployment/agentweave-dashboard -n "${NAMESPACE}" --timeout=60s \
  || fail "Rollout did not complete"

# --- Verify new bundle is live ---
sleep 3
LIVE_BUNDLE=$(curl -s http://192.168.1.70:30896/index.html | grep -o 'assets/index-[^"]*\.js' | head -1)
ts "Live bundle: ${LIVE_BUNDLE}"
if [[ "${LIVE_BUNDLE}" == *"${NEW_BUNDLE%.js}"* ]]; then
  ts "✅ Deploy verified — new bundle is live"
else
  ts "⚠️  Bundle mismatch — live: ${LIVE_BUNDLE}, expected: ${NEW_BUNDLE}"
fi

ts "Dashboard deployed as ${NEXT_TAG} — http://192.168.1.70:30896"
