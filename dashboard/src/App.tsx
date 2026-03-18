import React, { useState, useEffect, useCallback } from 'react'
import { Activity, DollarSign, Zap, RefreshCw, GitBranch, BarChart2 } from 'lucide-react'
import { Header } from './components/Header'
import { StatCard } from './components/StatCard'
import { TimeSeriesChart } from './components/TimeSeriesChart'
import { BarChartPanel } from './components/BarChart'
import { TraceTable } from './components/TraceTable'
import { AgentAttribution } from './components/AgentAttribution'
import { SessionExplorer } from './components/SessionExplorer'
import {
  TimeRange,
  tempoSearchQuery,
  tempoCostTimeSeriesQuery,
  tempoAgentAttributionQuery,
  tempoSubagentSearchQuery,
  promLLMCallsRateQuery,
  promCallsByModelQuery,
  promP95LatencyByModelQuery,
  promCallsByAgentQuery,
  transformTempoTraces,
  transformTempoCostSeries,
  transformAgentAttributionTraces,
  transformSubagentTraces,
  buildCostTimeSeries,
  buildCostByAgent,
} from './lib/queries'
import { useTempoSearch, useTempoSearchCount, useTempoMetrics, useSessionGraph } from './hooks/useTempo'
import { usePromQueryRange, usePromQueryInstant } from './hooks/usePrometheus'

type ActiveTab = 'overview' | 'sessions'

const REFRESH_INTERVAL_MS = 60_000 // 60s

