// All query strings and transformers for AgentWeave Dashboard

export type TimeRange = '15m' | '1h' | '3h' | '6h' | '24h' | '7d'

export function getTimeRangeSeconds(range: TimeRange): number {
  const map: Record<TimeRange, number> = {
    '15m': 900,
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
    '15m': 30,
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

export function tempoSearchQuery(project?: string): string {
  // select() fetches span attributes; used for both trace table and stat card aggregation
  const projectFilter = project ? ` && span.prov.project = "${project}"` : ''
  return (
    `{ resource.service.name = "${TEMPO_SERVICE}" && name != "llm.unknown"${projectFilter} }` +
    ` | select(span.prov.llm.model, span.cost.usd, span.prov.llm.prompt_tokens,` +
    ` span.prov.llm.completion_tokens, span.cache.hit_rate, span.session.id, span.prov.agent.id, span.prov.project)`
  )
}

// ─── Prometheus Queries ───────────────────────────────────────────────────────

export function promLLMCallsRateQuery(): string {
  return `sum by (prov_llm_model) (rate(traces_spanmetrics_calls_total{service="${TEMPO_SERVICE}"}[5m])) * 300`
}

export function promCallsByModelQuery(range: TimeRange): string {
  const seconds = getTimeRangeSeconds(range)
  return `sum by (prov_llm_model) (increase(traces_spanmetrics_calls_total{service="${TEMPO_SERVICE}"}[${seconds}s]))`
}

export function promP95LatencyByModelQuery(): string {
  return `histogram_quantile(0.95, sum by (le, prov_llm_model) (rate(traces_spanmetrics_latency_bucket{service="${TEMPO_SERVICE}"}[5m]))) * 1000`
}

export function promCallsByAgentQuery(range: TimeRange): string {
  const seconds = getTimeRangeSeconds(range)
  return `sum by (prov_agent_id) (increase(traces_spanmetrics_calls_total{service="${TEMPO_SERVICE}"}[${seconds}s]))`
}

export function promCostByAgentQuery(range: TimeRange): string {
  // Uses Prometheus spanmetrics for call counts as a proxy; actual cost bucketed from traces
  // This gives relative call volume per agent — use alongside cost stat for context
  const seconds = getTimeRangeSeconds(range)
  return `sum by (prov_agent_id) (increase(traces_spanmetrics_calls_total{service="${TEMPO_SERVICE}"}[${seconds}s]))`
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
  agentId: string
  latencyMs: number
  tokensIn: number
  tokensOut: number
  costUsd: number
  cacheHitRate: number
  sessionId: string
  project: string
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

    // Fall back to parsing model from root trace name (e.g. "llm.claude-sonnet-4-6")
    const modelFromName = t.rootTraceName?.startsWith('llm.')
      ? t.rootTraceName.slice(4)
      : ''

    return {
      traceId: t.traceID,
      time: parseInt(t.startTimeUnixNano) / 1e6,
      model: getSpanAttr(attrs, 'prov.llm.model') || modelFromName || 'unknown',
      latencyMs: span ? span.durationNanos / 1e6 : t.durationMs,
      tokensIn: parseInt(getSpanAttr(attrs, 'prov.llm.prompt_tokens') || '0'),
      tokensOut: parseInt(getSpanAttr(attrs, 'prov.llm.completion_tokens') || '0'),
      costUsd: Math.max(0, parseFloat(getSpanAttr(attrs, 'cost.usd') || '0') || 0),
      cacheHitRate: Math.max(0, parseFloat(getSpanAttr(attrs, 'cache.hit_rate') || '0') || 0),
      sessionId: getSpanAttr(attrs, 'session.id') || '—',
      agentId: getSpanAttr(attrs, 'prov.agent.id') || 'unknown',
      project: getSpanAttr(attrs, 'prov.project') || '',
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

// ─── Tempo Metrics queries ────────────────────────────────────────────────────

/** TraceQL search query returning all spans for a specific session. */
export function tempoSessionQuery(sessionId: string): string {
  // Search both session.id (main agents) and prov.session.id (sub-agents) so
  // clicking any node in the session graph finds its traces.
  return (
    `{ resource.service.name = "${TEMPO_SERVICE}" && (span.session.id = "${sessionId}" || span.prov.session.id = "${sessionId}") }` +
    ` | select(span.prov.llm.model, span.cost.usd, span.prov.llm.prompt_tokens,` +
    ` span.prov.llm.completion_tokens, span.cache.hit_rate, span.prov.agent.id)`
  )
}

/** TraceQL metrics query that returns cost.usd summed per time bucket. */
export function tempoCostTimeSeriesQuery(): string {
  return (
    `{ resource.service.name = "${TEMPO_SERVICE}" && name != "llm.unknown" }` +
    ` | sum(span.cost.usd)`
  )
}

/**
 * Transform a TempoMetricResult into the series format expected by TimeSeriesChart.
 * Aggregates all series into a single "Cost USD" series (sum across labels).
 */
export function transformTempoCostSeries(
  result: TempoMetricResult | null,
): Array<{ label: string; points: Array<{ time: number; value: number }> }> {
  if (!result?.series?.length) return []

  // Merge all series into a single time-bucketed map (sum across any label dimensions)
  const buckets = new Map<number, number>()
  for (const s of result.series) {
    for (const sample of s.samples) {
      const v = parseFloat(sample.value) || 0
      if (v <= 0) continue
      buckets.set(sample.timestamp_ms, (buckets.get(sample.timestamp_ms) ?? 0) + v)
    }
  }

  if (!buckets.size) return []

  const points = Array.from(buckets.entries())
    .sort((a, b) => a[0] - b[0])
    .map(([time, value]) => ({ time, value }))

  return [{ label: 'Cost USD', points }]
}

// ─── Agent Attribution queries ────────────────────────────────────────────────

/** TraceQL search selecting sub-agent attribution attributes. */
export function tempoAgentAttributionQuery(): string {
  return (
    `{ resource.service.name = "${TEMPO_SERVICE}" && name != "llm.unknown" }` +
    ` | select(span.prov.agent.type, span.prov.parent.session.id, span.prov.session.turn,` +
    ` span.session.id, span.cost.usd, span.prov.agent.id)`
  )
}

/** TraceQL search for session graph: fetches all spans with session identity attributes. */
export function tempoSessionGraphQuery(): string {
  return (
    `{ resource.service.name = "${TEMPO_SERVICE}" && name != "llm.unknown" }` +
    ` | select(span.prov.session.id, span.session.id, span.prov.parent.session.id, span.prov.task.label,` +
    ` span.prov.agent.id, span.prov.agent.type, span.cost.usd, span.prov.llm.model,` +
    ` span.prov.llm.prompt_tokens, span.prov.llm.completion_tokens, span.prov.project)`
  )
}

/** TraceQL search for sub-agent spans only. */
export function tempoSubagentSearchQuery(): string {
  return (
    `{ resource.service.name = "${TEMPO_SERVICE}" && span.prov.agent.type = "subagent" }` +
    ` | select(span.prov.llm.model, span.cost.usd, span.prov.llm.prompt_tokens,` +
    ` span.prov.llm.completion_tokens, span.prov.parent.session.id, span.session.id,` +
    ` span.prov.agent.id, span.prov.agent.type)`
  )
}

// ─── Agent Attribution types & transformers ──────────────────────────────────

export interface AgentAttributionRow {
  traceId: string
  time: number
  agentType: string
  parentSessionId: string
  sessionTurn: number
  sessionId: string
  costUsd: number
  agentId: string
}

export function transformAgentAttributionTraces(traces: TempoSpan[]): AgentAttributionRow[] {
  return traces.map((t) => {
    const spans = t.spanSets?.[0]?.spans ?? t.spanSet?.spans ?? []
    const span = spans[0]
    const attrs = span?.attributes ?? []
    return {
      traceId: t.traceID,
      time: parseInt(t.startTimeUnixNano) / 1e6,
      agentType: getSpanAttr(attrs, 'prov.agent.type') || 'unknown',
      parentSessionId: getSpanAttr(attrs, 'prov.parent.session.id'),
      sessionTurn: parseInt(getSpanAttr(attrs, 'prov.session.turn') || '0'),
      sessionId: getSpanAttr(attrs, 'session.id') || '',
      costUsd: Math.max(0, parseFloat(getSpanAttr(attrs, 'cost.usd') || '0') || 0),
      agentId: getSpanAttr(attrs, 'prov.agent.id') || 'unknown',
    }
  })
}

/** Group agent attribution rows by prov.agent.type and return counts. */
export function buildCallsByAgentType(
  rows: AgentAttributionRow[]
): Array<{ label: string; value: number }> {
  const map = new Map<string, number>()
  for (const r of rows) {
    if (!r.agentType || r.agentType === 'unknown') continue
    map.set(r.agentType, (map.get(r.agentType) ?? 0) + 1)
  }
  return Array.from(map.entries())
    .map(([label, value]) => ({ label, value }))
    .sort((a, b) => b.value - a.value)
}

export interface SessionOverviewRow {
  sessionId: string
  agentType: string
  callCount: number
  totalCost: number
  hasSubAgents: boolean
  lastActive: number
}

/** Group agent attribution rows by session.id for the session overview table. */
export function buildSessionOverview(rows: AgentAttributionRow[]): SessionOverviewRow[] {
  const map = new Map<string, {
    agentType: string
    callCount: number
    totalCost: number
    hasSubAgents: boolean
    lastActive: number
  }>()

  for (const r of rows) {
    if (!r.sessionId) continue
    const existing = map.get(r.sessionId)
    if (existing) {
      existing.callCount++
      existing.totalCost += r.costUsd
      if (r.parentSessionId) existing.hasSubAgents = true
      if (r.time > existing.lastActive) existing.lastActive = r.time
    } else {
      map.set(r.sessionId, {
        agentType: r.agentType,
        callCount: 1,
        totalCost: r.costUsd,
        hasSubAgents: !!r.parentSessionId,
        lastActive: r.time,
      })
    }
  }

  return Array.from(map.entries())
    .map(([sessionId, data]) => ({ sessionId, ...data }))
    .sort((a, b) => b.lastActive - a.lastActive)
}

export interface SubagentTraceRow {
  traceId: string
  time: number
  model: string
  agentId: string
  tokensIn: number
  tokensOut: number
  costUsd: number
  sessionId: string
  parentSessionId: string
}

export function transformSubagentTraces(traces: TempoSpan[]): SubagentTraceRow[] {
  return traces.map((t) => {
    const spans = t.spanSets?.[0]?.spans ?? t.spanSet?.spans ?? []
    const span = spans[0]
    const attrs = span?.attributes ?? []
    const modelFromName = t.rootTraceName?.startsWith('llm.')
      ? t.rootTraceName.slice(4)
      : ''
    return {
      traceId: t.traceID,
      time: parseInt(t.startTimeUnixNano) / 1e6,
      model: getSpanAttr(attrs, 'prov.llm.model') || modelFromName || 'unknown',
      agentId: getSpanAttr(attrs, 'prov.agent.id') || 'unknown',
      tokensIn: parseInt(getSpanAttr(attrs, 'prov.llm.prompt_tokens') || '0'),
      tokensOut: parseInt(getSpanAttr(attrs, 'prov.llm.completion_tokens') || '0'),
      costUsd: Math.max(0, parseFloat(getSpanAttr(attrs, 'cost.usd') || '0') || 0),
      sessionId: getSpanAttr(attrs, 'session.id') || '—',
      parentSessionId: getSpanAttr(attrs, 'prov.parent.session.id') || '—',
    }
  })
}

// ─── Trace-derived aggregations ──────────────────────────────────────────────

/** Aggregate total cost per agent from trace rows. */
export function buildCostByAgent(
  traces: TraceRow[]
): Array<{ label: string; value: number }> {
  const map = new Map<string, number>()
  for (const t of traces) {
    if (t.costUsd <= 0) continue
    const key = t.agentId || 'unknown'
    map.set(key, (map.get(key) ?? 0) + t.costUsd)
  }
  return Array.from(map.entries())
    .map(([label, value]) => ({ label, value }))
    .sort((a, b) => b.value - a.value)
}

/** Bucket trace costs into a time series for the cost-over-time chart. */
export function buildCostTimeSeries(
  traces: TraceRow[],
  timeRange: TimeRange
): Array<{ label: string; points: Array<{ time: number; value: number }> }> {
  if (!traces.length) return []
  const stepMs = getStepForRange(timeRange) * 1000
  const buckets = new Map<number, number>()
  for (const t of traces) {
    if (t.costUsd <= 0) continue
    const bucket = Math.floor(t.time / stepMs) * stepMs
    buckets.set(bucket, (buckets.get(bucket) ?? 0) + t.costUsd)
  }
  const points = Array.from(buckets.entries())
    .sort((a, b) => a[0] - b[0])
    .map(([time, value]) => ({ time, value }))
  return points.length ? [{ label: 'Cost USD', points }] : []
}

// ─── Session Graph types & transformers ──────────────────────────────────────

/** A single aggregated session node (grouped from many spans). */
export interface SessionNode {
  sessionId: string
  agentId: string
  agentType: string          // 'main' | 'subagent' | 'unknown'
  taskLabel: string
  parentSessionId: string    // '' for root sessions
  callCount: number
  totalCost: number
  tokensIn: number
  tokensOut: number
  firstSeen: number          // unix ms
  lastSeen: number           // unix ms
  durationMs: number
  hasError: boolean
  project?: string           // prov.project value (issue #101)
}

/** An edge between two session nodes (parent → child). */
export interface SessionEdge {
  from: string  // parent sessionId
  to: string    // child sessionId
}

/** A single LLM call within a session (for the detail timeline). */
export interface SessionCallRow {
  traceId: string
  time: number
  model: string
  tokensIn: number
  tokensOut: number
  costUsd: number
  latencyMs?: number
}

/** Raw span row used when building session graph; carries all session attributes. */
interface SessionSpanRow {
  traceId: string
  time: number
  sessionId: string
  parentSessionId: string
  taskLabel: string
  agentId: string
  agentType: string
  costUsd: number
  tokensIn: number
  tokensOut: number
  model: string
  durationNanos: number
  project: string
}

function spanRowFromTempoSpan(t: TempoSpan): SessionSpanRow {
  const spans = t.spanSets?.[0]?.spans ?? t.spanSet?.spans ?? []
  const span = spans[0]
  const attrs = span?.attributes ?? []
  const modelFromName = t.rootTraceName?.startsWith('llm.') ? t.rootTraceName.slice(4) : ''
  return {
    traceId: t.traceID,
    time: parseInt(t.startTimeUnixNano) / 1e6,
    // Fall back to session.id when prov.session.id is absent (main/parent sessions
    // may only set session.id, while sub-agents set prov.session.id explicitly).
    sessionId: getSpanAttr(attrs, 'prov.session.id') || getSpanAttr(attrs, 'session.id') || '',
    parentSessionId: getSpanAttr(attrs, 'prov.parent.session.id') || '',
    taskLabel: getSpanAttr(attrs, 'prov.task.label') || '',
    agentId: getSpanAttr(attrs, 'prov.agent.id') || 'unknown',
    agentType: getSpanAttr(attrs, 'prov.agent.type') || 'unknown',
    costUsd: Math.max(0, parseFloat(getSpanAttr(attrs, 'cost.usd') || '0') || 0),
    tokensIn: parseInt(getSpanAttr(attrs, 'prov.llm.prompt_tokens') || '0'),
    tokensOut: parseInt(getSpanAttr(attrs, 'prov.llm.completion_tokens') || '0'),
    model: getSpanAttr(attrs, 'prov.llm.model') || modelFromName || 'unknown',
    durationNanos: span?.durationNanos ?? 0,
    project: getSpanAttr(attrs, 'prov.project'),
  }
}

/** Build SessionNode map + SessionEdge list from raw Tempo spans. */
export function buildSessionGraph(
  traces: TempoSpan[]
): { nodes: SessionNode[]; edges: SessionEdge[] } {
  const nodeMap = new Map<string, SessionNode>()
  const edgeSet = new Set<string>()

  for (const t of traces) {
    const row = spanRowFromTempoSpan(t)
    if (!row.sessionId) continue

    const existing = nodeMap.get(row.sessionId)
    if (existing) {
      existing.callCount++
      existing.totalCost += row.costUsd
      existing.tokensIn += row.tokensIn
      existing.tokensOut += row.tokensOut
      if (row.time < existing.firstSeen) existing.firstSeen = row.time
      if (row.time > existing.lastSeen) existing.lastSeen = row.time
      if (row.taskLabel && !existing.taskLabel) existing.taskLabel = row.taskLabel
      if (row.parentSessionId && !existing.parentSessionId) existing.parentSessionId = row.parentSessionId
      if (row.project && !existing.project) existing.project = row.project
      if (row.agentType && row.agentType !== 'unknown' && existing.agentType === 'unknown') {
        existing.agentType = row.agentType
      }
      existing.durationMs = existing.lastSeen - existing.firstSeen
    } else {
      nodeMap.set(row.sessionId, {
        sessionId: row.sessionId,
        agentId: row.agentId,
        agentType: row.agentType,
        taskLabel: row.taskLabel,
        parentSessionId: row.parentSessionId,
        callCount: 1,
        totalCost: row.costUsd,
        tokensIn: row.tokensIn,
        tokensOut: row.tokensOut,
        firstSeen: row.time,
        lastSeen: row.time,
        durationMs: 0,
        hasError: false,
        project: row.project || undefined,
      })
    }

    if (row.parentSessionId) {
      const edgeKey = `${row.parentSessionId}->${row.sessionId}`
      if (!edgeSet.has(edgeKey)) {
        edgeSet.add(edgeKey)
      }
    }
  }

  // Build edge list — only include edges where both nodes are known
  const edges: SessionEdge[] = []
  for (const edgeKey of edgeSet) {
    const [from, to] = edgeKey.split('->')
    if (nodeMap.has(from) && nodeMap.has(to)) {
      edges.push({ from, to })
    }
  }

  // Sort nodes: roots first, then by firstSeen desc
  const nodes = Array.from(nodeMap.values()).sort((a, b) => {
    if (!a.parentSessionId && b.parentSessionId) return -1
    if (a.parentSessionId && !b.parentSessionId) return 1
    return b.firstSeen - a.firstSeen
  })

  return { nodes, edges }
}

/** Build session call rows (LLM call timeline) for the session detail view. */
export function buildSessionCalls(
  traces: TempoSpan[],
  sessionId: string
): SessionCallRow[] {
  const rows: SessionCallRow[] = []
  for (const t of traces) {
    const row = spanRowFromTempoSpan(t)
    if (row.sessionId !== sessionId) continue
    rows.push({
      traceId: t.traceID,
      time: row.time,
      model: row.model,
      tokensIn: row.tokensIn,
      tokensOut: row.tokensOut,
      costUsd: row.costUsd,
      latencyMs: row.durationNanos / 1e6,
    })
  }
  return rows.sort((a, b) => a.time - b.time)
}

/** Daily summary stats derived from session nodes. */
export interface DailySummary {
  topLevelSessions: number
  subAgentSessions: number
  totalCost: number
  totalCalls: number
}

export function buildDailySummary(nodes: SessionNode[]): DailySummary {
  const topLevel = nodes.filter((n) => !n.parentSessionId)
  const subAgents = nodes.filter((n) => !!n.parentSessionId)
  return {
    topLevelSessions: topLevel.length,
    subAgentSessions: subAgents.length,
    totalCost: nodes.reduce((s, n) => s + n.totalCost, 0),
    totalCalls: nodes.reduce((s, n) => s + n.callCount, 0),
  }
}

// ─── Project tracking (issue #101) ─────────────────────────────────────────

/** Extract distinct prov.project values from trace rows. */
export function extractProjects(traces: TraceRow[]): string[] {
  const set = new Set<string>()
  for (const t of traces) {
    if (t.project) set.add(t.project)
  }
  return Array.from(set).sort()
}
