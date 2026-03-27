/**
 * CostSparklines — per-agent cost over time sparklines with optional budget progress bars.
 *
 * Reads trace data that is already fetched by the parent (App.tsx) and builds
 * per-agent time series client-side so we don't need an extra Tempo query.
 *
 * Budget limits are read from the AGENTWEAVE_BUDGET_* env vars that the proxy
 * exposes via the /budget/status endpoint (if available).  When the endpoint is
 * absent, the component shows sparklines without progress bars.
 */

import React, { useEffect, useState, useMemo } from 'react'
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
} from 'recharts'
import { AlertTriangle, DollarSign } from 'lucide-react'
import { TraceRow, TimeRange, getStepForRange } from '../lib/queries'

const COLORS = [
  '#6366f1', '#8b5cf6', '#06b6d4', '#10b981', '#f59e0b', '#ef4444',
  '#ec4899', '#84cc16', '#f97316', '#3b82f6',
]

interface BudgetStatus {
  agents: Record<string, { spent: number; limit: number | null }>
  global: { spent: number; limit: number | null }
}

interface SparklineSeries {
  agentId: string
  points: Array<{ time: number; value: number }>
  totalSpend: number
  color: string
}

interface CostSparklinesProps {
  traces: TraceRow[]
  timeRange: TimeRange
  loading: boolean
}

/** Bucket traces by agent and time step, returning per-agent series. */
function buildPerAgentSeries(
  traces: TraceRow[],
  timeRange: TimeRange,
): SparklineSeries[] {
  const stepMs = getStepForRange(timeRange) * 1000

  // Collect per-agent, per-bucket cost
  const agentBuckets = new Map<string, Map<number, number>>()

  for (const t of traces) {
    if (t.costUsd <= 0) continue
    const agentId = t.agentId || 'unknown'
    const bucket = Math.floor(t.time / stepMs) * stepMs

    if (!agentBuckets.has(agentId)) agentBuckets.set(agentId, new Map())
    const bmap = agentBuckets.get(agentId)!
    bmap.set(bucket, (bmap.get(bucket) ?? 0) + t.costUsd)
  }

  if (!agentBuckets.size) return []

  // Sort agents by total spend descending
  const agents = Array.from(agentBuckets.entries())
    .map(([agentId, bmap]) => ({
      agentId,
      totalSpend: Array.from(bmap.values()).reduce((a, b) => a + b, 0),
      points: Array.from(bmap.entries())
        .sort((a, b) => a[0] - b[0])
        .map(([time, value]) => ({ time, value })),
    }))
    .sort((a, b) => b.totalSpend - a.totalSpend)

  return agents.map((a, i) => ({
    ...a,
    color: COLORS[i % COLORS.length],
  }))
}

function useSparklines(traces: TraceRow[], timeRange: TimeRange) {
  return useMemo(() => buildPerAgentSeries(traces, timeRange), [traces, timeRange])
}

function useBudgetStatus() {
  const [status, setStatus] = useState<BudgetStatus | null>(null)

  useEffect(() => {
    fetch('/proxy/budget/status')
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (data) setStatus(data as BudgetStatus)
      })
      .catch(() => {
        // Budget endpoint not available — progress bars will be hidden
      })
  }, [])

  return status
}

interface MiniSparklineProps {
  series: SparklineSeries
  budgetLimit: number | null
  budgetSpent: number
}