export default function App() {
  const [timeRange, setTimeRange] = useState<TimeRange>('6h')
  const [refreshKey, setRefreshKey] = useState(0)
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)
  const [activeTab, setActiveTab] = useState<ActiveTab>('overview')

  // Auto-refresh every 60s
  useEffect(() => {
    const id = setInterval(() => {
      setRefreshKey((k) => k + 1)
      setLastUpdated(new Date())
    }, REFRESH_INTERVAL_MS)
    return () => clearInterval(id)
  }, [])

  const handleRefresh = useCallback(() => {
    setRefreshKey((k) => k + 1)
    setLastUpdated(new Date())
  }, [])

  const handleTimeRangeChange = useCallback((range: TimeRange) => {
    setTimeRange(range)
    setRefreshKey((k) => k + 1)
    setLastUpdated(new Date())
  }, [])

  // Set initial lastUpdated
  useEffect(() => {
    setLastUpdated(new Date())
  }, [])

  // ─── Trace data (drives table + stat card aggregations) ──────────────────

  // Fetch up to 1000 traces with span attributes (used for table AND stat aggregation)
  const { traces: rawTraces, loading: tracesLoading, error: tracesError } =
    useTempoSearch(tempoSearchQuery(), timeRange, refreshKey)

  const traceRows = transformTempoTraces(rawTraces)

  // ─── Stat Card Data ───────────────────────────────────────────────────────

  // 1. Total LLM Calls (separate high-limit query for accurate count)
  const { count: llmCallCount, loading: llmCallLoading, error: llmCallError } =
    useTempoSearchCount(tempoSearchQuery(), timeRange, refreshKey)

  // 2. Total Cost — summed from trace attributes
  const costValue = tracesLoading ? null : traceRows.reduce((s, t) => s + t.costUsd, 0)

  // 3. Avg Cache Hit Rate — averaged across traces that have the attribute
  const tracesWithCache = traceRows.filter((t) => t.cacheHitRate > 0)
  const cacheValue = tracesLoading
    ? null
    : tracesWithCache.length > 0
      ? tracesWithCache.reduce((s, t) => s + t.cacheHitRate, 0) / tracesWithCache.length
      : null

  // 4. Avg Latency — computed from trace durations
  const avgLatencyValue = tracesLoading
    ? null
    : traceRows.length > 0
      ? traceRows.reduce((s, t) => s + t.latencyMs, 0) / traceRows.length
      : null

  // ─── Time Series Data ─────────────────────────────────────────────────────

  // 5. LLM Calls over Time (Prometheus spanmetrics, grouped by model)
  const { series: callsSeries, loading: callsSeriesLoading, error: callsSeriesError } =
    usePromQueryRange(promLLMCallsRateQuery(), timeRange, refreshKey, 'prov_llm_model')

  // 6. Cost over Time — traceqlmetrics time series (accurate across all spans)
  const { result: costMetricResult, loading: costMetricLoading, error: costMetricError } =
    useTempoMetrics(tempoCostTimeSeriesQuery(), timeRange, refreshKey)

  const costMetricSeries = transformTempoCostSeries(costMetricResult)
  // Fall back to trace-derived series when traceqlmetrics returns empty (known issue with long windows)
  const costChartSeries = costMetricSeries.length > 0
    ? costMetricSeries
    : buildCostTimeSeries(traceRows, timeRange)
  const costChartSubtitle = costMetricSeries.length > 0
    ? 'Cost per time bucket (all spans)'
    : 'Cost per time bucket (based on loaded traces only)'

  // ─── Bar Chart Data ───────────────────────────────────────────────────────

  // 7. Calls by Model
  const { bars: callsByModel, loading: callsByModelLoading, error: callsByModelError } =
    usePromQueryInstant(promCallsByModelQuery(timeRange), timeRange, refreshKey, 'prov_llm_model')

  // 8. P95 Latency by Model
  const { bars: latencyByModel, loading: latencyByModelLoading, error: latencyByModelError } =
    usePromQueryInstant(promP95LatencyByModelQuery(), timeRange, refreshKey, 'prov_llm_model')

  // 9. Calls by Agent (Prometheus spanmetrics)
  const { bars: callsByAgent, loading: callsByAgentLoading, error: callsByAgentError } =
    usePromQueryInstant(promCallsByAgentQuery(timeRange), timeRange, refreshKey, 'prov_agent_id')

  // 10. Cost by Agent — derived from trace rows (no extra fetch needed)
  const costByAgent = buildCostByAgent(traceRows)

  // ─── Agent Attribution Data ─────────────────────────────────────────────────

  // 11. Agent attribution traces (prov.agent.type, prov.parent.session.id, etc.)
  const { traces: rawAttribution, loading: attrLoading, error: attrError } =
    useTempoSearch(tempoAgentAttributionQuery(), timeRange, refreshKey)

  const attributionRows = transformAgentAttributionTraces(rawAttribution)

  // 12. Sub-agent only traces
  const { traces: rawSubagent, loading: subagentLoading, error: subagentError } =
    useTempoSearch(tempoSubagentSearchQuery(), timeRange, refreshKey)

  const subagentRows = transformSubagentTraces(rawSubagent)

  // 13. Session graph data (Session Explorer tab)
  const { nodes: sessionNodes, edges: sessionEdges, rawTraces: sessionRawTraces, loading: sessionLoading, error: sessionError } =
    useSessionGraph(timeRange, refreshKey)

  // Tempo error flag — only true when the core trace search fails
  const tempoError = !!(llmCallError || tracesError)

  return (
    <div className="min-h-screen bg-[#0a0a0f] overflow-x-hidden">
      <Header
        timeRange={timeRange}
        onTimeRangeChange={handleTimeRangeChange}
        onRefresh={handleRefresh}
        lastUpdated={lastUpdated}
        tempoError={tempoError}
      />

      {/* Tab navigation */}
      <div className="border-b border-slate-800 bg-[#0d0d14]">
        <div className="max-w-screen-2xl mx-auto px-4 sm:px-6 flex gap-1">
          <button
            onClick={() => setActiveTab('overview')}
            className={`flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 transition-colors ${
              activeTab === 'overview'
                ? 'border-indigo-500 text-indigo-300'
                : 'border-transparent text-slate-500 hover:text-slate-300'
            }`}
          >
            <BarChart2 className="w-4 h-4" />
            Overview
          </button>
          <button
            onClick={() => setActiveTab('sessions')}
            className={`flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 transition-colors ${
              activeTab === 'sessions'
                ? 'border-indigo-500 text-indigo-300'
                : 'border-transparent text-slate-500 hover:text-slate-300'
            }`}
          >
            <GitBranch className="w-4 h-4" />
            Session Explorer
            {sessionNodes.length > 0 && (
              <span className="ml-1 text-xs bg-indigo-500/20 text-indigo-400 rounded-full px-1.5 py-0.5">
                {sessionNodes.length}
              </span>
            )}
          </button>
        </div>
      </div>

      <main className="max-w-screen-2xl mx-auto px-4 sm:px-6 py-6 space-y-6">
        {/* Session Explorer tab */}
        {activeTab === 'sessions' && (
          <SessionExplorer
            nodes={sessionNodes}
            edges={sessionEdges}
            rawTraces={sessionRawTraces}
            loading={sessionLoading}
            error={sessionError}
          />
        )}

        {/* Overview tab — all existing content */}
        {activeTab === 'overview' && (<>
        {/* Row 1: Stat Cards */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          <StatCard
            icon={Activity}
            iconColor="text-indigo-400"
            iconBg="bg-indigo-500/10"
            label="Total LLM Calls"
            value={llmCallCount !== null ? llmCallCount.toLocaleString() : null}
            loading={llmCallLoading}
            error={llmCallError}
          />
          <StatCard
            icon={DollarSign}
            iconColor="text-emerald-400"
            iconBg="bg-emerald-500/10"
            label="Total Cost (USD)"
            value={costValue !== null ? `$${costValue.toFixed(4)}` : null}
            loading={tracesLoading}
            error={tracesError}
          />
          <StatCard
            icon={Zap}
            iconColor="text-amber-400"
            iconBg="bg-amber-500/10"
            label="Avg Cache Hit Rate"
            value={cacheValue !== null ? `${(cacheValue * 100).toFixed(1)}%` : null}
            loading={tracesLoading}
            error={tracesError}
          />
          <StatCard
            icon={RefreshCw}
            iconColor="text-cyan-400"
            iconBg="bg-cyan-500/10"
            label="Avg Latency"
            value={avgLatencyValue !== null ? `${(avgLatencyValue / 1000).toFixed(1)}s` : null}
            loading={tracesLoading}
            error={tracesError}
          />
        </div>

        {/* Row 2: Time Series Charts */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <TimeSeriesChart
            title="LLM Calls over Time"
            subtitle="Calls per 5-minute window"
            series={callsSeries.length ? callsSeries : []}
            loading={callsSeriesLoading}
            error={callsSeriesError}
            type="line"
            valueFormatter={(v) => v.toFixed(0)}
          />
          <TimeSeriesChart
            title="Cost over Time (USD)"
            subtitle={costChartSubtitle}
            series={costChartSeries}
            loading={costMetricLoading && tracesLoading}
            error={costChartSeries.length === 0 ? (costMetricError ?? tracesError) : null}
            type="area"
            valueFormatter={(v) => `$${v.toFixed(4)}`}
          />
        </div>

        {/* Row 3: Bar Charts — Model breakdown */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <BarChartPanel
            title="Calls by Model"
            subtitle="Total calls per model in time range"
            data={callsByModel}
            loading={callsByModelLoading}
            error={callsByModelError}
            valueFormatter={(v) => v.toFixed(0)}
          />
          <BarChartPanel
            title="P95 Latency by Model"
            subtitle="95th percentile latency in ms"
            data={latencyByModel}
            loading={latencyByModelLoading}
            error={latencyByModelError}
            valueFormatter={(v) => `${v.toFixed(0)} ms`}
          />
        </div>

        {/* Row 4: Agent breakdown */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <BarChartPanel
            title="Calls by Agent"
            subtitle="Total LLM calls per agent in time range"
            data={callsByAgent}
            loading={callsByAgentLoading}
            error={callsByAgentError}
            valueFormatter={(v) => v.toFixed(0)}
          />
          <BarChartPanel
            title="Cost by Agent (USD)"
            subtitle="Estimated total cost per agent in time range"
            data={costByAgent}
            loading={tracesLoading}
            error={null}
            valueFormatter={(v) => `$${v.toFixed(4)}`}
          />
        </div>

        {/* Agent Attribution Section */}
        <AgentAttribution
          attributionRows={attributionRows}
          attributionLoading={attrLoading}
          attributionError={attrError}
          subagentRows={subagentRows}
          subagentLoading={subagentLoading}
          subagentError={subagentError}
        />

        {/* Row 5: Trace Table */}
        <TraceTable
          traces={traceRows}
          loading={tracesLoading}
          error={tracesError}
        />
        </>)}
      </main>
    </div>
  )
}
