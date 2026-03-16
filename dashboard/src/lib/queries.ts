// All query strings and transformers for AgentWeave Dashboard

export type TimeRange = '1h' | '3h' | '6h' | '24h' | '7d'

export function getTimeRangeSeconds(range: TimeRange): number {
  const map: Record<TimeRange, number> = {
    '1h': 3600,
    '3h': 10800,
    '6h': 21600,
    '24h': 86400,
    '7d': 604800,
  }
  return map[range]
}

export function getTimeRangeBounds(range: TimeRange): { start: number; end: number } {
  const end = Math.floor(Date.now() / 1000)
  const start = end - getTimeRangeSeconds(range)
  return { start, end }
}

export function getStepForRange(range: TimeRange): number {
  const map: Record<TimeRange, number> = {
    '1h': 60,
    '3h': 120,
    '6h': 300,
    '24h': 600,
    '7d': 3600,
  }
  return map[range]
}

// ─── Tempo Queries ───────────────────────────────────────────────────────────

export const TEMPO_SERVICE = 'agentweave-proxy'

export function tempoSearchQuery(): string {
  return `{ resource.service.name = "${TEMPO_SERVICE}" && name != "llm.unknown" }`
}

export function tempoCostMetricQuery(): string {
  return `{ resource.service.name = "${TEMPO_SERVICE}" } | sum_over_time(span.cost.usd)`
}

export function tempoCacheHitQuery(): string {
  return `{ resource.service.name = "${TEMPO_SERVICE}" } | avg_over_time(span.cache.hit_rate)`
}

export function tempoTurnCountQuery(): string {
  return `{ resource.service.name = "${TEMPO_SERVICE}" } | avg_over_time(span.agent.turn_count)`
}

// ─── Prometheus Queries ───────────────────────────────────────────────────────

export function promLLMCallsRateQuery(): string {
  return `rate(traces_spanmetrics_calls_total{service="${TEMPO_SERVICE}"}[5m]) * 300`
}

export function promCallsByModelQuery(range: TimeRange): string {
  const seconds = getTimeRangeSeconds(range)
  return `sum by (prov_llm_model) (increase(traces_spanmetrics_calls_total{service="${TEMPO_SERVICE}"}[${seconds}s]))`
}

export function promP95LatencyByModelQuery(): string {
  return `histogram_quantile(0.95, sum by (le, prov_llm_model) (rate(traces_spanmetrics_latency_bucket{service="${TEMPO_SERVICE}"}[5m]))) * 1000`
}

// ─── Response transformers ─────────────────────────────────────────────────

export interface PrometheusMatrixResult {
  metric: Record<string, string>
  values: [number, string][]
}

export interface PrometheusVectorResult {
  metric: Record<string, string>
  value: [number, string]
}

export interface PrometheusResponse {
  status: string
  data: {
    resultType: string
    result: PrometheusMatrixResult[] | PrometheusVectorResult[]
  }
}

export interface TimeSeriesPoint {
  time: number
  value: number
}

export function transformPrometheusMatrix(
  data: PrometheusResponse,
  labelKey?: string
): Array<{ label: string; points: TimeSeriesPoint[] }> {
  if (data.status !== 'success' || !data.data?.result) return []
  const results = data.data.result as PrometheusMatrixResult[]
  return results.map((r) => ({
    label: labelKey ? (r.metric[labelKey] ?? 'unknown') : JSON.stringify(r.metric),
    points: r.values.map(([ts, v]) => ({ time: ts * 1000, value: parseFloat(v) || 0 })),
  }))
}

export function transformPrometheusVector(
  data: PrometheusResponse,
  labelKey: string
): Array<{ label: string; value: number }> {
  if (data.status !== 'success' || !data.data?.result) return []
  const results = data.data.result as PrometheusVectorResult[]
  return results
    .map((r) => ({
      label: r.metric[labelKey] ?? 'unknown',
      value: parseFloat(r.value[1]) || 0,
    }))
    .sort((a, b) => b.value - a.value)
}

// ─── Tempo trace types ────────────────────────────────────────────────────────

export interface TempoSpan {
  traceID: string
  rootServiceName: string
  rootTraceName: string
  startTimeUnixNano: string
  durationMs: number
  spanSets?: Array<{
    spans: Array<{
      spanID: string
      startTimeUnixNano: string
      durationNanos: number
      attributes: Array<{ key: string; value: { stringValue?: string; intValue?: string; doubleValue?: number } }>
    }>
  }>
  spanSet?: {
    spans: Array<{
      spanID: string
      startTimeUnixNano: string
      durationNanos: number
      attributes: Array<{ key: string; value: { stringValue?: string; intValue?: string; doubleValue?: number } }>
    }>
  }
}

export interface TraceRow {
  traceId: string
  time: number
  model: string
  latencyMs: number
  tokensIn: number
  tokensOut: number
  costUsd: number
  sessionId: string
  attributes: Record<string, string>
}

function getSpanAttr(
  attrs: Array<{ key: string; value: { stringValue?: string; intValue?: string; doubleValue?: number } }>,
  key: string
): string {
  const a = attrs.find((x) => x.key === key)
  if (!a) return ''
  const v = a.value
  if (v.stringValue !== undefined) return v.stringValue
  if (v.intValue !== undefined) return String(v.intValue)
  if (v.doubleValue !== undefined) return String(v.doubleValue)
  return ''
}

export function transformTempoTraces(traces: TempoSpan[]): TraceRow[] {
  return traces.map((t) => {
    const spans =
      t.spanSets?.[0]?.spans ??
      t.spanSet?.spans ??
      []
    const span = spans[0]
    const attrs = span?.attributes ?? []
    const allAttrs: Record<string, string> = {}
    attrs.forEach((a) => {
      const v = a.value
      allAttrs[a.key] =
        v.stringValue ?? (v.intValue !== undefined ? String(v.intValue) : String(v.doubleValue ?? ''))
    })

    return {
      traceId: t.traceID,
      time: parseInt(t.startTimeUnixNano) / 1e6,
      model: getSpanAttr(attrs, 'llm.model') || getSpanAttr(attrs, 'prov.llm.model') || 'unknown',
      latencyMs: span ? span.durationNanos / 1e6 : t.durationMs,
      tokensIn: parseInt(getSpanAttr(attrs, 'llm.usage.prompt_tokens') || '0'),
      tokensOut: parseInt(getSpanAttr(attrs, 'llm.usage.completion_tokens') || '0'),
      costUsd: parseFloat(getSpanAttr(attrs, 'span.cost.usd') || '0'),
      sessionId: getSpanAttr(attrs, 'session.id') || getSpanAttr(attrs, 'agent.session_id') || '—',
      attributes: allAttrs,
    }
  })
}

export interface TempoMetricResult {
  series: Array<{
    labels: Record<string, string>
    samples: Array<{ timestamp_ms: number; value: string }>
  }>
}

export function extractLastTempoMetricValue(result: TempoMetricResult | null): number | null {
  if (!result?.series?.length) return null
  // sum the last value of each series
  let total = 0
  for (const s of result.series) {
    if (s.samples.length > 0) {
      total += parseFloat(s.samples[s.samples.length - 1].value) || 0
    }
  }
  return total
}
