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
      setTraces(transformTempoTraces(raw))
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
      <div className="flex items-center gap-2 text-gray-500 text-xs py-3 px-4">
        <Loader2 className="w-3 h-3 animate-spin" />
        Loading session…
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex items-center gap-2 text-red-400 text-xs py-3 px-4">
        <AlertTriangle className="w-3 h-3" />
        {error}
      </div>
    )
  }

  if (!traces.length) {
    return (
      <div className="text-gray-600 text-xs py-3 px-4 italic">
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
    <div className="bg-[#0d0d16] border border-[#2a2a3e] rounded-lg overflow-hidden">
      {/* Session summary */}
      <div className="px-4 py-3 border-b border-[#1e1e2e] flex flex-wrap gap-4">
        <div className="text-xs">
          <span className="text-gray-500">Session </span>
          <span className="text-violet-300 font-mono">{sessionId}</span>
        </div>
        <div className="flex flex-wrap gap-4 ml-auto">
          <Stat label="Calls" value={totalCalls.toLocaleString()} />
          <Stat label="Total Cost" value={`$${totalCost.toFixed(4)}`} color="text-emerald-400" />
          <Stat label="Tokens In" value={totalTokensIn.toLocaleString()} />
          <Stat label="Tokens Out" value={totalTokensOut.toLocaleString()} />
          <Stat label="Avg Latency" value={`${(avgLatencyMs / 1000).toFixed(2)}s`} />
        </div>
      </div>

      {/* Mini table */}
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="bg-[#0a0a0f]/70">
              <th className="px-3 py-2 text-left text-gray-500 font-medium whitespace-nowrap">Time</th>
              <th className="px-3 py-2 text-left text-gray-500 font-medium">Model</th>
              <th className="px-3 py-2 text-left text-gray-500 font-medium">Agent</th>
              <th className="px-3 py-2 text-right text-gray-500 font-medium whitespace-nowrap">Tokens In</th>
              <th className="px-3 py-2 text-right text-gray-500 font-medium whitespace-nowrap">Tokens Out</th>
              <th className="px-3 py-2 text-right text-gray-500 font-medium">Cost</th>
              <th className="px-3 py-2 text-right text-gray-500 font-medium">Latency</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((row, i) => (
              <tr
                key={row.traceId + i}
                className="border-t border-[#1e1e2e]/60 hover:bg-[#1e1e2e]/20 transition-colors"
              >
                <td className="px-3 py-2 text-gray-400 whitespace-nowrap tabular-nums">
                  {format(row.time, 'HH:mm:ss')}
                </td>
                <td className="px-3 py-2">
                  <span className="text-gray-300 bg-[#1e1e2e] px-1.5 py-0.5 rounded-full">
                    {row.model || '—'}
                  </span>
                </td>
                <td className="px-3 py-2">
                  <span className="text-violet-400 bg-violet-950/40 px-1.5 py-0.5 rounded-full">
                    {row.agentId || '—'}
                  </span>
                </td>
                <td className="px-3 py-2 text-right text-gray-400 tabular-nums">
                  {row.tokensIn > 0 ? row.tokensIn.toLocaleString() : '—'}
                </td>
                <td className="px-3 py-2 text-right text-gray-400 tabular-nums">
                  {row.tokensOut > 0 ? row.tokensOut.toLocaleString() : '—'}
                </td>
                <td className="px-3 py-2 text-right tabular-nums">
                  <span className={row.costUsd > 0 ? 'text-emerald-400' : 'text-gray-600'}>
                    {row.costUsd > 0 ? `$${row.costUsd.toFixed(4)}` : '—'}
                  </span>
                </td>
                <td className="px-3 py-2 text-right text-gray-400 tabular-nums whitespace-nowrap">
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
  color = 'text-gray-200',
}: {
  label: string
  value: string
  color?: string
}) {
  return (
    <div className="text-xs">
      <span className="text-gray-500">{label} </span>
      <span className={color}>{value}</span>
    </div>
  )
}
