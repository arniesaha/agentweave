import React, { useState, useMemo, useEffect } from 'react'
import { format } from 'date-fns'
import { ArrowRight, ChevronLeft, ChevronRight } from 'lucide-react'
import { MuxRoutingRow, buildRoutingReasonBreakdown, buildModelRedirectBreakdown } from '../lib/queries'
import { BarChartPanel } from './BarChart'

interface MuxRoutingProps {
  rows: MuxRoutingRow[]
  loading: boolean
  error?: string | null
}

type SortKey = 'time' | 'latencyMs' | 'tokensIn'
type SortDir = 'asc' | 'desc'

function SkeletonRow() {
  return (
    <tr className="border-b border-edge">
      {Array.from({ length: 7 }).map((_, i) => (
        <td key={i} className="px-3 py-3">
          <div className="skeleton h-4 rounded w-full" />
        </td>
      ))}
    </tr>
  )
}

export function MuxRouting({ rows, loading, error }: MuxRoutingProps) {
  const PAGE_SIZE = 25
  const [sortKey, setSortKey] = useState<SortKey>('time')
  const [sortDir, setSortDir] = useState<SortDir>('desc')
  const [page, setPage] = useState(0)

  useEffect(() => { setPage(0) }, [rows, sortKey, sortDir])

  const sorted = useMemo(() => {
    return [...rows].sort((a, b) => {
      const av = a[sortKey]
      const bv = b[sortKey]
      const cmp = av < bv ? -1 : av > bv ? 1 : 0
      return sortDir === 'asc' ? cmp : -cmp
    })
  }, [rows, sortKey, sortDir])

  const totalPages = Math.max(1, Math.ceil(sorted.length / PAGE_SIZE))
  const pageRows = sorted.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE)

  const redirectCount = rows.filter(r => r.redirected).length
  const redirectPct = rows.length > 0 ? ((redirectCount / rows.length) * 100).toFixed(1) : '0'

  const reasonBars = useMemo(() => buildRoutingReasonBreakdown(rows), [rows])
  const redirectBars = useMemo(() => buildModelRedirectBreakdown(rows), [rows])

  function handleSort(key: SortKey) {
    if (sortKey === key) {
      setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    } else {
      setSortKey(key)
      setSortDir('desc')
    }
  }

  function SortIcon({ active, dir }: { active: boolean; dir: SortDir }) {
    if (!active) return <span className="text-ink-faint ml-1">&#8597;</span>
    return <span className="text-accent ml-1">{dir === 'asc' ? '\u2191' : '\u2193'}</span>
  }

  return (
    <div className="space-y-5 animate-fade-in">
      {/* Stat summary */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="bg-surface border border-edge rounded-xl p-4">
          <div className="text-xs text-ink-faint mb-1">Total Routed</div>
          <div className="text-2xl font-semibold text-ink mono">{loading ? '...' : rows.length}</div>
        </div>
        <div className="bg-surface border border-edge rounded-xl p-4">
          <div className="text-xs text-ink-faint mb-1">Redirected</div>
          <div className="text-2xl font-semibold mono">
            <span className={redirectCount > 0 ? 'text-signal-amber' : 'text-ink'}>{loading ? '...' : redirectCount}</span>
            {!loading && rows.length > 0 && (
              <span className="text-sm text-ink-faint ml-2">({redirectPct}%)</span>
            )}
          </div>
        </div>
        <div className="bg-surface border border-edge rounded-xl p-4">
          <div className="text-xs text-ink-faint mb-1">Kept Original</div>
          <div className="text-2xl font-semibold text-signal-lime mono">
            {loading ? '...' : rows.length - redirectCount}
          </div>
        </div>
        <div className="bg-surface border border-edge rounded-xl p-4">
          <div className="text-xs text-ink-faint mb-1">Unique Reasons</div>
          <div className="text-2xl font-semibold text-accent mono">
            {loading ? '...' : reasonBars.length}
          </div>
        </div>
      </div>

      {/* Charts */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <BarChartPanel
          title="Routing Reasons"
          subtitle="How often each routing rule fired"
          data={reasonBars}
          loading={loading}
          error={error ?? null}
          valueFormatter={v => v.toFixed(0)}
        />
        <BarChartPanel
          title="Model Redirects"
          subtitle="Requested model vs resolved model"
          data={redirectBars}
          loading={loading}
          error={error ?? null}
          valueFormatter={v => v.toFixed(0)}
        />
      </div>

      {/* Routing decisions table */}
      <div className="bg-surface border border-edge rounded-xl overflow-hidden">
        <div className="px-4 py-3 border-b border-edge">
          <h3 className="text-sm font-medium text-ink">Routing Decisions</h3>
          <p className="text-xs text-ink-faint mt-0.5">Each row is a request routed through Mux</p>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-edge bg-void/50">
                <th
                  className="text-left px-3 py-2.5 text-ink-faint font-medium cursor-pointer select-none whitespace-nowrap"
                  onClick={() => handleSort('time')}
                >
                  Time <SortIcon active={sortKey === 'time'} dir={sortDir} />
                </th>
                <th className="text-left px-3 py-2.5 text-ink-faint font-medium whitespace-nowrap">Prompt</th>
                <th className="text-left px-3 py-2.5 text-ink-faint font-medium whitespace-nowrap">Routing</th>
                <th className="text-left px-3 py-2.5 text-ink-faint font-medium whitespace-nowrap">Reason</th>
                <th className="text-left px-3 py-2.5 text-ink-faint font-medium whitespace-nowrap">Runtime</th>
                <th
                  className="text-right px-3 py-2.5 text-ink-faint font-medium cursor-pointer select-none whitespace-nowrap"
                  onClick={() => handleSort('tokensIn')}
                >
                  Tokens <SortIcon active={sortKey === 'tokensIn'} dir={sortDir} />
                </th>
                <th
                  className="text-right px-3 py-2.5 text-ink-faint font-medium cursor-pointer select-none whitespace-nowrap"
                  onClick={() => handleSort('latencyMs')}
                >
                  Latency <SortIcon active={sortKey === 'latencyMs'} dir={sortDir} />
                </th>
              </tr>
            </thead>
            <tbody>
              {loading && Array.from({ length: 5 }).map((_, i) => <SkeletonRow key={i} />)}
              {!loading && error && (
                <tr><td colSpan={7} className="px-4 py-8 text-center text-signal-coral text-xs">{error}</td></tr>
              )}
              {!loading && !error && pageRows.length === 0 && (
                <tr><td colSpan={7} className="px-4 py-8 text-center text-ink-faint text-xs">No routing traces found in this time range</td></tr>
              )}
              {!loading && !error && pageRows.map((row) => (
                <tr key={`${row.traceId}-${row.time}`} className="border-b border-edge hover:bg-accent/3 transition-colors">
                  <td className="px-3 py-2.5 text-ink-muted mono whitespace-nowrap">
                    {format(new Date(row.time), 'HH:mm:ss')}
                  </td>
                  <td className="px-3 py-2.5 text-ink max-w-[300px] truncate" title={row.promptPreview}>
                    {row.promptPreview || <span className="text-ink-faint italic">no preview</span>}
                  </td>
                  <td className="px-3 py-2.5 whitespace-nowrap">
                    <span className="inline-flex items-center gap-1.5">
                      <span className={`mono text-xs ${row.redirected ? 'text-ink-muted line-through' : 'text-ink'}`}>
                        {row.requestedModel}
                      </span>
                      {row.redirected && (
                        <>
                          <ArrowRight className="w-3 h-3 text-signal-amber" />
                          <span className="mono text-xs text-signal-amber font-medium">
                            {row.resolvedModel}
                          </span>
                        </>
                      )}
                    </span>
                  </td>
                  <td className="px-3 py-2.5">
                    <span className="inline-block bg-accent/8 text-accent text-[10px] font-medium px-2 py-0.5 rounded-full">
                      {row.reason.replace('heuristic:', '')}
                    </span>
                  </td>
                  <td className="px-3 py-2.5 text-ink-muted mono">{row.runtime}</td>
                  <td className="px-3 py-2.5 text-right text-ink-muted mono whitespace-nowrap">
                    {row.tokensIn > 0 ? `${row.tokensIn.toLocaleString()} / ${row.tokensOut.toLocaleString()}` : '—'}
                  </td>
                  <td className="px-3 py-2.5 text-right text-ink-muted mono">
                    {(row.latencyMs / 1000).toFixed(1)}s
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="flex items-center justify-between px-4 py-2.5 border-t border-edge bg-void/30">
            <span className="text-xs text-ink-faint mono">
              {page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, sorted.length)} of {sorted.length}
            </span>
            <div className="flex gap-1">
              <button
                onClick={() => setPage(p => Math.max(0, p - 1))}
                disabled={page === 0}
                className="p-1 rounded text-ink-faint hover:text-ink disabled:opacity-30 transition-colors"
              >
                <ChevronLeft className="w-4 h-4" />
              </button>
              <button
                onClick={() => setPage(p => Math.min(totalPages - 1, p + 1))}
                disabled={page >= totalPages - 1}
                className="p-1 rounded text-ink-faint hover:text-ink disabled:opacity-30 transition-colors"
              >
                <ChevronRight className="w-4 h-4" />
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
