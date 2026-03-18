# Proxy Latency Benchmarks

AgentWeave's proxy intercepts LLM API calls to emit OpenTelemetry spans. This document covers the overhead introduced by the proxy and how to measure it yourself.

## TL;DR

**The proxy adds ~5–15ms overhead on LLM API calls — less than 3% of typical request latency.**

Model inference dominates request latency (300ms–5s+). The proxy is not the bottleneck.

---

## Methodology

The benchmark compares:
1. **Direct latency** — requests sent directly to the upstream provider (Anthropic/OpenAI/Google)
2. **Proxied latency** — the same requests routed through the AgentWeave proxy

We measure p50, p95, p99, and mean latency at:
- **`/health` endpoint** — measures raw proxy overhead (no upstream call)
- **LLM requests** — measures end-to-end overhead including span emission

---

## Reference Results

Environment: NAS k3s cluster, proxy on NodePort 30400, Tempo on NodePort 30418.

### /health Endpoint (raw proxy overhead)

| Metric | Direct | Proxied | Overhead |
|--------|--------|---------|----------|
| P50    | 0.41 ms | 0.89 ms | +0.5 ms (119%) |
| P95    | 0.82 ms | 2.1 ms  | +1.3 ms (159%) |
| P99    | 1.2 ms  | 4.8 ms  | +3.6 ms (300%) |
| Mean   | 0.45 ms | 1.1 ms  | +0.7 ms (155%) |

The percentage looks high because absolute values are sub-millisecond. In practice this adds < 1ms.

### LLM Requests (Anthropic claude-3-haiku, non-streaming)

| Metric | Direct | Proxied | Overhead |
|--------|--------|---------|----------|
| P50    | 312 ms | 318 ms | +6 ms (1.9%)   |
| P95    | 580 ms | 591 ms | +11 ms (1.9%)  |
| P99    | 820 ms | 837 ms | +17 ms (2.1%)  |
| Mean   | 325 ms | 334 ms | +9 ms (2.8%)   |

Samples: 100 requests each.

### Streaming Requests

Streaming overhead is negligible — the proxy forwards bytes as they arrive without buffering the full response. Span emission happens at stream end, not during streaming.

---

## What the Proxy Does Per Request

1. Receives request from agent
2. Detects provider from URL path (Anthropic / OpenAI / Google)
3. Forwards request to upstream API (streaming or non-streaming)
4. For non-streaming: parses response to extract token counts
5. For streaming: reads final chunk for usage data (Anthropic `message_stop`, OpenAI usage chunk)
6. Emits one OTel span to Tempo via OTLP HTTP
7. Returns response to agent

The dominant costs are network I/O (upstream API latency) and OTLP export (async, non-blocking).

---

## Run the Benchmark Yourself

```bash
# See reference results without live calls
python benchmarks/proxy_latency.py --dry-run

# Benchmark against a running proxy
python benchmarks/proxy_latency.py \
  --proxy-url http://localhost:4000 \
  --requests 100

# Compare proxy vs a direct endpoint
python benchmarks/proxy_latency.py \
  --proxy-url http://localhost:4000 \
  --direct-url http://some-other-endpoint \
  --requests 50
```

Requirements: `pip install httpx`
