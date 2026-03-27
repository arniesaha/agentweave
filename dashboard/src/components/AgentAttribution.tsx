import React, { useState } from 'react'
import { Users, GitBranch, Network } from 'lucide-react'
import { format } from 'date-fns'
import { BarChartPanel } from './BarChart'
import {
  AgentAttributionRow,
  SubagentTraceRow,
  SessionOverviewRow,
  buildCallsByAgentType,
  buildSessionOverview,
} from '../lib/queries'

// ─── Empty State ──────────────────────────────────────────────────────────────

function EmptyState({ message }: { message: string }) {
  return (
    <div className="bg-surface-overlay/50 border border-edge border-dashed rounded-xl p-8 flex flex-col items-center justify-center gap-3">
      <Network className="w-8 h-8 text-ink-faint" />
      <p className="text-ink-muted text-sm text-center max-w-md">{message}</p>
    </div>
  )
}

// ─── Session Overview Table ───────────────────────────────────────────────────

function SessionOverviewTable({
  rows,
  loading,
  error,
}: {
  rows: SessionOverviewRow[]
  loading: boolean
  error: string | null
}) {
  function SkeletonRow() {
    return (
      <tr className="border-b border-edge">
        {Array.from({ length: 6 }).map((_, i) => (
          <td key={i} className="px-4 py-3">
            <div className="skeleton h-4 rounded w-full" />
          </td>
        ))}
      </tr>
    )
  }

  return (
    <div className="card overflow-hidden">
      <div className="px-5 py-4 border-b border-edge flex items-center gap-3">
        <div className="p-2 rounded-lg bg-[#B88CFF]/10">
          <Users className="w-4 h-4 text-[#B88CFF]" />
        </div>
        <div>
          <h3 className="text-ink font-semibold text-sm">Session Overview</h3>
          <p className="text-ink-muted text-xs mt-0.5">Sessions grouped by ID — cost, call count, and sub-agent activity</p>
        </div>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full">
          <thead className="bg-void/50">
            <tr>
              <th className="px-4 py-3 text-left text-xs font-medium text-ink-muted uppercase tracking-wider">Session ID</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-ink-muted uppercase tracking-wider">Agent Type</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-ink-muted uppercase tracking-wider">Calls</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-ink-muted uppercase tracking-wider">Total Cost</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-ink-muted uppercase tracking-wider">Sub-agents</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-ink-muted uppercase tracking-wider">Last Active</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              Array.from({ length: 5 }).map((_, i) => <SkeletonRow key={i} />)
            ) : error ? (
              <tr>
                <td colSpan={6} className="px-4 py-12 text-center text-ink-faint text-sm">
                  Unable to load sessions — {error}
                </td>
              </tr>
            ) : !rows.length ? (
              <tr>
                <td colSpan={6} className="px-4 py-12 text-center">
                  <div className="flex flex-col items-center gap-2">
                    <Network className="w-6 h-6 text-ink-faint" />
                    <p className="text-ink-muted text-sm">No sub-agent activity detected yet.</p>
                    <p className="text-ink-faint text-xs">Use <code className="text-accent/80 bg-surface-overlay px-1.5 py-0.5 rounded">@trace_agent</code> with <code className="text-accent/80 bg-surface-overlay px-1.5 py-0.5 rounded">agent_type=subagent</code> to track sub-agents.</p>
                  </div>
                </td>
              </tr>
            ) : (
              rows.map((row) => (
                <tr key={row.sessionId} className="border-b border-edge hover:bg-surface-overlay transition-colors">
                  <td className="px-4 py-3">
                    <span className="text-xs text-ink mono" title={row.sessionId}>
                      {row.sessionId.slice(0, 12)}{row.sessionId.length > 12 ? '…' : ''}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <AgentTypeBadge type={row.agentType} />
                  </td>
                  <td className="px-4 py-3 text-xs text-ink mono">
                    {row.callCount}
                  </td>
                  <td className="px-4 py-3 text-xs mono">
                    <span className={row.totalCost > 0 ? 'text-signal-lime' : 'text-ink-faint'}>
                      {row.totalCost > 0 ? `$${row.totalCost.toFixed(4)}` : '—'}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    {row.hasSubAgents ? (
                      <span className="text-xs text-accent bg-accent/8 px-2 py-0.5 rounded-full">Yes</span>
                    ) : (
                      <span className="text-xs text-ink-faint">No</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-xs text-ink-muted whitespace-nowrap">
                    {format(row.lastActive, 'MMM d HH:mm:ss')}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ─── Sub-agent Trace Table ────────────────────────────────────────────────────

function SubagentTraceTable({
  rows,
  loading,
  error,
}: {
  rows: SubagentTraceRow[]
  loading: boolean
  error: string | null
}) {
  function SkeletonRow() {
    return (
      <tr className="border-b border-edge">
        {Array.from({ length: 7 }).map((_, i) => (
          <td key={i} className="px-4 py-3">
            <div className="skeleton h-4 rounded w-full" />
          </td>
        ))}
      </tr>
    )
  }

  return (
    <div className="card overflow-hidden">
      <div className="px-5 py-4 border-b border-edge flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="p-2 rounded-lg bg-accent/8">
            <GitBranch className="w-4 h-4 text-accent" />
          </div>
          <div>
            <h3 className="text-ink font-semibold text-sm">Sub-agent Traces</h3>
            <p className="text-ink-muted text-xs mt-0.5">Spans where prov.agent.type = "subagent" — with parent session linkage</p>
          </div>
        </div>
        {!loading && !error && (
          <span className="text-xs text-ink-muted bg-void px-2 py-1 rounded-lg border border-edge">
            {rows.length} traces
          </span>
        )}
      </div>

      <div className="overflow-x-auto">
        <table className="w-full">
          <thead className="bg-void/50">
            <tr>
              <th className="px-4 py-3 text-left text-xs font-medium text-ink-muted uppercase tracking-wider">Time</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-ink-muted uppercase tracking-wider">Trace ID</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-ink-muted uppercase tracking-wider">Model</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-ink-muted uppercase tracking-wider">Agent</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-ink-muted uppercase tracking-wider">Cost</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-ink-muted uppercase tracking-wider">Session</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-ink-muted uppercase tracking-wider">Parent Session</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              Array.from({ length: 5 }).map((_, i) => <SkeletonRow key={i} />)
            ) : error ? (
              <tr>
                <td colSpan={7} className="px-4 py-12 text-center text-ink-faint text-sm">
                  Unable to load sub-agent traces — {error}
                </td>
              </tr>
            ) : !rows.length ? (
              <tr>
                <td colSpan={7} className="px-4 py-12 text-center">
                  <div className="flex flex-col items-center gap-2">
                    <GitBranch className="w-6 h-6 text-ink-faint" />
                    <p className="text-ink-muted text-sm">No sub-agent activity detected yet.</p>
                    <p className="text-ink-faint text-xs">Use <code className="text-accent/80 bg-surface-overlay px-1.5 py-0.5 rounded">@trace_agent</code> with <code className="text-accent/80 bg-surface-overlay px-1.5 py-0.5 rounded">agent_type=subagent</code> to track sub-agents.</p>
                  </div>
                </td>
              </tr>
            ) : (
              rows.map((row) => (
                <tr key={row.traceId} className="border-b border-edge hover:bg-surface-overlay transition-colors">
                  <td className="px-4 py-3 text-xs text-ink-muted whitespace-nowrap">
                    {format(row.time, 'MMM d HH:mm:ss')}
                  </td>
                  <td className="px-4 py-3">
                    <span className="text-xs text-accent mono">
                      {row.traceId.slice(0, 8)}…
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <span className="text-xs text-ink bg-surface-overlay px-2 py-0.5 rounded-full">
                      {row.model || '—'}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <span className="text-xs text-[#B88CFF] bg-[#B88CFF]/10 px-2 py-0.5 rounded-full">
                      {row.agentId || '—'}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-xs mono">
                    <span className={row.costUsd > 0 ? 'text-signal-lime' : 'text-ink-faint'}>
                      {row.costUsd > 0 ? `$${row.costUsd.toFixed(4)}` : '—'}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <span className="text-xs text-ink mono" title={row.sessionId}>
                      {row.sessionId !== '—' ? row.sessionId.slice(0, 12) + (row.sessionId.length > 12 ? '…' : '') : '—'}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <span className="text-xs text-signal-sky mono" title={row.parentSessionId}>
                      {row.parentSessionId !== '—' ? row.parentSessionId.slice(0, 12) + (row.parentSessionId.length > 12 ? '…' : '') : '—'}
                    </span>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ─── Agent Type Badge ─────────────────────────────────────────────────────────

function AgentTypeBadge({ type }: { type: string }) {
  const styles: Record<string, string> = {
    main: 'text-accent bg-accent/8',
    subagent: 'text-signal-sky bg-signal-sky/10',
    delegated: 'text-signal-amber bg-signal-amber/10',
  }
  return (
    <span className={`text-xs px-2 py-0.5 rounded-full ${styles[type] ?? 'text-ink-muted bg-surface-overlay'}`}>
      {type}
    </span>
  )
}

// ─── Main Component ───────────────────────────────────────────────────────────

interface AgentAttributionProps {
  attributionRows: AgentAttributionRow[]
  attributionLoading: boolean
  attributionError: string | null
  subagentRows: SubagentTraceRow[]
  subagentLoading: boolean
  subagentError: string | null
}

export function AgentAttribution({
  attributionRows,
  attributionLoading,
  attributionError,
  subagentRows,
  subagentLoading,
  subagentError,
}: AgentAttributionProps) {
  const callsByAgentType = buildCallsByAgentType(attributionRows)
  const sessionOverview = buildSessionOverview(attributionRows)

  // Check if we have any agent attribution data at all
  const hasAttributionData = !attributionLoading && !attributionError && attributionRows.length > 0
  const hasAgentTypeData = callsByAgentType.length > 0

  return (
    <div className="space-y-6">
      {/* Section Header */}
      <div className="flex items-center gap-3">
        <div className="h-px flex-1 bg-edge" />
        <h2 className="text-sm font-semibold text-ink-muted uppercase tracking-wider flex items-center gap-2">
          <Network className="w-4 h-4 text-accent" />
          Agent Attribution
        </h2>
        <div className="h-px flex-1 bg-edge" />
      </div>

      {/* Row 1: Calls by Agent Type */}
      {!hasAttributionData && !attributionLoading ? (
        <EmptyState message="No sub-agent activity detected yet. Use @trace_agent with agent_type=subagent to track sub-agents." />
      ) : (
        <>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <BarChartPanel
              title="Calls by Agent Type"
              subtitle="LLM calls grouped by prov.agent.type (main / subagent / delegated)"
              data={callsByAgentType}
              loading={attributionLoading}
              error={attributionError}
              valueFormatter={(v) => v.toFixed(0)}
            />
            <div className="card p-5 flex flex-col gap-4">
              <div>
                <h3 className="text-ink font-semibold text-sm">Agent Type Summary</h3>
                <p className="text-ink-muted text-xs mt-0.5">Quick breakdown of agent activity</p>
              </div>
              {attributionLoading ? (
                <div className="flex flex-col gap-3 py-2">
                  {Array.from({ length: 3 }).map((_, i) => (
                    <div key={i} className="skeleton h-10 rounded-lg w-full" />
                  ))}
                </div>
              ) : !hasAgentTypeData ? (
                <div className="h-48 flex items-center justify-center text-ink-faint text-sm">
                  No agent type data for this period
                </div>
              ) : (
                <div className="flex flex-col gap-3 py-2">
                  {callsByAgentType.map((item) => {
                    const total = callsByAgentType.reduce((s, i) => s + i.value, 0)
                    const pct = total > 0 ? (item.value / total) * 100 : 0
                    return (
                      <div key={item.label} className="space-y-1.5">
                        <div className="flex items-center justify-between">
                          <AgentTypeBadge type={item.label} />
                          <span className="text-xs text-ink mono">
                            {item.value} <span className="text-ink-faint">({pct.toFixed(0)}%)</span>
                          </span>
                        </div>
                        <div className="h-1.5 bg-void rounded-full overflow-hidden">
                          <div
                            className="h-full rounded-full bg-accent/60 transition-all"
                            style={{ width: `${pct}%` }}
                          />
                        </div>
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          </div>

          {/* Row 2: Session Overview Table */}
          <SessionOverviewTable
            rows={sessionOverview}
            loading={attributionLoading}
            error={attributionError}
          />

          {/* Row 3: Sub-agent Trace Table */}
          <SubagentTraceTable
            rows={subagentRows}
            loading={subagentLoading}
            error={subagentError}
          />
        </>
      )}
    </div>
  )
}
