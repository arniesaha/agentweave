/**
 * PromptVersionFilter — filter and group traces by prompt version.
 *
 * Reads prov.prompt.name and prov.prompt.version attributes from trace rows,
 * presents a dropdown filter, and renders a summary table grouping latency/cost
 * by prompt version so teams can A/B test prompt changes.
 *
 * Issue #111 — prompt versioning and registry.
 */

import React, { useMemo, useState } from 'react'
import { Tag, ChevronDown, X } from 'lucide-react'
import { TraceRow, TempoSpan } from '../lib/queries'

interface Props {
  traces: TraceRow[]
  selectedPromptVersion: string | null
  onSelectPromptVersion: (v: string | null) => void
}

interface VersionSummary {
  promptName: string
  version: string
  count: number
  avgLatencyMs: number
  totalCostUsd: number
  avgTokensIn: number
  avgTokensOut: number
}

function buildVersionSummaries(traces: TraceRow[]): VersionSummary[] {
  const map = new Map<string, {
    promptName: string
    version: string
    latencies: number[]
    costs: number[]
    tokensIn: number[]
    tokensOut: number[]
  }>()

  for (const t of traces) {
    const name = t.attributes?.['prov.prompt.name']
    const version = t.attributes?.['prov.prompt.version']
    if (!name || !version) continue

    const key = `${name}@${version}`
    if (!map.has(key)) {
      map.set(key, { promptName: name, version, latencies: [], costs: [], tokensIn: [], tokensOut: [] })
    }
    const entry = map.get(key)!
    entry.latencies.push(t.latencyMs)
    entry.costs.push(t.costUsd)
    entry.tokensIn.push(t.tokensIn)
    entry.tokensOut.push(t.tokensOut)
  }

  return Array.from(map.values()).map(({ promptName, version, latencies, costs, tokensIn, tokensOut }) => ({
    promptName,
    version,
    count: latencies.length,
    avgLatencyMs: latencies.reduce((s, v) => s + v, 0) / latencies.length,
    totalCostUsd: costs.reduce((s, v) => s + v, 0),
    avgTokensIn: tokensIn.reduce((s, v) => s + v, 0) / tokensIn.length,
    avgTokensOut: tokensOut.reduce((s, v) => s + v, 0) / tokensOut.length,
  })).sort((a, b) => b.count - a.count)
}

