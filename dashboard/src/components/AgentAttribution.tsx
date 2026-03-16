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
    <div className="bg-[#1a1a2e]/50 border border-[#1e1e2e] border-dashed rounded-xl p-8 flex flex-col items-center justify-center gap-3">
      <Network className="w-8 h-8 text-gray-600" />
      <p className="text-gray-500 text-sm text-center max-w-md">{message}</p>
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
      <tr className="border-b border-[#1e1e2e]">
        {Array.from({ length: 6 }).map((_, i) => (
          <td key={i} className="px-4 py-3">
            <div className="skeleton h-4 rounded w-full" />
          </td>
        ))}
      </tr>
    )
  }

  return (
    <div className="bg-[#111118] border border-[#1e1e2e] rounded-xl overflow-hidden">
      <div className="px-5 py-4 border-b border-[#1e1e2e] flex items-center gap-3">
        <div className="p-2 rounded-lg bg-violet-500/10">
          <Users className="w-4 h-4 text-violet-400" />
        </div>
        <div>
          <h3 className="text-white font-semibold text-sm">Session Overview</h3>
          <p className="text-gray-500 text-xs mt-0.5">Sessions grouped by ID — cost, call count, and sub-agent activity</p>
        </div>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full">
          <thead className="bg-[#0a0a0f]/50">
            <tr>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Session ID</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Agent Type</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Calls</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Total Cost</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Sub-agents</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Last Active</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              Array.from({ length: 5 }).map((_, i) => <SkeletonRow key={i} />)
            ) : error ? (
              <tr>
                <td colSpan={6} className="px-4 py-12 text-center text-gray-600 text-sm">
                  Unable to load sessions — {error}
                </td>
              </tr>
            ) : !rows.length ? (
              <tr>
                <td colSpan={6} className="px-4 py-12 text-center">
                  <div className="flex flex-col items-center gap-2">
                    <Network className="w-6 h-6 text-gray-600" />
                    <p className="text-gray-500 text-sm">No sub-agent activity detected yet.</p>
                    <p className="text-gray-600 text-xs">Use <code className="text-indigo-400/80 bg-[#1e1e2e] px-1.5 py-0.5 rounded">@trace_agent</code> with <code className="text-indigo-400/80 bg-[#1e1e2e] px-1.5 py-0.5 rounded">agent_type=subagent</code> to track sub-agents.</p>
                  </div>
                </td>
              </tr>
            ) : (
              rows.map((row) => (
                <tr key={row.sessionId} className="border-b border-[#1e1e2e] hover:bg-[#1e1e2e]/30 transition-colors">
                  <td className="px-4 py-3">
                    <span className="text-xs text-gray-300 font-mono" title={row.sessionId}>
                      {row.sessionId.slice(0, 12)}{row.sessionId.length > 12 ? '…' : ''}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <AgentTypeBadge type={row.agentType} />
                  </td>
                  <td className="px-4 py-3 text-xs text-gray-300 tabular-nums">
                    {row.callCount}
                  </td>
                  <td className="px-4 py-3 text-xs tabular-nums">
                    <span className={row.totalCost > 0 ? 'text-emerald-400' : 'text-gray-600'}>
                      {row.totalCost > 0 ? `$${row.totalCost.toFixed(4)}` : '—'}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    {row.hasSubAgents ? (
                      <span className="text-xs text-indigo-400 bg-indigo-500/10 px-2 py-0.5 rounded-full">Yes</span>
                    ) : (
                      <span className="text-xs text-gray-600">No</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-xs text-gray-400 whitespace-nowrap">
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
      <tr className="border-b border-[#1e1e2e]">
        {Array.from({ length: 7 }).map((_, i) => (
          <td key={i} className="px-4 py-3">
            <div className="skeleton h-4 rounded w-full" />
          </td>
        ))}
      </tr>
    )
  }

  return (
    <div className="bg-[#111118] border border-[#1e1e2e] rounded-xl overflow-hidden">
      <div className="px-5 py-4 border-b border-[#1e1e2e] flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="p-2 rounded-lg bg-indigo-500/10">
            <GitBranch className="w-4 h-4 text-indigo-400" />
          </div>
          <div>
            <h3 className="text-white font-semibold text-sm">Sub-agent Traces</h3>
            <p className="text-gray-500 text-xs mt-0.5">Spans where prov.agent.type = "subagent" — with parent session linkage</p>
          </div>
        </div>
        {!loading && !error && (
          <span className="text-xs text-gray-500 bg-[#0a0a0f] px-2 py-1 rounded-lg border border-[#1e1e2e]">
            {rows.length} traces
          </span>
        )}
      </div>

      <div className="overflow-x-auto">
        <table className="w-full">
          <thead className="bg-[#0a0a0f]/50">
            <tr>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Time</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Trace ID</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Model</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Agent</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Cost</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Session</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Parent Session</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              Array.from({ length: 5 }).map((_, i) => <SkeletonRow key={i} />)
            ) : error ? (
              <tr>
                <td colSpan={7} className="px-4 py-12 text-center text-gray-600 text-sm">
                  Unable to load sub-agent traces — {error}
                </td>
              </tr>
            ) : !rows.length ? (
              <tr>
                <td colSpan={7} className="px-4 py-12 text-center">
                  <div className="flex flex-col items-center gap-2">
                    <GitBranch className="w-6 h-6 text-gray-600" />
                    <p className="text-gray-500 text-sm">No sub-agent activity detected yet.</p>
                    <p className="text-gray-600 text-xs">Use <code className="text-indigo-400/80 bg-[#1e1e2e] px-1.5 py-0.5 rounded">@trace_agent</code> with <code className="text-indigo-400/80 bg-[#1e1e2e] px-1.5 py-0.5 rounded">agent_type=subagent</code> to track sub-agents.</p>
                  </div>
                </td>
              </tr>
            ) : (
              rows.map((row) => (
                <tr key={row.traceId} className="border-b border-[#1e1e2e] hover:bg-[#1e1e2e]/30 transition-colors">
                  <td className="px-4 py-3 text-xs text-gray-400 whitespace-nowrap">
                    {format(row.time, 'MMM d HH:mm:ss')}
                  </td>
                  <td className="px-4 py-3">
                    <span className="text-xs text-indigo-400 font-mono">
                      {row.traceId.slice(0, 8)}…
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <span className="text-xs text-gray-300 bg-[#1e1e2e] px-2 py-0.5 rounded-full">
                      {row.model || '—'}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <span className="text-xs text-violet-400 bg-violet-950/40 px-2 py-0.5 rounded-full">
                      {row.agentId || '—'}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-xs tabular-nums">
                    <span className={row.costUsd > 0 ? 'text-emerald-400' : 'text-gray-600'}>
                      {row.costUsd > 0 ? `$${row.costUsd.toFixed(4)}` : '—'}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <span className="text-xs text-gray-300 font-mono" title={row.sessionId}>
                      {row.sessionId !== '—' ? row.sessionId.slice(0, 12) + (row.sessionId.length > 12 ? '…' : '') : '—'}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <span className="text-xs text-cyan-400 font-mono" title={row.parentSessionId}>
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
    main: 'text-indigo-400 bg-indigo-500/10',
    subagent: 'text-cyan-400 bg-cyan-500/10',
    delegated: 'text-amber-400 bg-amber-500/10',
  }
  return (
    <span className={`text-xs px-2 py-0.5 rounded-full ${styles[type] ?? 'text-gray-400 bg-[#1e1e2e]'}`}>
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
        <div className="h-px flex-1 bg-[#1e1e2e]" />
        <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider flex items-center gap-2">
          <Network className="w-4 h-4 text-indigo-400" />
          Agent Attribution
        </h2>
        <div className="h-px flex-1 bg-[#1e1e2e]" />
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
            <div className="bg-[#111118] border border-[#1e1e2e] rounded-xl p-5 flex flex-col gap-4">
              <div>
                <h3 className="text-white font-semibold text-sm">Agent Type Summary</h3>
                <p className="text-gray-500 text-xs mt-0.5">Quick breakdown of agent activity</p>
              </div>
              {attributionLoading ? (
                <div className="flex flex-col gap-3 py-2">
                  {Array.from({ length: 3 }).map((_, i) => (
                    <div key={i} className="skeleton h-10 rounded-lg w-full" />
                  ))}
                </div>
              ) : !hasAgentTypeData ? (
                <div className="h-48 flex items-center justify-center text-gray-600 text-sm">
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
                          <span className="text-xs text-gray-300 tabular-nums">
                            {item.value} <span className="text-gray-600">({pct.toFixed(0)}%)</span>
                          </span>
                        </div>
                        <div className="h-1.5 bg-[#0a0a0f] rounded-full overflow-hidden">
                          <div
                            className="h-full rounded-full bg-indigo-500/60 transition-all"
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
