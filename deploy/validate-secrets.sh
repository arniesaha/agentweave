#!/usr/bin/env bash
# validate-secrets.sh — warn when k8s secrets have empty API keys
set -euo pipefail

NAMESPACE="${AGENTWEAVE_NAMESPACE:-agentweave}"

check_secret_field() {
  local field="$1"
  local value
  value=$(kubectl get secret agentweave-proxy -n "$NAMESPACE" \
    -o jsonpath="{.data.$field}" 2>/dev/null | base64 -d 2>/dev/null || echo "")
  if [ -z "$value" ]; then
    echo "WARNING: secret field '$field' is empty — key injection for this provider is disabled"
    return 1
  fi
  echo "OK: $field is set"
  return 0
}

echo "=== AgentWeave secret validation ==="
check_secret_field "anthropic-api-key" || true
check_secret_field "openai-api-key" || true
