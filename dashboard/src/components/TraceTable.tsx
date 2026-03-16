import React, { useState, useMemo } from 'react'
import { ChevronDown, ChevronRight, ExternalLink } from 'lucide-react'
import { format } from 'date-fns'
import { TraceRow } from '../lib/queries'

interface TraceTableProps {
  traces: TraceRow[]
  loading: boolean
  error?: string | null
}

type SortKey = 'time' | 'latencyMs' | 'tokensIn' | 'tokensOut' | 'costUsd'
type SortDir = 'asc' | 'desc'

function SkeletonRow() {
  return (
    <tr className="border-b border-[#1e1e2e]">
      {Array.from({ length: 7 }).map((_, i) => (
        <td key={i} className="px-4 py-3">
          <div className="skeleton h-4 rounded w-full" />
        </td>
      ))}
    </tr>
  )
}

function AttributeGrid({ attrs }: { attrs: Record<string, string> }) {
  const entries = Object.entries(attrs).filter(([, v]) => v)
  if (!entries.length) return <p className="text-gray-500 text-xs italic">No attributes</p>
  return (
    <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-2">
      {entries.map(([k, v]) => (
        <div key={k} className="bg-[#0a0a0f] rounded-lg p-2">
          <div className="text-gray-500 text-xs font-medium truncate">{k}</div>
          <div className="text-gray-200 text-xs mt-0.5 break-all">{v}</div>
        </div>
      ))}
    </div>
  )
}

function SortIcon({ active, dir }: { active: boolean; dir: SortDir }) {
  if (!active) return <span className="text-gray-600 ml-1">↕</span>
  return <span className="text-indigo-400 ml-1">{dir === 'asc' ? '↑' : '↓'}</span>
}

export function TraceTable({ traces, loading, error }: TraceTableProps) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [sortKey, setSortKey] = useState<SortKey>('time')
  const [sortDir, setSortDir] = useState<SortDir>('desc')

  const sorted = useMemo(() => {
    return [...traces].sort((a, b) => {
      const av = a[sortKey]
      const bv = b[sortKey]
      const cmp = av < bv ? -1 : av > bv ? 1 : 0
      return sortDir === 'asc' ? cmp : -cmp
    })
  }, [traces, sortKey, sortDir])

  function handleSort(key: SortKey) {
    if (sortKey === key) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortKey(key)
      setSortDir('desc')
    }
  }

  function toggleExpand(id: string) {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const ThSortable = ({ label, k }: { label: string; k: SortKey }) => (
    <th
      className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider cursor-pointer hover:text-gray-300 select-none whitespace-nowrap"
      onClick={() => handleSort(k)}
    >
      {label}
      <SortIcon active={sortKey === k} dir={sortDir} />
    </th>
  )

  return (
    <div className="bg-[#111118] border border-[#1e1e2e] rounded-xl overflow-hidden">
      <div className="px-5 py-4 border-b border-[#1e1e2e] flex items-center justify-between">
        <div>
          <h3 className="text-white font-semibold text-sm">Recent LLM Calls</h3>
          <p className="text-gray-500 text-xs mt-0.5">Latest traces from Tempo — click row to expand</p>
        </div>
        {!loading && !error && (
          <span className="text-xs text-gray-500 bg-[#0a0a0f] px-2 py-1 rounded-lg border border-[#1e1e2e]">
            {sorted.length} traces
          </span>
        )}
      </div>

      <div className="overflow-x-auto">
        <table className="w-full">
          <thead className="bg-[#0a0a0f]/50">
            <tr>
              <th className="px-4 py-3 w-8" />
              <ThSortable label="Time" k="time" />
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Trace ID</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Model</th>
              <ThSortable label="Latency" k="latencyMs" />
              <ThSortable label="Tokens In" k="tokensIn" />
              <ThSortable label="Tokens Out" k="tokensOut" />
              <ThSortable label="Cost" k="costUsd" />
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Session</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              Array.from({ length: 8 }).map((_, i) => <SkeletonRow key={i} />)
            ) : error ? (
              <tr>
                <td colSpan={9} className="px-4 py-12 text-center text-gray-600 text-sm">
                  Unable to load traces — {error}
                </td>
              </tr>
            ) : !sorted.length ? (
              <tr>
                <td colSpan={9} className="px-4 py-12 text-center text-gray-600 text-sm">
                  No traces found for this time range
                </td>
              </tr>
            ) : (
              sorted.map((row) => {
                const isOpen = expanded.has(row.traceId)
                return (
                  <React.Fragment key={row.traceId}>
                    <tr
                      className="border-b border-[#1e1e2e] hover:bg-[#1e1e2e]/30 cursor-pointer transition-colors"
                      onClick={() => toggleExpand(row.traceId)}
                    >
                      <td className="px-4 py-3 w-8">
                        {isOpen ? (
                          <ChevronDown className="w-4 h-4 text-gray-500" />
                        ) : (
                          <ChevronRight className="w-4 h-4 text-gray-500" />
                        )}
                      </td>
                      <td className="px-4 py-3 text-xs text-gray-400 whitespace-nowrap">
                        {format(row.time, 'MMM d HH:mm:ss')}
                      </td>
                      <td className="px-4 py-3">
                        <a
                          href={`http://192.168.1.70:31989/trace/${row.traceId}`}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="flex items-center gap-1 text-indigo-400 hover:text-indigo-300 text-xs font-mono"
                          onClick={(e) => e.stopPropagation()}
                        >
                          {row.traceId.slice(0, 8)}…
                          <ExternalLink className="w-3 h-3" />
                        </a>
                      </td>
                      <td className="px-4 py-3">
                        <span className="text-xs text-gray-300 bg-[#1e1e2e] px-2 py-0.5 rounded-full">
                          {row.model || '—'}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-xs text-gray-300 tabular-nums whitespace-nowrap">
                        {row.latencyMs > 0 ? `${row.latencyMs.toFixed(0)} ms` : '—'}
                      </td>
                      <td className="px-4 py-3 text-xs text-gray-400 tabular-nums">
                        {row.tokensIn > 0 ? row.tokensIn.toLocaleString() : '—'}
                      </td>
                      <td className="px-4 py-3 text-xs text-gray-400 tabular-nums">
                        {row.tokensOut > 0 ? row.tokensOut.toLocaleString() : '—'}
                      </td>
                      <td className="px-4 py-3 text-xs tabular-nums">
                        <span className={row.costUsd > 0 ? 'text-emerald-400' : 'text-gray-600'}>
                          {row.costUsd > 0 ? `$${row.costUsd.toFixed(4)}` : '—'}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-xs text-gray-500 font-mono max-w-24 truncate">
                        {row.sessionId}
                      </td>
                    </tr>
                    {isOpen && (
                      <tr className="bg-[#0a0a0f]/80 border-b border-[#1e1e2e]">
                        <td colSpan={9} className="px-6 py-4">
                          <div className="space-y-2">
                            <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">
                              Span Attributes
                            </p>
                            <AttributeGrid attrs={row.attributes} />
                          </div>
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                )
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
