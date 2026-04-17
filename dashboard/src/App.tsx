import React, { useState, useEffect, useCallback, useMemo } from 'react'
import { Activity, DollarSign, Zap, RefreshCw, GitBranch, BarChart2, X, PlayCircle, Route } from 'lucide-react'
import { Header } from './components/Header'
import { StatCard } from './components/StatCard'
import { TimeSeriesChart } from './components/TimeSeriesChart'
import { BarChartPanel } from './components/BarChart'
import { TraceTable } from './components/TraceTable'
import { AgentAttribution } from './components/AgentAttribution'
import { CostSparklines } from './components/CostSparklines'
import { AgentHealthBadges } from './components/AgentHealthBadges'
import { SessionExplorer } from './components/SessionExplorer'
import { PromptVersionFilter, filterByPromptVersion, filterTempoSpansByPromptVersion } from './components/PromptVersionFilter'
import { SessionReplay } from './components/SessionReplay'
import { MuxRouting } from './components/MuxRouting'
import {
  TimeRange,
  tempoSearchQuery,
  tempoAgentAttributionQuery,
  tempoSubagentSearchQuery,
  tempoMuxRoutingQuery,
  transformMuxRoutingTraces,
  promLLMCallsRateQuery,
  promCallsByModelQuery,
  promP95LatencyByModelQuery,
  promCallsByAgentQuery,
  buildCostTotal,
  buildCostTimeSeries,
  buildCostByAgent,
  transformTempoTraces,
  transformAgentAttributionTraces,
  transformSubagentTraces,
  extractProjects,
  computeAgentHealthScores,
} from './lib/queries'
import { useTempoSearch, useSessionGraph, TEMPO_SEARCH_LIMIT } from './hooks/useTempo'
import { usePromQueryRange, usePromQueryInstant } from './hooks/usePrometheus'

type ActiveTab = 'overview' | 'sessions' | 'replay' | 'routing'

const REFRESH_INTERVAL_MS = 60_000 // 60s

const TAB_CONFIG: { key: ActiveTab; label: string; icon: React.ElementType }[] = [
  { key: 'overview', label: 'Overview', icon: BarChart2 },
  { key: 'routing', label: 'Routing', icon: Route },
  { key: 'sessions', label: 'Sessions', icon: GitBranch },
  { key: 'replay', label: 'Replay', icon: PlayCircle },
]

