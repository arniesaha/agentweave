import React, { useState, useEffect } from 'react'
import { SessionNode, SessionEdge } from '../lib/queries'
import { TempoSpan, tempoSessionQuery } from '../lib/queries'
import { SessionGraph } from './SessionGraph'
import { SessionDetail, DailySummaryBanner } from './SessionDetail'
import { X, ExternalLink } from 'lucide-react'

interface Props {
  nodes: SessionNode[]
  edges: SessionEdge[]
  rawTraces: TempoSpan[]
  loading: boolean
  error: string | null
}

export function SessionExplorer({ nodes, edges, rawTraces, loading, error }: Props) {
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [fullscreenPanel, setFullscreenPanel] = useState<null | 0 | 1>(null)
  const [replaySessionId, setReplaySessionId] = useState('')

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setFullscreenPanel(null)
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [])

  useEffect(() => {
    if (fullscreenPanel !== null) {
      document.body.style.overflow = 'hidden'
    } else {
      document.body.style.overflow = ''
    }
    return () => { document.body.style.overflow = '' }
  }, [fullscreenPanel])

  const selectedNode = selectedId ? (nodes.find((n) => n.sessionId === selectedId) ?? null) : null

  const handleSelect = (sessionId: string) => {
    setSelectedId((prev) => (prev === sessionId ? null : sessionId))
    setReplaySessionId(sessionId)
  }

  const handleClose = () => setSelectedId(null)

  const openSessionReplay = () => {
    const sid = replaySessionId.trim()
    if (!sid) return
    const query = tempoSessionQuery(sid)
    const left = JSON.stringify({
      datasource: 'tempo',
      queries: [{ refId: 'A', query, queryType: 'traceql' }],
      range: { from: 'now-1h', to: 'now' },
    })
    window.open(
      `https://o11y.arnabsaha.com/explore?orgId=1&left=${encodeURIComponent(left)}`,
      '_blank',
    )
  }

  return (
    <div className="space-y-4">
      {/* Section header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-sm font-semibold text-ink">Session Explorer</h2>
          <p className="text-xs text-ink-muted mt-0.5">
            Agent sessions, parent–child relationships, and cost per task
          </p>
        </div>
        {nodes.length > 0 && (
          <span className="text-xs text-ink-faint mono">
            {nodes.length} session{nodes.length !== 1 ? 's' : ''}
          </span>
        )}
      </div>

      {/* Session replay — open in Grafana Explore */}
      <div className="flex items-center gap-2">
        <input
          type="text"
          value={replaySessionId}
          onChange={(e) => setReplaySessionId(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') openSessionReplay() }}
          placeholder="Click a session node or paste ID to replay in Tempo…"
          className="flex-1 bg-surface border border-edge rounded-lg px-3 py-1.5 text-xs text-ink placeholder:text-ink-faint focus:outline-none focus:border-accent/60"
        />
        <button
          onClick={openSessionReplay}
          disabled={!replaySessionId.trim()}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-accent/12 text-accent border border-accent/25 hover:bg-accent/20 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          <ExternalLink size={12} />
          Open in Grafana
        </button>
      </div>

      {/* Daily summary */}
      <DailySummaryBanner nodes={nodes} loading={loading} />

      {/* Fullscreen overlay */}
      {fullscreenPanel !== null && (
        <div className="fixed inset-0 z-50 bg-void p-4 flex flex-col">
          <button
            onClick={() => setFullscreenPanel(null)}
            className="absolute top-4 right-4 z-10 p-1.5 rounded-lg bg-surface-overlay text-ink-muted hover:text-ink hover:bg-surface-raised transition-colors"
            aria-label="Close fullscreen"
          >
            <X size={18} />
          </button>
          <div className="flex-1 min-h-0">
            <SessionGraph
              nodes={nodes}
              edges={edges}
              selectedId={selectedId}
              onSelect={handleSelect}
              loading={loading}
              error={error}
              fixedMode={fullscreenPanel === 0 ? 'agent' : 'session'}
              title={fullscreenPanel === 0 ? 'Agents' : 'Sessions'}
            />
          </div>
        </div>
      )}

      {/* Graph panels — Agents + Sessions side by side on desktop, stacked on mobile */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="card p-4">
          <SessionGraph
            nodes={nodes}
            edges={edges}
            selectedId={selectedId}
            onSelect={handleSelect}
            loading={loading}
            error={error}
            fixedMode="agent"
            title="Agents"
            onFullscreen={() => setFullscreenPanel(0)}
          />
        </div>
        <div className="card p-4">
          <SessionGraph
            nodes={nodes}
            edges={edges}
            selectedId={selectedId}
            onSelect={handleSelect}
            loading={loading}
            error={error}
            fixedMode="session"
            title="Sessions"
            onFullscreen={() => setFullscreenPanel(1)}
          />
        </div>
      </div>

      {/* Session list (sorted by cost desc — gives Obsidian "file list" feel) */}
      {nodes.length > 0 && (
        <div className="card overflow-hidden">
          <div className="px-4 py-2.5 border-b border-edge text-xs text-ink-muted uppercase tracking-wide">
            Sessions — sorted by cost
          </div>
          <div className="divide-y divide-edge/50 max-h-52 overflow-y-auto">
            {[...nodes].sort((a, b) => b.totalCost - a.totalCost).map((node) => {
              const isSelected = node.sessionId === selectedId
              return (
                <button
                  key={node.sessionId}
                  onClick={() => handleSelect(node.sessionId)}
                  className={`w-full flex items-center gap-3 px-4 py-2.5 text-left transition-colors hover:bg-surface-overlay ${
                    isSelected ? 'bg-accent/8' : ''
                  }`}
                >
                  {/* Type indicator */}
                  <span className={`w-2 h-2 rounded-full flex-shrink-0 ${
                    node.hasError ? 'bg-signal-coral' :
                    !node.parentSessionId ? 'bg-accent' :
                    node.agentType === 'subagent' ? 'bg-signal-sky' : 'bg-signal-lime'
                  }`} />

                  {/* Session ID + task */}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="mono text-xs text-ink-muted truncate">
                        {node.sessionId.slice(0, 16)}{node.sessionId.length > 16 ? '…' : ''}
                      </span>
                      {node.taskLabel && (
                        <span className="text-xs text-accent truncate max-w-[180px]">
                          {node.taskLabel}
                        </span>
                      )}
                    </div>
                    <div className="text-[10px] text-ink-faint mt-0.5">
                      {node.agentId} · {node.callCount} call{node.callCount !== 1 ? 's' : ''}
                    </div>
                  </div>

                  {/* Cost */}
                  <span className={`text-xs mono flex-shrink-0 ${node.totalCost > 0 ? 'text-signal-lime' : 'text-ink-faint'}`}>
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
