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
  green: 'bg-signal-lime/15 text-signal-lime border border-signal-lime/30',
  yellow: 'bg-signal-amber/15 text-signal-amber border border-signal-amber/30',
  red: 'bg-signal-coral/15 text-signal-coral border border-signal-coral/30',
}

const BADGE_DOT: Record<AgentHealthScore['badge'], string> = {
  green: 'bg-signal-lime',
  yellow: 'bg-signal-amber',
  red: 'bg-signal-coral',
}

const BADGE_LABEL: Record<AgentHealthScore['badge'], string> = {
  green: 'Healthy',
  yellow: 'Degraded',
  red: 'Critical',
}

function ScoreBar({ value, color }: { value: number; color: string }) {
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1.5 bg-surface-overlay rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full ${color} transition-all`}
          style={{ width: `${Math.min(100, Math.max(0, value))}%` }}
        />
      </div>
      <span className="text-xs text-ink-faint mono w-8 text-right">{value}</span>
    </div>
  )
}

function AgentCard({ score }: { score: AgentHealthScore }) {
  const [expanded, setExpanded] = useState(false)

  const barColor = score.badge === 'green'
    ? 'bg-signal-lime'
    : score.badge === 'yellow'
    ? 'bg-signal-amber'
    : 'bg-signal-coral'

  return (
    <div className="card glow-hover overflow-hidden">
      {/* Header row */}
      <div
        className="flex items-center gap-3 px-4 py-3 cursor-pointer"
        onClick={() => setExpanded((v) => !v)}
      >
        {/* Badge dot + agent id */}
        <span className={`inline-block w-2 h-2 rounded-full flex-shrink-0 ${BADGE_DOT[score.badge]}`} />
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-ink truncate">{score.agentId}</p>
          <p className="text-xs text-ink-faint">{score.spanCount} spans</p>
        </div>

        {/* Score + badge */}
        <div className="flex items-center gap-2">
          <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${BADGE_STYLES[score.badge]}`}>
            {BADGE_LABEL[score.badge]}
          </span>
          <span className="text-lg font-bold text-ink mono w-12 text-right">
            {score.score}
          </span>
        </div>

        {/* Expand toggle */}
        {expanded ? (
          <ChevronUp className="w-4 h-4 text-ink-faint flex-shrink-0" />
        ) : (
          <ChevronDown className="w-4 h-4 text-ink-faint flex-shrink-0" />
        )}
      </div>

      {/* Expanded detail */}
      {expanded && (
        <div className="border-t border-edge px-4 py-3 space-y-2">
          <div className="grid grid-cols-2 gap-x-8 gap-y-1 text-xs text-ink-muted mb-3">
            <div>Error rate: <span className="text-ink mono">{(score.errorRate * 100).toFixed(1)}%</span></div>
            <div>P95 latency: <span className="text-ink mono">{score.p95LatencyMs >= 1000 ? `${(score.p95LatencyMs / 1000).toFixed(1)}s` : `${score.p95LatencyMs}ms`}</span></div>
            <div>Avg cost/session: <span className="text-ink mono">${score.avgCostPerSession.toFixed(4)}</span></div>
            <div>Tool retry rate: <span className="text-ink mono">{(score.toolRetryRate * 100).toFixed(1)}%</span></div>
          </div>

          <p className="text-xs uppercase tracking-wider text-ink-faint font-medium mb-1">Score components</p>
          <div className="space-y-1.5">
            <div>
              <span className="text-xs text-ink-muted block mb-0.5">Error rate (30%)</span>
              <ScoreBar value={score.components.error_rate} color={barColor} />
            </div>
            <div>
              <span className="text-xs text-ink-muted block mb-0.5">P95 latency (30%)</span>
              <ScoreBar value={score.components.latency} color={barColor} />
            </div>
            <div>
              <span className="text-xs text-ink-muted block mb-0.5">Cost efficiency (20%)</span>
              <ScoreBar value={score.components.cost} color={barColor} />
            </div>
            <div>
              <span className="text-xs text-ink-muted block mb-0.5">Tool retry (20%)</span>
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
    <div className="card px-4 py-3 flex items-center gap-3">
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
    <div className="bg-surface border border-edge rounded-xl overflow-hidden">
      {/* Panel header */}
      <div className="px-5 py-4 border-b border-edge flex items-center gap-3">
        <div className="p-2 rounded-lg bg-signal-coral/10">
          <Heart className="w-4 h-4 text-signal-coral" />
        </div>
        <div className="flex-1">
          <h3 className="text-ink font-semibold text-sm">Agent Health</h3>
          <p className="text-ink-faint text-xs mt-0.5">
            Reliability scores based on error rate, P95 latency, cost, and tool retries
          </p>
        </div>
        {!loading && !error && scores.length > 0 && (
          <div className="flex items-center gap-2 text-xs">
            {greenCount > 0 && (
              <span className="flex items-center gap-1 text-signal-lime">
                <span className="w-2 h-2 rounded-full bg-signal-lime inline-block" />
                {greenCount}
              </span>
            )}
            {yellowCount > 0 && (
              <span className="flex items-center gap-1 text-signal-amber">
                <span className="w-2 h-2 rounded-full bg-signal-amber inline-block" />
                {yellowCount}
              </span>
            )}
            {redCount > 0 && (
              <span className="flex items-center gap-1 text-signal-coral">
                <span className="w-2 h-2 rounded-full bg-signal-coral inline-block" />
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
          <p className="text-center text-ink-faint text-sm py-6">
            Unable to compute health scores — {error}
          </p>
        ) : scores.length === 0 ? (
          <div className="flex flex-col items-center gap-2 py-8">
            <Heart className="w-6 h-6 text-ink-faint" />
            <p className="text-ink-muted text-sm">No agent spans in the current time window.</p>
            <p className="text-ink-faint text-xs">Health scores appear once spans are collected.</p>
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