export default function App() {
  const [timeRange, setTimeRange] = useState<TimeRange>('6h')
  const [refreshKey, setRefreshKey] = useState(0)
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)
  const rawTab = new URLSearchParams(window.location.search).get('tab')
  const initialTab: ActiveTab = (rawTab === 'sessions' || rawTab === 'replay' || rawTab === 'routing' ? rawTab : 'overview')
  const [activeTab, setActiveTab] = useState<ActiveTab>(initialTab)
  const [selectedAgent, setSelectedAgent] = useState<string | null>(null)
  const [selectedProject, setSelectedProject] = useState<string | null>(null)
  const [selectedPromptVersion, setSelectedPromptVersion] = useState<string | null>(null)

  const handleAgentBarClick = useCallback((agentId: string) => {
    setSelectedAgent((prev) => (prev === agentId ? null : agentId))
  }, [])

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

  useEffect(() => {
    setLastUpdated(new Date())
  }, [])

  // ─── Trace data ──────────────────────────────────────────────────────────
  const { traces: rawTraces, loading: tracesLoading, error: tracesError } =
    useTempoSearch(tempoSearchQuery(selectedProject ?? undefined), timeRange, refreshKey)

  const traceRows = transformTempoTraces(rawTraces)
  const projects = extractProjects(traceRows)
  const filteredTraceRows = selectedAgent
    ? traceRows.filter((t) => t.agentId === selectedAgent)
    : traceRows

  // ─── Stat Card Data ────────────────────────────────────────────────────
  const llmCallCount = tracesLoading ? null : traceRows.length
  const llmCallLoading = tracesLoading
  const llmCallError = tracesError

  const tracesWithCache = traceRows.filter((t) => t.cacheHitRate > 0)
  const cacheValue = tracesLoading
    ? null
    : tracesWithCache.length > 0
      ? tracesWithCache.reduce((s, t) => s + t.cacheHitRate, 0) / tracesWithCache.length
      : null

  const avgLatencyValue = tracesLoading
    ? null
    : traceRows.length > 0
      ? traceRows.reduce((s, t) => s + t.latencyMs, 0) / traceRows.length
      : null

  // ─── Time Series Data ─────────────────────────────────────────────────
  const { series: callsSeries, loading: callsSeriesLoading, error: callsSeriesError } =
    usePromQueryRange(promLLMCallsRateQuery(), timeRange, refreshKey, 'prov_llm_model')

  const costValue = useMemo(() => buildCostTotal(traceRows), [traceRows])
  const costChartSeries = useMemo(() => buildCostTimeSeries(traceRows, timeRange), [traceRows, timeRange])

  // ─── Bar Chart Data ───────────────────────────────────────────────────
  const { bars: callsByModel, loading: callsByModelLoading, error: callsByModelError } =
    usePromQueryInstant(promCallsByModelQuery(timeRange), timeRange, refreshKey, 'prov_llm_model')

  const { bars: latencyByModel, loading: latencyByModelLoading, error: latencyByModelError } = useMemo(() => {
    if (tracesLoading) return { bars: [], loading: true, error: null }
    if (!traceRows.length) return { bars: [], loading: false, error: null }
    const byModel: Record<string, number[]> = {}
    for (const t of traceRows) {
      if (!t.model || t.model === 'unknown') continue
      if (!byModel[t.model]) byModel[t.model] = []
      byModel[t.model].push(t.latencyMs)
    }
    const bars = Object.entries(byModel).map(([model, latencies]) => {
      latencies.sort((a, b) => a - b)
      const p95idx = Math.floor(latencies.length * 0.95)
      return { label: model, value: latencies[Math.min(p95idx, latencies.length - 1)] }
    }).sort((a, b) => b.value - a.value)
    return { bars, loading: false, error: null }
  }, [traceRows, tracesLoading])

  const { bars: callsByAgent, loading: callsByAgentLoading, error: callsByAgentError } =
    usePromQueryInstant(promCallsByAgentQuery(timeRange), timeRange, refreshKey, 'prov_agent_id')

  const costByAgent = useMemo(() => buildCostByAgent(traceRows), [traceRows])

  const agentHealthScores = useMemo(
    () => computeAgentHealthScores(traceRows),
    [traceRows]
  )

  // ─── Agent Attribution Data ──────────────────────────────────────────
  const { traces: rawAttribution, loading: attrLoading, error: attrError } =
    useTempoSearch(tempoAgentAttributionQuery(), timeRange, refreshKey)
  const attributionRows = transformAgentAttributionTraces(rawAttribution)

  const { traces: rawSubagent, loading: subagentLoading, error: subagentError } =
    useTempoSearch(tempoSubagentSearchQuery(), timeRange, refreshKey)
  const subagentRows = transformSubagentTraces(rawSubagent)

  // ─── Mux Routing data ────────────────────────────────────────────────
  const { traces: rawRoutingTraces, loading: routingLoading, error: routingError } =
    useTempoSearch(tempoMuxRoutingQuery(), timeRange, refreshKey)
  const routingRows = useMemo(() => transformMuxRoutingTraces(rawRoutingTraces), [rawRoutingTraces])

  // ─── Session graph data ──────────────────────────────────────────────
  const { nodes: sessionNodes, edges: sessionEdges, rawTraces: sessionRawTraces, loading: sessionLoading, error: sessionError } =
    useSessionGraph(timeRange, refreshKey)

  const tempoError = !!(llmCallError || tracesError)

  return (
    <div className="min-h-screen bg-void bg-dot-grid overflow-x-hidden">
      <Header
        timeRange={timeRange}
        onTimeRangeChange={handleTimeRangeChange}
        onRefresh={handleRefresh}
        lastUpdated={lastUpdated}
        tempoError={tempoError}
        projects={projects}
        selectedProject={selectedProject}
        onProjectChange={setSelectedProject}
      />

      {/* Tab navigation */}
      <div className="border-b border-edge bg-void/80 backdrop-blur-sm">
        <div className="max-w-screen-2xl mx-auto px-4 sm:px-6 flex gap-0.5">
          {TAB_CONFIG.map(({ key, label, icon: TabIcon }) => (
            <button
              key={key}
              onClick={() => setActiveTab(key)}
              className={`flex items-center gap-2 px-4 py-3 text-xs font-medium border-b-2 transition-all duration-200 ${
                activeTab === key
                  ? 'border-accent text-accent'
                  : 'border-transparent text-ink-faint hover:text-ink-muted'
              }`}
            >
              <TabIcon className="w-3.5 h-3.5" strokeWidth={1.5} />
              {label}
              {key === 'sessions' && sessionNodes.length > 0 && (
                <span className="ml-1 text-[10px] bg-accent/10 text-accent rounded-full px-1.5 py-0.5 mono">
                  {sessionNodes.length}
                </span>
              )}
            </button>
          ))}
        </div>
      </div>

      <main className="max-w-screen-2xl mx-auto px-4 sm:px-6 py-6 space-y-5">
        {/* Mux Routing tab */}
        {activeTab === 'routing' && (
          <MuxRouting
            rows={routingRows}
            loading={routingLoading}
            error={routingError}
          />
        )}

        {/* Session Explorer tab */}
        {activeTab === 'sessions' && (
          <div className="space-y-4 animate-fade-in">
            <PromptVersionFilter
              traces={traceRows}
              selectedPromptVersion={selectedPromptVersion}
              onSelectPromptVersion={setSelectedPromptVersion}
            />
            <SessionExplorer
              nodes={sessionNodes}
              edges={sessionEdges}
              rawTraces={filterTempoSpansByPromptVersion(sessionRawTraces, selectedPromptVersion)}
              loading={sessionLoading}
              error={sessionError}
            />
          </div>
        )}

        {/* Session Replay tab */}
        {activeTab === 'replay' && (
          <div className="animate-fade-in">
            <SessionReplay
              timeRange={timeRange}
              refreshKey={refreshKey}
            />
          </div>
        )}

        {/* Overview tab */}
        {activeTab === 'overview' && (<>
        {/* Row 1: Stat Cards */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 stagger-children">
          <StatCard
            icon={Activity}
            iconColor="text-accent"
            iconBg="bg-accent/8"
            label="LLM Calls"
            value={llmCallCount !== null ? llmCallCount.toLocaleString() : null}
            loading={llmCallLoading}
            error={llmCallError}
          />
          <StatCard
            icon={DollarSign}
            iconColor="text-signal-lime"
            iconBg="bg-signal-lime/8"
            label="Total Cost"
            value={!tracesLoading ? `$${costValue.toFixed(4)}` : null}
            loading={tracesLoading}
            error={tracesError}
            caption={
              !tracesLoading && traceRows.length >= TEMPO_SEARCH_LIMIT
                ? `sampled — showing ${traceRows.length.toLocaleString()} most-recent traces`
                : null
            }
          />
          <StatCard
            icon={Zap}
            iconColor="text-signal-amber"
            iconBg="bg-signal-amber/8"
            label="Cache Hit Rate"
            value={cacheValue !== null ? `${(cacheValue * 100).toFixed(1)}%` : null}
            loading={tracesLoading}
            error={tracesError}
          />
          <StatCard
            icon={RefreshCw}
            iconColor="text-signal-sky"
            iconBg="bg-signal-sky/8"
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
            title="Cost over Time"
            subtitle="Cost per time bucket"
            series={costChartSeries}
            loading={tracesLoading}
            error={tracesError}
            type="area"
            valueFormatter={(v) => `$${v.toFixed(4)}`}
          />
        </div>

        {/* Row 3: Model breakdown */}
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
            subtitle="95th percentile latency (ms)"
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
            subtitle="Click a bar to filter traces"
            data={callsByAgent}
            loading={callsByAgentLoading}
            error={callsByAgentError}
            valueFormatter={(v) => v.toFixed(0)}
            onBarClick={handleAgentBarClick}
            selectedLabel={selectedAgent}
          />
          <BarChartPanel
            title="Cost by Agent"
            subtitle="Estimated total cost per agent"
            data={costByAgent}
            loading={tracesLoading}
            error={tracesError}
            valueFormatter={(v) => `$${v.toFixed(4)}`}
          />
        </div>

        {/* Cost Sparklines + Budget */}
        <CostSparklines
          traces={traceRows}
          timeRange={timeRange}
          loading={tracesLoading}
        />

        {/* Agent Health */}
        <AgentHealthBadges
          scores={agentHealthScores}
          loading={tracesLoading}
          error={tracesError}
        />

        {/* Agent Attribution */}
        <AgentAttribution
          attributionRows={attributionRows}
          attributionLoading={attrLoading}
          attributionError={attrError}
          subagentRows={subagentRows}
          subagentLoading={subagentLoading}
          subagentError={subagentError}
        />

        {/* Trace filter badge */}
        {selectedAgent && (
          <div className="flex items-center gap-2">
            <span className="text-xs text-ink-faint">Filtered:</span>
            <span className="flex items-center gap-1.5 text-xs font-medium text-accent bg-accent/8 px-2.5 py-1 rounded-full border border-accent/20">
              {selectedAgent}
              <button
                onClick={() => setSelectedAgent(null)}
                className="ml-0.5 text-accent/60 hover:text-accent transition-colors"
                aria-label="Clear agent filter"
              >
                <X className="w-3 h-3" />
              </button>
            </span>
            <span className="text-xs text-ink-faint mono">({filteredTraceRows.length})</span>
          </div>
        )}

        {/* Trace Table */}
        <TraceTable
          traces={filteredTraceRows}
          loading={tracesLoading}
          error={tracesError}
        />
        </>)}
      </main>
    </div>
  )
}
