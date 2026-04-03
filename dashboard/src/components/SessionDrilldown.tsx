import React, { useState, useEffect, useCallback } from 'react'
import { format } from 'date-fns'
import { Loader2, AlertTriangle } from 'lucide-react'
import {
  tempoSessionQuery,
  transformTempoTraces,
  TraceRow,
  getTimeRangeBounds,
} from '../lib/queries'
import type { TempoSpan } from '../lib/queries'

interface SessionDrilldownProps {
  sessionId: string
}

const TEMPO_BASE = '/tempo'

function useSessionTraces(sessionId: string) {
  const [traces, setTraces] = useState<TraceRow[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const fetch_ = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      // Fetch last 7 days to cover the full session
      const { start, end } = getTimeRangeBounds('7d')
      const params = new URLSearchParams({
        q: tempoSessionQuery(sessionId),
        limit: '500',
        start: String(start),
        end: String(end),
      })
      const resp = await fetch(`${TEMPO_BASE}/api/search?${params}`)
      if (!resp.ok) throw new Error(`Tempo search failed: ${resp.status}`)
      const data = await resp.json()
      const raw: TempoSpan[] = data.traces ?? []
      // tempoSessionQuery already filters to llm_call spans, but keep one extra
      // client-side guard so agent_turn lifecycle spans never show up in the
      // drilldown if the query broadens again later.
      const llmOnly = raw.filter((t) => t.rootTraceName?.startsWith('llm.'))
      setTraces(transformTempoTraces(llmOnly))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Tempo unavailable')
      setTraces([])
    } finally {
      setLoading(false)
    }
  }, [sessionId])

  useEffect(() => { fetch_() }, [fetch_])

  return { traces, loading, error }
}

export function SessionDrilldown({ sessionId }: SessionDrilldownProps) {
  const { traces, loading, error } = useSessionTraces(sessionId)

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-ink-muted text-xs py-3 px-4">
        <Loader2 className="w-3 h-3 animate-spin" />
        Loading session…
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex items-center gap-2 text-signal-coral text-xs py-3 px-4">
        <AlertTriangle className="w-3 h-3" />
        {error}
      </div>
    )
  }

  if (!traces.length) {
    return (
      <div className="text-ink-faint text-xs py-3 px-4 italic">
        No calls found for this session.
      </div>
    )
  }

  // Compute session summary
  const totalCalls = traces.length
  const totalCost = traces.reduce((s, t) => s + t.costUsd, 0)
  const totalTokensIn = traces.reduce((s, t) => s + t.tokensIn, 0)
  const totalTokensOut = traces.reduce((s, t) => s + t.tokensOut, 0)
  const avgLatencyMs =
    traces.reduce((s, t) => s + t.latencyMs, 0) / traces.length

  const sorted = [...traces].sort((a, b) => a.time - b.time)

  return (
    <div className="bg-surface border border-edge rounded-lg overflow-hidden">
      {/* Session summary */}
      <div className="px-4 py-3 border-b border-edge flex flex-wrap gap-4">
        <div className="text-xs">
          <span className="text-ink-muted">Session </span>
          <span className="text-[#B88CFF] mono">{sessionId}</span>
        </div>
        <div className="flex flex-wrap gap-4 ml-auto">
          <Stat label="Calls" value={totalCalls.toLocaleString()} />
          <Stat label="Total Cost" value={`$${totalCost.toFixed(4)}`} color="text-signal-lime" />
          <Stat label="Tokens In" value={totalTokensIn.toLocaleString()} />
          <Stat label="Tokens Out" value={totalTokensOut.toLocaleString()} />
          <Stat label="Avg Latency" value={`${(avgLatencyMs / 1000).toFixed(2)}s`} />
        </div>
      </div>

      {/* Mini table */}
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="bg-void/70">
              <th className="px-3 py-2 text-left text-ink-muted font-medium whitespace-nowrap">Time</th>
              <th className="px-3 py-2 text-left text-ink-muted font-medium">Model</th>
              <th className="px-3 py-2 text-left text-ink-muted font-medium">Agent</th>
              <th className="px-3 py-2 text-right text-ink-muted font-medium whitespace-nowrap">Tokens In</th>
              <th className="px-3 py-2 text-right text-ink-muted font-medium whitespace-nowrap">Tokens Out</th>
              <th className="px-3 py-2 text-right text-ink-muted font-medium">Cost</th>
              <th className="px-3 py-2 text-right text-ink-muted font-medium">Latency</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((row, i) => (
              <tr
                key={row.traceId + i}
                className="border-t border-edge/60 hover:bg-surface-overlay transition-colors"
              >
                <td className="px-3 py-2 text-ink-muted whitespace-nowrap mono">
                  {format(row.time, 'HH:mm:ss')}
                </td>
                <td className="px-3 py-2">
                  <span className="text-ink bg-surface-overlay px-1.5 py-0.5 rounded-full">
                    {row.model || '—'}
                  </span>
                </td>
                <td className="px-3 py-2">
                  <span className="text-[#B88CFF] bg-[#B88CFF]/10 px-1.5 py-0.5 rounded-full">
                    {row.agentId || '—'}
                  </span>
                </td>
                <td className="px-3 py-2 text-right text-ink-muted mono">
                  {row.tokensIn > 0 ? row.tokensIn.toLocaleString() : '—'}
                </td>
                <td className="px-3 py-2 text-right text-ink-muted mono">
                  {row.tokensOut > 0 ? row.tokensOut.toLocaleString() : '—'}
                </td>
                <td className="px-3 py-2 text-right mono">
                  <span className={row.costUsd > 0 ? 'text-signal-lime' : 'text-ink-faint'}>
                    {row.costUsd > 0 ? `$${row.costUsd.toFixed(4)}` : '—'}
                  </span>
                </td>
                <td className="px-3 py-2 text-right text-ink-muted mono whitespace-nowrap">
                  {row.latencyMs > 0 ? `${row.latencyMs.toFixed(0)} ms` : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function Stat({
  label,
  value,
  color = 'text-ink',
}: {
  label: string
  value: string
  color?: string
}) {
  return (
    <div className="text-xs">
      <span className="text-ink-muted">{label} </span>
      <span className={color}>{value}</span>
    </div>
  )
}
