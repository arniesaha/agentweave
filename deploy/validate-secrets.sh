#!/usr/bin/env bash
# validate-secrets.sh — check agentweave-proxy secret for empty fields,
# wrong formats, and OAuth tokens (which expire and break injection).
#
# Exit codes:
#   0 — all checks pass (warnings possible but non-fatal)
#   1 — OAuth token detected in a field (hard fail, per docs/proxy-setup.md)
set -euo pipefail

NAMESPACE="${AGENTWEAVE_NAMESPACE:-agentweave}"
OAUTH_FOUND=0

read_secret_field() {
  kubectl get secret agentweave-proxy -n "$NAMESPACE" \
    -o jsonpath="{.data.$1}" 2>/dev/null | base64 -d 2>/dev/null || true
}

# emptiness check — returns 0 if set, 1 if empty. Warning is non-fatal.
check_empty() {
  local field="$1" value="$2"
  if [ -z "$value" ]; then
    echo "WARNING: secret field '$field' is empty — key injection for this provider is disabled"
    return 1
  fi
  echo "OK: $field is set"
  return 0
}

# OAuth guard — sk-ant-oat* tokens expire and break injection after ~24h.
# Documented rule: never use OAuth tokens for key injection. Hard fail.
check_oauth() {
  local field="$1" value="$2"
  if [[ "$value" == sk-ant-oat* ]]; then
    echo "ERROR: secret field '$field' is an OAuth token (sk-ant-oat*)."
    echo "       OAuth tokens expire — use a standard API key (sk-ant-api03_*)."
    echo "       See docs/proxy-setup.md for key injection setup."
    OAUTH_FOUND=1
    return 1
  fi
  return 0
}

# format check for Anthropic keys — warn only (some operators may use custom formats)
check_anthropic_format() {
  local field="$1" value="$2"
  if [ -n "$value" ] && [[ "$value" != sk-ant-api03_* ]]; then
    echo "WARNING: secret field '$field' does not look like a standard Anthropic API key (expected sk-ant-api03_*)"
  fi
}

echo "=== AgentWeave secret validation (namespace: $NAMESPACE) ==="

ANTHROPIC_KEY=$(read_secret_field anthropic-api-key)
OPENAI_KEY=$(read_secret_field openai-api-key)
GOOGLE_KEY=$(read_secret_field google-api-key)

check_oauth "anthropic-api-key" "$ANTHROPIC_KEY" || true
check_oauth "openai-api-key" "$OPENAI_KEY" || true
check_oauth "google-api-key" "$GOOGLE_KEY" || true

check_empty "anthropic-api-key" "$ANTHROPIC_KEY" || true
check_empty "openai-api-key" "$OPENAI_KEY" || true
check_empty "google-api-key" "$GOOGLE_KEY" || true

check_anthropic_format "anthropic-api-key" "$ANTHROPIC_KEY"

if [ "$OAUTH_FOUND" -ne 0 ]; then
  echo "=== validation FAILED: OAuth token(s) detected ==="
  exit 1
fi

echo "=== validation passed ==="
