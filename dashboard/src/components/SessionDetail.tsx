import React, { useMemo } from 'react'
import { SessionNode, SessionEdge, SessionCallRow, buildSessionCalls, buildDailySummary } from '../lib/queries'
import { TempoSpan } from '../lib/queries'

interface SessionDetailProps {
  node: SessionNode | null
  allNodes: SessionNode[]
  edges: SessionEdge[]
  rawTraces: TempoSpan[]
  onClose: () => void
  onSelectSession: (sessionId: string) => void
}

function formatCost(cost: number): string {
  if (cost === 0) return '$0.0000'
  if (cost < 0.01) return `$${cost.toFixed(4)}`
  return `$${cost.toFixed(4)}`
}

function formatTime(ms: number): string {
  return new Date(ms).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

function formatDuration(ms: number): string {
  if (!ms || ms <= 0) return '—'
  if (ms < 1000) return `${ms.toFixed(0)}ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
  return `${(ms / 60000).toFixed(1)}m`
}

function shortModel(model: string): string {
  // Shorten well-known model names
  return model
    .replace('claude-sonnet-4-', 'sonnet-4.')
    .replace('claude-haiku-', 'haiku-')
    .replace('claude-opus-', 'opus-')
    .replace('gpt-4o', 'gpt-4o')
    .replace('gemini-2.0-flash', 'gemini-2.0f')
    .replace('gemini-2.5-pro', 'gemini-2.5p')
}

export function SessionDetail({
  node,
  allNodes,
  edges,
  rawTraces,
  onClose,
  onSelectSession,
}: SessionDetailProps) {
  const calls: SessionCallRow[] = useMemo(() => {
    if (!node) return []
    return buildSessionCalls(rawTraces, node.sessionId)
  }, [node, rawTraces])

  const children = useMemo(() => {
    if (!node) return []
    const childIds = edges
      .filter((e) => e.from === node.sessionId)
      .map((e) => e.to)
    return allNodes.filter((n) => childIds.includes(n.sessionId))
  }, [node, allNodes, edges])

  if (!node) {
    return (
      <div className="flex flex-col items-center justify-center h-40 text-ink-muted text-sm gap-2">
        <svg className="w-8 h-8 text-ink-faint" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
            d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
        Select a session node to view details
      </div>
    )
  }

  const sessionStart = node.firstSeen ? new Date(node.firstSeen).toLocaleTimeString() : '—'
  const sessionEnd = node.lastSeen ? new Date(node.lastSeen).toLocaleTimeString() : '—'

  return (
    <div className="card overflow-hidden">
      {/* Header */}
      <div className="flex items-start justify-between px-4 py-3 border-b border-edge">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
              node.agentType === 'subagent'
                ? 'bg-signal-sky/10 text-signal-sky border border-signal-sky/20'
                : 'bg-accent/8 text-accent border border-accent/20'
            }`}>
              {node.agentType}
            </span>
            <span className="text-xs text-ink-muted mono truncate">{node.agentId}</span>
          </div>
          <div className="mt-1 mono text-xs text-ink-muted truncate">{node.sessionId}</div>
          {node.taskLabel && (
            <div className="mt-1 text-sm text-accent">📋 {node.taskLabel}</div>
          )}
        </div>
        <button
          onClick={onClose}
          className="ml-3 flex-shrink-0 text-ink-muted hover:text-ink transition-colors p-1"
          aria-label="Close"
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-px bg-edge">
        {[
          { label: 'LLM Calls', value: String(node.callCount) },
          { label: 'Total Cost', value: formatCost(node.totalCost) },
          { label: 'Tokens In', value: node.tokensIn.toLocaleString() },
          { label: 'Tokens Out', value: node.tokensOut.toLocaleString() },
        ].map(({ label, value }) => (
          <div key={label} className="bg-surface px-4 py-2.5">
            <div className="text-[10px] text-ink-muted uppercase tracking-wide">{label}</div>
            <div className="text-sm font-semibold text-ink mt-0.5">{value}</div>
          </div>
        ))}
      </div>

      {/* Time range */}
      <div className="flex items-center gap-4 px-4 py-2 text-xs text-ink-muted border-b border-edge">
        <span>Start: <span className="text-ink-muted">{sessionStart}</span></span>
        <span>End: <span className="text-ink-muted">{sessionEnd}</span></span>
        <span>Duration: <span className="text-ink-muted">{formatDuration(node.durationMs)}</span></span>
        {node.parentSessionId && (
          <span>
            Parent:{' '}
            <button
              className="text-signal-sky hover:text-signal-sky/80 mono underline underline-offset-2"
              onClick={() => onSelectSession(node.parentSessionId)}
            >
              {node.parentSessionId.slice(0, 12)}…
            </button>
          </span>
        )}
      </div>

      {/* Child sessions */}
      {children.length > 0 && (
        <div className="px-4 py-3 border-b border-edge">
          <div className="text-xs text-ink-muted mb-2 uppercase tracking-wide">
            Child Sessions ({children.length})
          </div>
          <div className="flex flex-wrap gap-2">
            {children.map((child) => (
              <button
                key={child.sessionId}
                onClick={() => onSelectSession(child.sessionId)}
                className="flex items-center gap-1.5 text-xs bg-surface-raised hover:bg-surface-overlay border border-edge rounded-lg px-2.5 py-1.5 text-ink transition-colors"
              >
                <span className="w-2 h-2 rounded-full bg-signal-sky flex-shrink-0" />
                <span className="mono">{child.sessionId.slice(0, 10)}…</span>
                {child.taskLabel && (
                  <span className="text-ink-muted truncate max-w-[120px]">{child.taskLabel}</span>
                )}
                <span className="text-signal-lime ml-1">{formatCost(child.totalCost)}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Call timeline */}
      <div className="px-4 py-3">
        <div className="text-xs text-ink-muted mb-3 uppercase tracking-wide">
          LLM Call Timeline ({calls.length} calls)
        </div>

        {calls.length === 0 ? (
          <div className="text-xs text-ink-faint py-4 text-center">
            No calls found for this session in the current time range.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-ink-muted border-b border-edge">
                  <th className="text-left py-1.5 pr-3 font-medium w-20">Time</th>
                  <th className="text-left py-1.5 pr-3 font-medium">Model</th>
                  <th className="text-right py-1.5 pr-3 font-medium">In</th>
                  <th className="text-right py-1.5 pr-3 font-medium">Out</th>
                  <th className="text-right py-1.5 pr-3 font-medium">Cost</th>
                  <th className="text-right py-1.5 font-medium">Latency</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-edge/50">
                {calls.map((call, i) => (
                  <tr key={call.traceId || i} className="hover:bg-surface-overlay transition-colors">
                    <td className="py-1.5 pr-3 text-ink-muted mono">
                      {formatTime(call.time)}
                    </td>
                    <td className="py-1.5 pr-3 text-ink mono truncate max-w-[140px]">
                      {shortModel(call.model)}
                    </td>
                    <td className="py-1.5 pr-3 text-right text-ink-muted mono">
                      {call.tokensIn > 0 ? call.tokensIn.toLocaleString() : '—'}
                    </td>
                    <td className="py-1.5 pr-3 text-right text-ink-muted mono">
                      {call.tokensOut > 0 ? call.tokensOut.toLocaleString() : '—'}
                    </td>
                    <td className="py-1.5 pr-3 text-right text-signal-lime mono">
                      {call.costUsd > 0 ? formatCost(call.costUsd) : '—'}
                    </td>
                    <td className="py-1.5 text-right text-ink-muted mono">
                      {call.latencyMs ? formatDuration(call.latencyMs) : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}

// ─── Daily Summary Banner ─────────────────────────────────────────────────────

interface DailySummaryBannerProps {
  nodes: SessionNode[]
  loading: boolean
}

export function DailySummaryBanner({ nodes, loading }: DailySummaryBannerProps) {
  const summary = useMemo(() => buildDailySummary(nodes), [nodes])

  if (loading) {
    return (
      <div className="h-12 bg-surface border border-edge rounded-xl animate-pulse" />
    )
  }

  if (nodes.length === 0) return null

  return (
    <div className="card px-5 py-3 flex flex-wrap items-center gap-x-6 gap-y-2 text-sm">
      <span className="text-ink-muted text-xs uppercase tracking-wide font-medium">Session Summary</span>
      <span className="text-ink">
        <span className="font-semibold text-accent">{summary.topLevelSessions}</span>
        <span className="text-ink-muted ml-1">top-level</span>
      </span>
      <span className="text-ink">
        <span className="font-semibold text-signal-sky">{summary.subAgentSessions}</span>
        <span className="text-ink-muted ml-1">sub-agents</span>
      </span>
      <span className="text-ink">
        <span className="font-semibold text-ink">{summary.totalCalls.toLocaleString()}</span>
        <span className="text-ink-muted ml-1">total calls</span>
      </span>
      <span className="text-ink">
        <span className="font-semibold text-signal-lime">
          ${summary.totalCost < 0.01 ? summary.totalCost.toFixed(4) : summary.totalCost.toFixed(2)}
        </span>
        <span className="text-ink-muted ml-1">total cost</span>
      </span>
    </div>
  )
}