export function PromptVersionFilter({ traces, selectedPromptVersion, onSelectPromptVersion }: Props) {
  const summaries = useMemo(() => buildVersionSummaries(traces), [traces])
  const [expanded, setExpanded] = useState(false)

  if (summaries.length === 0) return null

  const uniqueVersionKeys = summaries.map((s) => `${s.promptName}@${s.version}`)

  return (
    <div className="rounded-xl border border-edge bg-surface-raised p-4 space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Tag className="w-3.5 h-3.5 text-[#B88CFF]" />
          <span className="text-xs font-semibold text-ink">Prompt Versions</span>
          <span className="text-xs text-ink-muted mono">
            {summaries.length} version{summaries.length !== 1 ? 's' : ''}
          </span>
        </div>
        <div className="flex items-center gap-2">
          {selectedPromptVersion && (
            <button
              onClick={() => onSelectPromptVersion(null)}
              className="flex items-center gap-1 text-xs text-[#B88CFF] hover:text-[#B88CFF]/80 transition-colors"
            >
              <X className="w-3 h-3" />
              clear filter
            </button>
          )}
          <button
            onClick={() => setExpanded((e) => !e)}
            className="flex items-center gap-1 text-xs text-ink-muted hover:text-ink transition-colors"
          >
            {expanded ? 'hide' : 'show'} breakdown
            <ChevronDown className={`w-3 h-3 transition-transform ${expanded ? 'rotate-180' : ''}`} />
          </button>
        </div>
      </div>

      {/* Version pills */}
      <div className="flex flex-wrap gap-1.5">
        {summaries.map((s) => {
          const key = `${s.promptName}@${s.version}`
          const active = selectedPromptVersion === key
          return (
            <button
              key={key}
              onClick={() => onSelectPromptVersion(active ? null : key)}
              className={`
                inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs mono transition-all
                ${active
                  ? 'bg-accent/12 border border-accent/25 text-accent'
                  : 'bg-surface border border-edge text-ink-muted hover:border-edge-hover hover:text-ink'}
              `}
            >
              <span className="text-ink-muted">{s.promptName}</span>
              <span className="text-ink-faint">/</span>
              <span>{s.version}</span>
              <span className={`ml-0.5 ${active ? 'text-accent' : 'text-ink-muted'}`}>
                ×{s.count}
              </span>
            </button>
          )
        })}
      </div>

      {/* Expanded breakdown table */}
      {expanded && (
        <div className="mt-2 overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-ink-muted border-b border-edge">
                <th className="text-left py-1.5 pr-3 font-medium">Prompt</th>
                <th className="text-left py-1.5 pr-3 font-medium">Version</th>
                <th className="text-right py-1.5 pr-3 font-medium">Calls</th>
                <th className="text-right py-1.5 pr-3 font-medium">Avg Latency</th>
                <th className="text-right py-1.5 pr-3 font-medium">Avg In Tokens</th>
                <th className="text-right py-1.5 pr-3 font-medium">Avg Out Tokens</th>
                <th className="text-right py-1.5 font-medium">Total Cost</th>
              </tr>
            </thead>
            <tbody>
              {summaries.map((s) => {
                const key = `${s.promptName}@${s.version}`
                const active = selectedPromptVersion === key
                return (
                  <tr
                    key={key}
                    onClick={() => onSelectPromptVersion(active ? null : key)}
                    className={`
                      cursor-pointer border-b border-edge/30 transition-colors
                      ${active ? 'bg-accent/8' : 'hover:bg-surface-overlay'}
                    `}
                  >
                    <td className="py-1.5 pr-3 text-ink mono">{s.promptName}</td>
                    <td className="py-1.5 pr-3 mono">
                      <span className={`px-1.5 py-0.5 rounded text-xs ${active ? 'bg-accent/12 text-accent' : 'bg-surface-overlay text-ink-muted'}`}>
                        {s.version}
                      </span>
                    </td>
                    <td className="py-1.5 pr-3 text-right text-ink mono">{s.count}</td>
                    <td className="py-1.5 pr-3 text-right text-ink mono">
                      {s.avgLatencyMs >= 1000
                        ? `${(s.avgLatencyMs / 1000).toFixed(1)}s`
                        : `${Math.round(s.avgLatencyMs)}ms`}
                    </td>
                    <td className="py-1.5 pr-3 text-right text-ink-muted mono">
                      {Math.round(s.avgTokensIn).toLocaleString()}
                    </td>
                    <td className="py-1.5 pr-3 text-right text-ink-muted mono">
                      {Math.round(s.avgTokensOut).toLocaleString()}
                    </td>
                    <td className="py-1.5 text-right mono">
                      <span className={s.totalCostUsd > 0 ? 'text-signal-amber' : 'text-ink-muted'}>
                        ${s.totalCostUsd.toFixed(4)}
                      </span>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

/**
 * Filter a list of TraceRows by a selected prompt version key (name@version).
 */
export function filterByPromptVersion(traces: TraceRow[], selectedKey: string | null): TraceRow[] {
  if (!selectedKey) return traces
  const atIdx = selectedKey.lastIndexOf('@')
  const name = atIdx >= 0 ? selectedKey.slice(0, atIdx) : selectedKey
  const version = atIdx >= 0 ? selectedKey.slice(atIdx + 1) : ''
  return traces.filter(
    (t) =>
      t.attributes?.['prov.prompt.name'] === name &&
      t.attributes?.['prov.prompt.version'] === version,
  )
}

/**
 * Filter a list of TempoSpans by a selected prompt version key (name@version).
 * Works with raw Tempo responses where attributes are nested arrays.
 */
export function filterTempoSpansByPromptVersion(traces: TempoSpan[], selectedKey: string | null): TempoSpan[] {
  if (!selectedKey) return traces
  const atIdx = selectedKey.lastIndexOf('@')
  const name = atIdx >= 0 ? selectedKey.slice(0, atIdx) : selectedKey
  const version = atIdx >= 0 ? selectedKey.slice(atIdx + 1) : ''
  return traces.filter((t) => {
    const attrs = t.spanSets?.[0]?.spans?.[0]?.attributes ?? t.spanSet?.spans?.[0]?.attributes ?? []
    const pName = attrs.find((a) => a.key === 'prov.prompt.name')?.value?.stringValue
    const pVersion = attrs.find((a) => a.key === 'prov.prompt.version')?.value?.stringValue
    return pName === name && pVersion === version
  })
}
