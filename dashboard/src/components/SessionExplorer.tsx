import React, { useState } from 'react'
import { SessionNode, SessionEdge } from '../lib/queries'
import { TempoSpan } from '../lib/queries'
import { SessionGraph } from './SessionGraph'
import { SessionDetail, DailySummaryBanner } from './SessionDetail'

interface Props {
  nodes: SessionNode[]
  edges: SessionEdge[]
  rawTraces: TempoSpan[]
  loading: boolean
  error: string | null
}

export function SessionExplorer({ nodes, edges, rawTraces, loading, error }: Props) {
  const [selectedId, setSelectedId] = useState<string | null>(null)

  const selectedNode = selectedId ? (nodes.find((n) => n.sessionId === selectedId) ?? null) : null

  const handleSelect = (sessionId: string) => {
    setSelectedId((prev) => (prev === sessionId ? null : sessionId))
  }

  const handleClose = () => setSelectedId(null)

  return (
    <div className="space-y-4">
      {/* Section header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-sm font-semibold text-slate-200">Session Explorer</h2>
          <p className="text-xs text-slate-500 mt-0.5">
            Agent sessions, parent–child relationships, and cost per task
          </p>
        </div>
        {nodes.length > 0 && (
          <span className="text-xs text-slate-600 font-mono">
            {nodes.length} session{nodes.length !== 1 ? 's' : ''}
          </span>
        )}
      </div>

      {/* Daily summary */}
      <DailySummaryBanner nodes={nodes} loading={loading} />

      {/* Graph panels — Agents + Sessions side by side on desktop, stacked on mobile */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="bg-[#111118] border border-slate-800 rounded-xl p-4">
          <SessionGraph
            nodes={nodes}
            edges={edges}
            selectedId={selectedId}
            onSelect={handleSelect}
            loading={loading}
            error={error}
            fixedMode="agent"
            title="Agents"
          />
        </div>
        <div className="bg-[#111118] border border-slate-800 rounded-xl p-4">
          <SessionGraph
            nodes={nodes}
            edges={edges}
            selectedId={selectedId}
            onSelect={handleSelect}
            loading={loading}
            error={error}
            fixedMode="session"
            title="Sessions"
          />
        </div>
      </div>

      {/* Session list (sorted by cost desc — gives Obsidian "file list" feel) */}
      {nodes.length > 0 && (
        <div className="bg-[#111118] border border-slate-800 rounded-xl overflow-hidden">
          <div className="px-4 py-2.5 border-b border-slate-800 text-xs text-slate-500 uppercase tracking-wide">
            Sessions — sorted by cost
          </div>
          <div className="divide-y divide-slate-800/50 max-h-52 overflow-y-auto">
            {[...nodes].sort((a, b) => b.totalCost - a.totalCost).map((node) => {
              const isSelected = node.sessionId === selectedId
              return (
                <button
                  key={node.sessionId}
                  onClick={() => handleSelect(node.sessionId)}
                  className={`w-full flex items-center gap-3 px-4 py-2.5 text-left transition-colors hover:bg-slate-800/40 ${
                    isSelected ? 'bg-indigo-500/10' : ''
                  }`}
                >
                  {/* Type indicator */}
                  <span className={`w-2 h-2 rounded-full flex-shrink-0 ${
                    node.hasError ? 'bg-red-400' :
                    !node.parentSessionId ? 'bg-indigo-400' :
                    node.agentType === 'subagent' ? 'bg-sky-400' : 'bg-teal-400'
                  }`} />

                  {/* Session ID + task */}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-mono text-xs text-slate-400 truncate">
                        {node.sessionId.slice(0, 16)}{node.sessionId.length > 16 ? '…' : ''}
                      </span>
                      {node.taskLabel && (
                        <span className="text-xs text-indigo-300 truncate max-w-[180px]">
                          {node.taskLabel}
                        </span>
                      )}
                    </div>
                    <div className="text-[10px] text-slate-600 mt-0.5">
                      {node.agentId} · {node.callCount} call{node.callCount !== 1 ? 's' : ''}
                    </div>
                  </div>

                  {/* Cost */}
                  <span className={`text-xs tabular-nums flex-shrink-0 ${node.totalCost > 0 ? 'text-emerald-400' : 'text-slate-600'}`}>
                    {node.totalCost > 0
                      ? `$${node.totalCost < 0.01 ? node.totalCost.toFixed(4) : node.totalCost.toFixed(2)}`
                      : '$0'}
                  </span>
                </button>
              )
            })}
          </div>
        </div>
      )}

      {/* Session detail panel */}
      {selectedNode && (
        <SessionDetail
          node={selectedNode}
          allNodes={nodes}
          edges={edges}
          rawTraces={rawTraces}
          onClose={handleClose}
          onSelectSession={(id) => setSelectedId(id)}
        />
      )}
    </div>
  )
}
