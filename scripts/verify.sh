#!/usr/bin/env bash
# AgentWeave end-to-end smoke test
set -uo pipefail

# --- Configuration (override via env) ---
NODE_IP="${AGENTWEAVE_NODE_IP:-192.168.1.70}"
PROXY_PORT="${AGENTWEAVE_PROXY_NODEPORT:-30400}"
TEMPO_QUERY_PORT="${AGENTWEAVE_TEMPO_QUERY_PORT:-31989}"  # Tempo HTTP query port (NodePort)
GRAFANA_PORT="${AGENTWEAVE_GRAFANA_PORT:-30300}"           # Grafana NodePort
OPENCLAW_PORT="${AGENTWEAVE_OPENCLAW_PORT:-18789}"         # OpenClaw gateway (has Anthropic key)
OPENCLAW_TOKEN="${OPENCLAW_GATEWAY_TOKEN:-}"               # Optional: OpenClaw gateway token

PROXY_URL="http://${NODE_IP}:${PROXY_PORT}"
TEMPO_QUERY_URL="http://${NODE_IP}:${TEMPO_QUERY_PORT}"
GRAFANA_URL="http://${NODE_IP}:${GRAFANA_PORT}"
OPENCLAW_URL="http://localhost:${OPENCLAW_PORT}"           # OpenClaw runs on localhost

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
# Check 2: Send a minimal LLM call via OpenClaw → proxy → Anthropic
# Uses OpenClaw's HTTP completions endpoint (OpenClaw holds the API key)
# This verifies the real production path: OpenClaw → AgentWeave proxy → Anthropic
# ============================================================
ts "--- Check 2/4: LLM call via OpenClaw → proxy ---"

AUTH_ARGS=()
if [ -n "${OPENCLAW_TOKEN}" ]; then
  AUTH_ARGS=(-H "Authorization: Bearer ${OPENCLAW_TOKEN}")
fi

LLM_RESP=$(curl -sf --max-time 30 \
  -X POST "${OPENCLAW_URL}/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -H "x-agentweave-agent-id: smoke-test" \
  -H "x-agentweave-project: agentweave-verify" \
  "${AUTH_ARGS[@]}" \
  -d '{
    "model": "anthropic/claude-haiku-4-5",
    "max_tokens": 16,
    "messages": [{"role": "user", "content": "Say ok"}]
  }' 2>&1) && {
  if echo "${LLM_RESP}" | grep -q '"choices"'; then
    check_pass "LLM call via OpenClaw succeeded (got completions response)"
  else
    check_fail "LLM call returned unexpected response: ${LLM_RESP:0:200}"
  fi
} || {
  check_fail "LLM call failed — curl error or non-2xx: ${LLM_RESP:0:200}"
}

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

# Query Prometheus directly (NodePort not exposed — use Grafana proxy endpoint)
# Grafana is on NodePort 30300, Prometheus ClusterIP 10.43.3.20:9090
# Grafana datasource uid can be found via /api/datasources
PROM_QUERY="traces_spanmetrics_calls_total"
GRAFANA_CREDS="${GRAFANA_USER:-admin}:${GRAFANA_PASSWORD:-observability123}"

# Try Grafana datasource proxy (finds Prometheus datasource by name)
GRAFANA_RESP=$(curl -sf --max-time 10 -u "${GRAFANA_CREDS}" \
  "${GRAFANA_URL}/api/datasources/proxy/uid/prometheus/api/v1/query?query=${PROM_QUERY}" \
  2>&1) || true

if [ -z "${GRAFANA_RESP}" ] || ! echo "${GRAFANA_RESP}" | grep -q '"result"'; then
  # Fallback: query via Grafana's generic datasource proxy (datasource id=1)
  GRAFANA_RESP=$(curl -sf --max-time 10 -u "${GRAFANA_CREDS}" \
    "${GRAFANA_URL}/api/datasources/proxy/1/api/v1/query?query=${PROM_QUERY}" \
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
