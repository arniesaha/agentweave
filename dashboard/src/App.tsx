import React, { useState, useEffect, useCallback } from 'react'
import { Activity, DollarSign, Zap, RefreshCw } from 'lucide-react'
import { Header } from './components/Header'
import { StatCard } from './components/StatCard'
import { TimeSeriesChart } from './components/TimeSeriesChart'
import { BarChartPanel } from './components/BarChart'
import { TraceTable } from './components/TraceTable'
import {
  TimeRange,
  tempoSearchQuery,
  tempoCostMetricQuery,
  tempoCacheHitQuery,
  tempoTurnCountQuery,
  promLLMCallsRateQuery,
  promCallsByModelQuery,
  promP95LatencyByModelQuery,
  transformTempoTraces,
  extractLastTempoMetricValue,
} from './lib/queries'
import { useTempoSearch, useTempoSearchCount, useTempoMetrics } from './hooks/useTempo'
import { usePromQueryRange, usePromQueryInstant } from './hooks/usePrometheus'

const REFRESH_INTERVAL_MS = 60_000 // 60s

export default function App() {
  const [timeRange, setTimeRange] = useState<TimeRange>('6h')
  const [refreshKey, setRefreshKey] = useState(0)
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)

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

  // ─── Stat Card Data ───────────────────────────────────────────────────────

  // 1. Total LLM Calls
  const { count: llmCallCount, loading: llmCallLoading, error: llmCallError } =
    useTempoSearchCount(tempoSearchQuery(), timeRange, refreshKey)

  // 2. Total Cost
  const { result: costResult, loading: costLoading, error: costError } =
    useTempoMetrics(tempoCostMetricQuery(), timeRange, refreshKey)

  // 3. Cache Hit Rate
  const { result: cacheResult, loading: cacheLoading, error: cacheError } =
    useTempoMetrics(tempoCacheHitQuery(), timeRange, refreshKey)

  // 4. Avg Turns/Task
  const { result: turnResult, loading: turnLoading, error: turnError } =
    useTempoMetrics(tempoTurnCountQuery(), timeRange, refreshKey)

  // ─── Time Series Data ─────────────────────────────────────────────────────

  // 5. LLM Calls over Time
  const { series: callsSeries, loading: callsSeriesLoading, error: callsSeriesError } =
    usePromQueryRange(promLLMCallsRateQuery(), timeRange, refreshKey)

  // 6. Cost over Time (Tempo metrics)
  const { result: costSeries, loading: costSeriesLoading, error: costSeriesError } =
    useTempoMetrics(tempoCostMetricQuery(), timeRange, refreshKey)

  // ─── Bar Chart Data ───────────────────────────────────────────────────────

  // 7. Calls by Model
  const { bars: callsByModel, loading: callsByModelLoading, error: callsByModelError } =
    usePromQueryInstant(promCallsByModelQuery(timeRange), timeRange, refreshKey, 'prov_llm_model')

  // 8. P95 Latency by Model
  const { bars: latencyByModel, loading: latencyByModelLoading, error: latencyByModelError } =
    usePromQueryInstant(promP95LatencyByModelQuery(), timeRange, refreshKey, 'prov_llm_model')

  // ─── Trace Table ──────────────────────────────────────────────────────────

  // 9. Recent traces
  const { traces: rawTraces, loading: tracesLoading, error: tracesError } =
    useTempoSearch(tempoSearchQuery(), timeRange, refreshKey)

  const traceRows = transformTempoTraces(rawTraces)

  // ─── Derived values ───────────────────────────────────────────────────────

  const costValue = extractLastTempoMetricValue(costResult)
  const cacheValue = extractLastTempoMetricValue(cacheResult)
  const turnValue = extractLastTempoMetricValue(turnResult)

  // Convert cost Tempo series to recharts format
  const costChartSeries: Array<{ label: string; points: Array<{ time: number; value: number }> }> = []
  if (costSeries?.series?.length) {
    const allPoints: Array<{ time: number; value: number }> = []
    costSeries.series.forEach((s) => {
      s.samples.forEach((sample) => {
        allPoints.push({
          time: sample.timestamp_ms,
          value: parseFloat(sample.value) || 0,
        })
      })
    })
    allPoints.sort((a, b) => a.time - b.time)
    if (allPoints.length > 0) {
      costChartSeries.push({ label: 'Cost USD', points: allPoints })
    }
  }

  // Any Tempo error
  const tempoError = !!(llmCallError || costError || cacheError || turnError || tracesError)

  return (
    <div className="min-h-screen bg-[#0a0a0f]">
      <Header
        timeRange={timeRange}
        onTimeRangeChange={handleTimeRangeChange}
        onRefresh={handleRefresh}
        lastUpdated={lastUpdated}
        tempoError={tempoError}
      />

      <main className="max-w-screen-2xl mx-auto px-4 sm:px-6 py-6 space-y-6">
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
            loading={costLoading}
            error={costError}
          />
          <StatCard
            icon={Zap}
            iconColor="text-amber-400"
            iconBg="bg-amber-500/10"
            label="Cache Hit Rate"
            value={cacheValue !== null ? `${(cacheValue * 100).toFixed(1)}%` : null}
            loading={cacheLoading}
            error={cacheError}
          />
          <StatCard
            icon={RefreshCw}
            iconColor="text-cyan-400"
            iconBg="bg-cyan-500/10"
            label="Avg Turns / Task"
            value={turnValue !== null ? turnValue.toFixed(1) : null}
            loading={turnLoading}
            error={turnError}
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
            subtitle="Cumulative cost per bucket"
            series={costChartSeries}
            loading={costSeriesLoading}
            error={costSeriesError}
            type="area"
            valueFormatter={(v) => `$${v.toFixed(4)}`}
          />
        </div>

        {/* Row 3: Bar Charts */}
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

        {/* Row 4: Trace Table */}
        <TraceTable
          traces={traceRows}
          loading={tracesLoading}
          error={tracesError}
        />
      </main>
    </div>
  )
}