function MiniSparkline({ series, budgetLimit, budgetSpent }: MiniSparklineProps) {
  const pct = budgetLimit != null && budgetLimit > 0
    ? Math.min((budgetSpent / budgetLimit) * 100, 100)
    : null

  const isNearLimit = pct !== null && pct >= 80
  const isExceeded = pct !== null && pct >= 100

  const barColor = isExceeded
    ? 'bg-red-500'
    : isNearLimit
      ? 'bg-amber-500'
      : 'bg-indigo-500'

  return (
    <div className="bg-slate-800/50 rounded-lg p-3 border border-slate-700/50">
      {/* Header */}
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <span
            className="inline-block w-2 h-2 rounded-full"
            style={{ backgroundColor: series.color }}
          />
          <span className="text-xs font-medium text-gray-300 truncate max-w-[120px]" title={series.agentId}>
            {series.agentId}
          </span>
          {isExceeded && (
            <span title="Budget exceeded"><AlertTriangle className="w-3 h-3 text-red-400 shrink-0" /></span>
          )}
          {isNearLimit && !isExceeded && (
            <span title="Near budget limit"><AlertTriangle className="w-3 h-3 text-amber-400 shrink-0" /></span>
          )}
        </div>
        <span className="text-xs font-mono text-gray-400">
          ${series.totalSpend.toFixed(4)}
        </span>
      </div>

      {/* Sparkline */}
      <div className="h-12">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={series.points} margin={{ top: 2, right: 0, bottom: 0, left: 0 }}>
            <defs>
              <linearGradient id={`grad-${series.agentId}`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor={series.color} stopOpacity={0.4} />
                <stop offset="95%" stopColor={series.color} stopOpacity={0.0} />
              </linearGradient>
            </defs>
            <XAxis dataKey="time" hide />
            <YAxis hide />
            <Tooltip
              contentStyle={{
                background: '#1e293b',
                border: '1px solid #334155',
                borderRadius: '6px',
                fontSize: '11px',
                color: '#e2e8f0',
              }}
              formatter={(v: number) => [`$${v.toFixed(5)}`, 'Cost']}
              labelFormatter={() => ''}
            />
            <Area
              type="monotone"
              dataKey="value"
              stroke={series.color}
              strokeWidth={1.5}
              fill={`url(#grad-${series.agentId})`}
              dot={false}
              isAnimationActive={false}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>

      {/* Budget progress bar (only shown when a limit is configured) */}
      {pct !== null && budgetLimit != null && (
        <div className="mt-2">
          <div className="flex justify-between text-[10px] text-gray-500 mb-1">
            <span>Daily budget</span>
            <span>
              ${budgetSpent.toFixed(4)} / ${budgetLimit.toFixed(2)}
              {isExceeded && (
                <span className="ml-1 text-red-400 font-semibold">EXCEEDED</span>
              )}
            </span>
          </div>
          <div className="w-full bg-slate-700 rounded-full h-1.5">
            <div
              className={`h-1.5 rounded-full transition-all duration-300 ${barColor}`}
              style={{ width: `${pct}%` }}
            />
          </div>
        </div>
      )}
    </div>
  )
}

export function CostSparklines({ traces, timeRange, loading }: CostSparklinesProps) {
  const series = useSparklines(traces, timeRange)
  const budgetStatus = useBudgetStatus()

  if (loading) {
    return (
      <div className="bg-slate-900/50 rounded-xl p-4 border border-slate-700/50">
        <div className="flex items-center gap-2 mb-4">
          <DollarSign className="w-4 h-4 text-emerald-400" />
          <h3 className="text-sm font-semibold text-gray-200">Cost per Agent</h3>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className="bg-slate-800/50 rounded-lg p-3 h-28 animate-pulse" />
          ))}
        </div>
      </div>
    )
  }

  if (!series.length) {
    return (
      <div className="bg-slate-900/50 rounded-xl p-4 border border-slate-700/50">
        <div className="flex items-center gap-2 mb-2">
          <DollarSign className="w-4 h-4 text-emerald-400" />
          <h3 className="text-sm font-semibold text-gray-200">Cost per Agent</h3>
        </div>
        <p className="text-xs text-gray-500">No cost data in selected time range.</p>
      </div>
    )
  }

  return (
    <div className="bg-slate-900/50 rounded-xl p-4 border border-slate-700/50">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <DollarSign className="w-4 h-4 text-emerald-400" />
          <h3 className="text-sm font-semibold text-gray-200">Cost per Agent</h3>
          <span className="text-xs text-gray-500">(sparklines + daily budget progress)</span>
        </div>
        {budgetStatus == null && (
          <span className="text-[10px] text-gray-600 italic">Budget limits: not configured</span>
        )}
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 xl:grid-cols-4 gap-3">
        {series.map((s) => {
          const agentBudget = budgetStatus?.agents?.[s.agentId]
          return (
            <MiniSparkline
              key={s.agentId}
              series={s}
              budgetLimit={agentBudget?.limit ?? budgetStatus?.global?.limit ?? null}
              budgetSpent={agentBudget?.spent ?? 0}
            />
          )
        })}
      </div>
    </div>
  )
}
