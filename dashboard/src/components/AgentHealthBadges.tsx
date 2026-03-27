/**
 * AgentHealthBadges — issue #116
 *
 * Renders a health badge per agent on the overview tab.
 * Badge colours: green (≥80), yellow (≥60), red (<60).
 *
 * Scores are computed client-side from the trace data already loaded by App.tsx,
 * so no extra API call is needed.
 */

import React, { useState } from 'react'
import { Heart, ChevronDown, ChevronUp } from 'lucide-react'
import { AgentHealthScore } from '../lib/queries'

// ─── Badge pill ──────────────────────────────────────────────────────────────

const BADGE_STYLES: Record<AgentHealthScore['badge'], string> = {
  green: 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/30',
  yellow: 'bg-amber-500/15 text-amber-400 border border-amber-500/30',
  red: 'bg-red-500/15 text-red-400 border border-red-500/30',
}

const BADGE_DOT: Record<AgentHealthScore['badge'], string> = {
  green: 'bg-emerald-400',
  yellow: 'bg-amber-400',
  red: 'bg-red-400',
}

const BADGE_LABEL: Record<AgentHealthScore['badge'], string> = {
  green: 'Healthy',
  yellow: 'Degraded',
  red: 'Critical',
}

function ScoreBar({ value, color }: { value: number; color: string }) {
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1.5 bg-slate-700 rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full ${color} transition-all`}
          style={{ width: `${Math.min(100, Math.max(0, value))}%` }}
        />
      </div>
      <span className="text-xs text-slate-500 w-8 text-right">{value}</span>
    </div>
  )
}

function AgentCard({ score }: { score: AgentHealthScore }) {
  const [expanded, setExpanded] = useState(false)

  const barColor = score.badge === 'green'
    ? 'bg-emerald-500'
    : score.badge === 'yellow'
    ? 'bg-amber-500'
    : 'bg-red-500'

  return (
    <div className="bg-[#111118] border border-[#1e1e2e] rounded-xl overflow-hidden hover:border-slate-600 transition-colors">
      {/* Header row */}
      <div
        className="flex items-center gap-3 px-4 py-3 cursor-pointer"
        onClick={() => setExpanded((v) => !v)}
      >
        {/* Badge dot + agent id */}
        <span className={`inline-block w-2 h-2 rounded-full flex-shrink-0 ${BADGE_DOT[score.badge]}`} />
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-white truncate">{score.agentId}</p>
          <p className="text-xs text-slate-500">{score.spanCount} spans</p>
        </div>

        {/* Score + badge */}
        <div className="flex items-center gap-2">
          <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${BADGE_STYLES[score.badge]}`}>
            {BADGE_LABEL[score.badge]}
          </span>
          <span className="text-lg font-bold text-white tabular-nums w-12 text-right">
            {score.score}
          </span>
        </div>

        {/* Expand toggle */}
        {expanded ? (
          <ChevronUp className="w-4 h-4 text-slate-500 flex-shrink-0" />
        ) : (
          <ChevronDown className="w-4 h-4 text-slate-500 flex-shrink-0" />
        )}
      </div>

      {/* Expanded detail */}
      {expanded && (
        <div className="border-t border-[#1e1e2e] px-4 py-3 space-y-2">
          <div className="grid grid-cols-2 gap-x-8 gap-y-1 text-xs text-slate-400 mb-3">
            <div>Error rate: <span className="text-white">{(score.errorRate * 100).toFixed(1)}%</span></div>
            <div>P95 latency: <span className="text-white">{score.p95LatencyMs >= 1000 ? `${(score.p95LatencyMs / 1000).toFixed(1)}s` : `${score.p95LatencyMs}ms`}</span></div>
            <div>Avg cost/session: <span className="text-white">${score.avgCostPerSession.toFixed(4)}</span></div>
            <div>Tool retry rate: <span className="text-white">{(score.toolRetryRate * 100).toFixed(1)}%</span></div>
          </div>

          <p className="text-xs text-slate-500 uppercase tracking-wider mb-1">Score components</p>
          <div className="space-y-1.5">
            <div>
              <span className="text-xs text-slate-400 block mb-0.5">Error rate (30%)</span>
              <ScoreBar value={score.components.error_rate} color={barColor} />
            </div>
            <div>
              <span className="text-xs text-slate-400 block mb-0.5">P95 latency (30%)</span>
              <ScoreBar value={score.components.latency} color={barColor} />
            </div>
            <div>
              <span className="text-xs text-slate-400 block mb-0.5">Cost efficiency (20%)</span>
              <ScoreBar value={score.components.cost} color={barColor} />
            </div>
            <div>
              <span className="text-xs text-slate-400 block mb-0.5">Tool retry (20%)</span>
              <ScoreBar value={score.components.tool_retry} color={barColor} />
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// ─── Main panel ──────────────────────────────────────────────────────────────

interface AgentHealthBadgesProps {
  scores: AgentHealthScore[]
  loading: boolean
  error: string | null
}

function SkeletonCard() {
  return (
    <div className="bg-[#111118] border border-[#1e1e2e] rounded-xl px-4 py-3 flex items-center gap-3">
      <div className="skeleton w-2 h-2 rounded-full" />
      <div className="flex-1 space-y-1">
        <div className="skeleton h-4 rounded w-24" />
        <div className="skeleton h-3 rounded w-16" />
      </div>
      <div className="skeleton h-5 rounded-full w-16" />
      <div className="skeleton h-6 rounded w-10" />
    </div>
  )
}

export function AgentHealthBadges({ scores, loading, error }: AgentHealthBadgesProps) {
  const greenCount = scores.filter((s) => s.badge === 'green').length
  const yellowCount = scores.filter((s) => s.badge === 'yellow').length
  const redCount = scores.filter((s) => s.badge === 'red').length

  return (
    <div className="bg-[#0d0d14] border border-[#1e1e2e] rounded-xl overflow-hidden">
      {/* Panel header */}
      <div className="px-5 py-4 border-b border-[#1e1e2e] flex items-center gap-3">
        <div className="p-2 rounded-lg bg-rose-500/10">
          <Heart className="w-4 h-4 text-rose-400" />
        </div>
        <div className="flex-1">
          <h3 className="text-white font-semibold text-sm">Agent Health</h3>
          <p className="text-slate-500 text-xs mt-0.5">
            Reliability scores based on error rate, P95 latency, cost, and tool retries
          </p>
        </div>
        {!loading && !error && scores.length > 0 && (
          <div className="flex items-center gap-2 text-xs">
            {greenCount > 0 && (
              <span className="flex items-center gap-1 text-emerald-400">
                <span className="w-2 h-2 rounded-full bg-emerald-400 inline-block" />
                {greenCount}
              </span>
            )}
            {yellowCount > 0 && (
              <span className="flex items-center gap-1 text-amber-400">
                <span className="w-2 h-2 rounded-full bg-amber-400 inline-block" />
                {yellowCount}
              </span>
            )}
            {redCount > 0 && (
              <span className="flex items-center gap-1 text-red-400">
                <span className="w-2 h-2 rounded-full bg-red-400 inline-block" />
                {redCount}
              </span>
            )}
          </div>
        )}
      </div>

      {/* Content */}
      <div className="p-4">
        {loading ? (
          <div className="space-y-2">
            {[0, 1, 2].map((i) => <SkeletonCard key={i} />)}
          </div>
        ) : error ? (
          <p className="text-center text-slate-600 text-sm py-6">
            Unable to compute health scores — {error}
          </p>
        ) : scores.length === 0 ? (
          <div className="flex flex-col items-center gap-2 py-8">
            <Heart className="w-6 h-6 text-slate-600" />
            <p className="text-slate-500 text-sm">No agent spans in the current time window.</p>
            <p className="text-slate-600 text-xs">Health scores appear once spans are collected.</p>
          </div>
        ) : (
          <div className="space-y-2">
            {scores.map((s) => (
              <AgentCard key={s.agentId} score={s} />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
