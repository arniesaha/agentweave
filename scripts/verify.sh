#!/usr/bin/env bash
# AgentWeave end-to-end smoke test
set -uo pipefail

# --- Configuration (override via env) ---
NODE_IP="${AGENTWEAVE_NODE_IP:-192.168.1.70}"
PROXY_PORT="${AGENTWEAVE_PROXY_NODEPORT:-30400}"
TEMPO_PORT="${AGENTWEAVE_TEMPO_NODEPORT:-30418}"
TEMPO_QUERY_PORT="${AGENTWEAVE_TEMPO_QUERY_PORT:-3200}"
GRAFANA_PORT="${AGENTWEAVE_GRAFANA_PORT:-3000}"
PROXY_TOKEN="${AGENTWEAVE_PROXY_TOKEN:-}"
ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}"

PROXY_URL="http://${NODE_IP}:${PROXY_PORT}"
TEMPO_QUERY_URL="http://${NODE_IP}:${TEMPO_QUERY_PORT}"
GRAFANA_URL="http://${NODE_IP}:${GRAFANA_PORT}"

PASS=0
FAIL=0
TOTAL=4

ts() { printf "[%s] %s\n" "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }

check_pass() { ts "PASS: $*"; PASS=$((PASS + 1)); }
check_fail() { ts "FAIL: $*"; FAIL=$((FAIL + 1)); }

# ============================================================
# Check 1: Proxy health endpoint
# ============================================================
ts "--- Check 1/4: Proxy health ---"
HEALTH_RESP=$(curl -sf --max-time 10 "${PROXY_URL}/health" 2>&1) && {
  check_pass "Proxy health responded: ${HEALTH_RESP}"
} || {
  check_fail "Proxy health unreachable at ${PROXY_URL}/health"
}

# ============================================================
# Check 2: Send a minimal LLM call through the proxy
# ============================================================
ts "--- Check 2/4: LLM call through proxy ---"

if [ -z "${ANTHROPIC_API_KEY}" ]; then
  check_fail "ANTHROPIC_API_KEY not set — cannot send test LLM call"
else
  AUTH_HEADER=""
  if [ -n "${PROXY_TOKEN}" ]; then
    AUTH_HEADER="Authorization: Bearer ${PROXY_TOKEN}"
  fi

  LLM_RESP=$(curl -sf --max-time 30 \
    -X POST "${PROXY_URL}/v1/messages" \
    -H "Content-Type: application/json" \
    -H "x-api-key: ${ANTHROPIC_API_KEY}" \
    -H "anthropic-version: 2023-06-01" \
    -H "x-agentweave-agent-id: smoke-test" \
    -H "x-agentweave-project: agentweave-verify" \
    ${AUTH_HEADER:+-H "${AUTH_HEADER}"} \
    -d '{
      "model": "claude-haiku-4-5-20251001",
      "max_tokens": 16,
      "messages": [{"role": "user", "content": "Say ok"}]
    }' 2>&1) && {
    # Check for a valid response with content
    if echo "${LLM_RESP}" | grep -q '"type":"message"'; then
      check_pass "LLM call succeeded (got message response)"
    else
      check_fail "LLM call returned unexpected response: ${LLM_RESP:0:200}"
    fi
  } || {
    check_fail "LLM call failed — curl error or non-2xx response"
  }
fi

# ============================================================
# Check 3: Verify span appeared in Tempo
# ============================================================
ts "--- Check 3/4: Trace in Tempo ---"

SPAN_FOUND=false
for i in $(seq 1 10); do
  # Search Tempo for recent spans from our smoke test
  TEMPO_RESP=$(curl -sf --max-time 10 \
    "${TEMPO_QUERY_URL}/api/search?q=%7Bprov.agent.id%3D%22smoke-test%22%7D&limit=1&start=$(( $(date +%s) - 120 ))&end=$(date +%s)" \
    2>&1) || true

  if echo "${TEMPO_RESP}" | grep -q '"traceID"'; then
    TRACE_ID=$(echo "${TEMPO_RESP}" | grep -o '"traceID":"[^"]*"' | head -1)
    check_pass "Trace found in Tempo: ${TRACE_ID}"
    SPAN_FOUND=true
    break
  fi

  ts "Waiting for span in Tempo (attempt ${i}/10)..."
  sleep 3
done

if [ "${SPAN_FOUND}" = false ]; then
  check_fail "No trace found in Tempo after 30s (query: prov.agent.id=smoke-test)"
fi

# ============================================================
# Check 4: Grafana "Total LLM Calls" panel returns non-zero
# ============================================================
ts "--- Check 4/4: Grafana LLM call count ---"

# Query Prometheus via Grafana's datasource proxy for the span metrics
# that Tempo's metrics-generator writes to Prometheus
PROM_QUERY="traces_spanmetrics_calls_total"
GRAFANA_RESP=$(curl -sf --max-time 10 \
  "${GRAFANA_URL}/api/datasources/proxy/1/api/v1/query?query=${PROM_QUERY}" \
  2>&1) || true

if [ -z "${GRAFANA_RESP}" ]; then
  # Fallback: try Prometheus directly
  PROM_URL="http://${NODE_IP}:9090"
  GRAFANA_RESP=$(curl -sf --max-time 10 \
    "${PROM_URL}/api/v1/query?query=${PROM_QUERY}" \
    2>&1) || true
fi

if echo "${GRAFANA_RESP}" | grep -q '"result"'; then
  # Check if there's at least one result with a non-zero value
  VALUE=$(echo "${GRAFANA_RESP}" | grep -o '"value":\[[^]]*\]' | head -1 | grep -o '[0-9.]*"' | head -1 | tr -d '"')
  if [ -n "${VALUE}" ] && [ "${VALUE}" != "0" ]; then
    check_pass "Total LLM Calls metric is non-zero (${VALUE})"
  else
    check_fail "Total LLM Calls metric is zero or empty"
  fi
else
  check_fail "Could not query LLM call metrics from Grafana/Prometheus"
fi

# ============================================================
# Summary
# ============================================================
echo ""
ts "========================================="
ts "Results: ${PASS}/${TOTAL} passed, ${FAIL}/${TOTAL} failed"
ts "========================================="

if [ "${FAIL}" -gt 0 ]; then
  exit 1
fi
exit 0
