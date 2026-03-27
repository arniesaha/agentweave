import React, { useState, useCallback } from 'react'
import { ChevronDown, ChevronRight, Download, Play, Search, RefreshCw } from 'lucide-react'
import { ReplayTurn } from '../lib/queries'
import { useSessionReplay } from '../hooks/useTempo'
import { TimeRange } from '../lib/queries'

// ─── Helpers ─────────────────────────────────────────────────────────────────

function formatTime(ms: number): string {
  return new Date(ms).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

function formatDuration(ms: number): string {
  if (!ms || ms <= 0) return '—'
  if (ms < 1000) return `${ms.toFixed(0)}ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
  return `${(ms / 60000).toFixed(1)}m`
}

function formatCost(cost: number): string {
  if (!cost || cost <= 0) return '—'
  return `$${cost < 0.01 ? cost.toFixed(6) : cost.toFixed(4)}`
}

function truncate(text: string, maxLen = 120): string {
  if (!text) return ''
  return text.length > maxLen ? text.slice(0, maxLen) + '…' : text
}

function activityColor(type: string): string {
  switch (type) {
    case 'llm_call': return 'bg-accent/20 text-accent border-accent/30'
    case 'tool_call': return 'bg-signal-amber/20 text-signal-amber border-signal-amber/30'
    case 'agent_turn': return 'bg-signal-sky/20 text-signal-sky border-signal-sky/30'
    default: return 'bg-surface-overlay text-ink-muted border-edge'
  }
}

function activityDot(type: string): string {
  switch (type) {
    case 'llm_call': return 'bg-accent'
    case 'tool_call': return 'bg-signal-amber'
    case 'agent_turn': return 'bg-signal-sky'
    default: return 'bg-ink-muted'
  }
}

function activityLabel(type: string, toolName: string): string {
  if (type === 'tool_call' && toolName) return `tool: ${toolName}`
  return type.replace('_', ' ')
}

// ─── Turn Row ─────────────────────────────────────────────────────────────────

interface TurnRowProps {
  turn: ReplayTurn
  index: number
  isExpanded: boolean
  onToggle: () => void
}

function TurnRow({ turn, index, isExpanded, onToggle }: TurnRowProps) {
  const hasContent = turn.promptPreview || turn.responsePreview

  return (
    <div className="border border-edge rounded-lg overflow-hidden">
      {/* Row header — always visible */}
      <button
        onClick={onToggle}
        disabled={!hasContent}
        className={`w-full flex items-start gap-3 px-4 py-3 text-left transition-colors ${
          hasContent ? 'hover:bg-surface-overlay cursor-pointer' : 'cursor-default'
        } ${isExpanded ? 'bg-surface-overlay' : ''}`}
      >
        {/* Turn number + timeline dot */}
        <div className="flex flex-col items-center gap-1 flex-shrink-0 mt-0.5">
          <span className="text-[10px] text-ink-faint mono w-6 text-center">
            {String(index + 1).padStart(2, '0')}
          </span>
          <div className={`w-2 h-2 rounded-full ${activityDot(turn.activityType)}`} />
        </div>

        {/* Main content */}
        <div className="flex-1 min-w-0">
          <div className="flex flex-wrap items-center gap-2 mb-1">
            {/* Activity badge */}
            <span className={`inline-flex items-center text-[10px] font-medium px-1.5 py-0.5 rounded border ${activityColor(turn.activityType)}`}>
              {activityLabel(turn.activityType, turn.toolName)}
            </span>
            {/* Agent */}
            <span className="text-xs text-ink-muted mono truncate max-w-[160px]">
              {turn.agentId}
            </span>
            {/* Model (only for LLM calls) */}
            {turn.model && turn.model !== 'unknown' && turn.activityType === 'llm_call' && (
              <span className="text-[10px] text-ink-faint mono">{turn.model}</span>
            )}
            {/* Task label */}
            {turn.taskLabel && (
              <span className="text-[10px] text-accent truncate max-w-[180px]">{turn.taskLabel}</span>
            )}
          </div>

          {/* Preview text */}
          {turn.promptPreview && !isExpanded && (
            <p className="text-xs text-ink-muted truncate mt-0.5">
              {truncate(turn.promptPreview)}
            </p>
          )}
        </div>

        {/* Right-side stats */}
        <div className="flex items-center gap-4 flex-shrink-0 text-xs mono">
          <span className="text-ink-muted">{formatTime(turn.time)}</span>
          <span className="text-ink-muted">{formatDuration(turn.latencyMs)}</span>
          {turn.costUsd > 0 ? (
            <span className="text-signal-lime">{formatCost(turn.costUsd)}</span>
          ) : (
            <span className="text-ink-faint">—</span>
          )}
          {hasContent && (
            isExpanded
              ? <ChevronDown size={14} className="text-ink-muted" />
              : <ChevronRight size={14} className="text-ink-muted" />
          )}
        </div>
      </button>

      {/* Expanded content */}
      {isExpanded && (
        <div className="border-t border-edge bg-surface-raised px-4 py-3 space-y-3">
          {/* Token counts */}
          {(turn.tokensIn > 0 || turn.tokensOut > 0) && (
            <div className="flex items-center gap-4 text-xs text-ink-muted">
              <span>In: <span className="text-ink">{turn.tokensIn.toLocaleString()}</span></span>
              <span>Out: <span className="text-ink">{turn.tokensOut.toLocaleString()}</span></span>
              <span>Total: <span className="text-ink">{(turn.tokensIn + turn.tokensOut).toLocaleString()}</span></span>
            </div>
          )}

          {/* Prompt */}
          {turn.promptPreview && (
            <div>
              <div className="text-[10px] text-ink-muted uppercase tracking-wide mb-1.5">Prompt</div>
              <pre className="text-xs text-ink mono whitespace-pre-wrap break-all bg-surface-raised border border-edge rounded-lg p-3 max-h-60 overflow-y-auto">
                {turn.promptPreview}
              </pre>
            </div>
          )}

          {/* Response */}
          {turn.responsePreview && (
            <div>
              <div className="text-[10px] text-ink-muted uppercase tracking-wide mb-1.5">Response</div>
              <pre className="text-xs text-ink mono whitespace-pre-wrap break-all bg-surface-raised border border-edge rounded-lg p-3 max-h-60 overflow-y-auto">
                {turn.responsePreview}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ─── Session Replay Summary ──────────────────────────────────────────────────

interface SummaryProps {
  turns: ReplayTurn[]
}

function ReplaySummary({ turns }: SummaryProps) {
  const totalCost = turns.reduce((s, t) => s + t.costUsd, 0)
  const totalLatencyMs = turns.reduce((s, t) => s + t.latencyMs, 0)
  const llmCalls = turns.filter((t) => t.activityType === 'llm_call').length
  const toolCalls = turns.filter((t) => t.activityType === 'tool_call').length

  return (
    <div className="grid grid-cols-2 sm:grid-cols-4 gap-px bg-edge rounded-xl overflow-hidden">
      {[
        { label: 'Total Turns', value: String(turns.length) },
        { label: 'LLM Calls', value: String(llmCalls) },
        { label: 'Tool Calls', value: String(toolCalls) },
        { label: 'Total Latency', value: formatDuration(totalLatencyMs) },
      ].map(({ label, value }) => (
        <div key={label} className="bg-surface px-4 py-3">
          <div className="text-[10px] text-ink-muted uppercase tracking-wide">{label}</div>
          <div className="text-sm font-semibold text-ink mt-0.5">{value}</div>
        </div>
      ))}
    </div>
  )
}

// ─── Main SessionReplay component ────────────────────────────────────────────

interface SessionReplayProps {
  timeRange: TimeRange
  refreshKey: number
  initialSessionId?: string
}

export function SessionReplay({ timeRange, refreshKey, initialSessionId }: SessionReplayProps) {
  const [inputId, setInputId] = useState(initialSessionId ?? '')
  const [sessionId, setSessionId] = useState(initialSessionId ?? '')
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set())

  const { turns, rawTraces, loading, error } = useSessionReplay(sessionId, timeRange, refreshKey)

  const handleSearch = useCallback(() => {
    setSessionId(inputId.trim())
    setExpandedIds(new Set())
  }, [inputId])

  const toggleExpand = useCallback((traceId: string) => {
    setExpandedIds((prev) => {
      const next = new Set(prev)
      if (next.has(traceId)) next.delete(traceId)
      else next.add(traceId)
      return next
    })
  }, [])

  const expandAll = useCallback(() => {
    setExpandedIds(new Set(turns.map((t) => t.traceId)))
  }, [turns])

  const collapseAll = useCallback(() => {
    setExpandedIds(new Set())
  }, [])

  const exportJson = useCallback(() => {
    const data = {
      sessionId,
      exportedAt: new Date().toISOString(),
      turnCount: turns.length,
      turns: turns.map((t) => ({
        turnNumber: t.turnNumber,
        traceId: t.traceId,
        time: new Date(t.time).toISOString(),
        agentId: t.agentId,
        activityType: t.activityType,
        toolName: t.toolName,
        model: t.model,
        tokensIn: t.tokensIn,
        tokensOut: t.tokensOut,
        costUsd: t.costUsd,
        latencyMs: t.latencyMs,
        promptPreview: t.promptPreview,
        responsePreview: t.responsePreview,
        taskLabel: t.taskLabel,
        project: t.project,
      })),
    }
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `session-replay-${sessionId.slice(0, 16)}-${Date.now()}.json`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  }, [sessionId, turns])

  return (
    <div className="space-y-4">
      {/* Section header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-sm font-semibold text-ink">Session Replay</h2>
          <p className="text-xs text-ink-muted mt-0.5">
            Step through LLM turns and tool calls for any session — the DVR for agents
          </p>
        </div>
      </div>

      {/* Session ID input */}
      <div className="flex items-center gap-2">
        <input
          type="text"
          value={inputId}
          onChange={(e) => setInputId(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') handleSearch() }}
          placeholder="Paste a session ID to replay…"
          className="flex-1 bg-surface border border-edge rounded-lg px-3 py-2 text-sm text-ink placeholder:text-ink-faint focus:outline-none focus:border-accent/60"
        />
        <button
          onClick={handleSearch}
          disabled={!inputId.trim() || loading}
          className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium bg-accent/12 text-accent border border-accent/25 hover:bg-accent/20 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          {loading ? <RefreshCw size={14} className="animate-spin" /> : <Play size={14} />}
          Replay
        </button>
      </div>

      {/* Error state */}
      {error && (
        <div className="bg-signal-coral/10 border border-signal-coral/20 rounded-lg px-4 py-3 text-sm text-signal-coral">
          {error}
        </div>
      )}

      {/* Loading skeleton */}
      {loading && (
        <div className="space-y-2">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="h-16 bg-surface border border-edge rounded-lg animate-pulse" />
          ))}
        </div>
      )}

      {/* Results */}
      {!loading && turns.length > 0 && (
        <>
          {/* Summary stats */}
          <ReplaySummary turns={turns} />

          {/* Action bar */}
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <button
                onClick={expandAll}
                className="text-xs text-ink-muted hover:text-ink underline underline-offset-2 transition-colors"
              >
                Expand all
              </button>
              <span className="text-ink-faint">·</span>
              <button
                onClick={collapseAll}
                className="text-xs text-ink-muted hover:text-ink underline underline-offset-2 transition-colors"
              >
                Collapse all
              </button>
            </div>
            <button
              onClick={exportJson}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-surface-raised text-ink border border-edge hover:bg-surface-overlay transition-colors"
            >
              <Download size={12} />
              Export JSON
            </button>
          </div>

          {/* Timeline */}
          <div className="space-y-2">
            {/* Legend */}
            <div className="flex items-center gap-4 px-1 pb-1 text-[10px] text-ink-faint">
              <span className="flex items-center gap-1.5">
                <span className="w-2 h-2 rounded-full bg-accent" /> LLM call
              </span>
              <span className="flex items-center gap-1.5">
                <span className="w-2 h-2 rounded-full bg-signal-amber" /> Tool call
              </span>
              <span className="flex items-center gap-1.5">
                <span className="w-2 h-2 rounded-full bg-signal-sky" /> Agent turn
              </span>
            </div>

            {turns.map((turn, i) => (
              <TurnRow
                key={turn.traceId || i}
                turn={turn}
                index={i}
                isExpanded={expandedIds.has(turn.traceId)}
                onToggle={() => toggleExpand(turn.traceId)}
              />
            ))}
          </div>
        </>
      )}

      {/* Empty state */}
      {!loading && sessionId && turns.length === 0 && !error && (
        <div className="flex flex-col items-center justify-center py-16 text-ink-muted gap-3">
          <Search size={32} className="text-ink-faint" />
          <div className="text-sm">No spans found for this session in the current time range.</div>
          <div className="text-xs text-ink-faint">
            Try expanding the time range in the header.
          </div>
        </div>
      )}

      {/* Initial state */}
      {!loading && !sessionId && (
        <div className="flex flex-col items-center justify-center py-16 text-ink-muted gap-3">
          <Play size={32} className="text-ink-faint" />
          <div className="text-sm">Enter a session ID above to replay its turns.</div>
          <div className="text-xs text-ink-faint">
            Click a session in the Session Explorer to copy its ID.
          </div>
        </div>
      )}
    </div>
  )
}
