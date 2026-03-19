import React, { useState, useMemo, useRef, useEffect } from 'react'
import { SessionNode, SessionEdge } from '../lib/queries'

interface LayoutNode extends SessionNode {
  x: number
  y: number
  depth: number
}

interface TooltipState {
  node: SessionNode
  x: number
  y: number
}

type GraphMode = 'agent' | 'session'

const NODE_RADIUS_BASE = 28
const NODE_RADIUS_SESSION = 32
const H_GAP_BASE = 100
const H_GAP_SESSION = 120
const V_GAP = 110
const PADDING = 60

function shortId(id: string, mode: GraphMode): string {
  if (mode === 'session') {
    return id.length > 14 ? id.slice(0, 13) + '…' : id
  }
  return id.length > 8 ? id.slice(0, 8) + '…' : id
}

function formatCost(cost: number): string {
  if (cost === 0) return '$0'
  if (cost < 0.01) return `$${cost.toFixed(4)}`
  return `$${cost.toFixed(2)}`
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms.toFixed(0)}ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
  return `${(ms / 60000).toFixed(1)}m`
}

function nodeColor(node: SessionNode): { fill: string; stroke: string; text: string } {
  if (node.hasError) return { fill: '#7f1d1d', stroke: '#ef4444', text: '#fca5a5' }
  if (node.agentType === 'subagent') return { fill: '#0c4a6e', stroke: '#38bdf8', text: '#bae6fd' }
  if (!node.parentSessionId) return { fill: '#1e1b4b', stroke: '#818cf8', text: '#c7d2fe' }
  return { fill: '#134e4a', stroke: '#2dd4bf', text: '#99f6e4' }
}

/** Compute a simple top-down tree layout. Returns nodes with x/y coordinates. */
function layoutTree(
  nodes: SessionNode[],
  edges: SessionEdge[],
  nodeRadius: number,
  hGap: number
): { layoutNodes: LayoutNode[]; width: number; height: number } {
  if (nodes.length === 0) return { layoutNodes: [], width: 0, height: 0 }

  // Build adjacency map (parent → children)
  const children = new Map<string, string[]>()
  const hasParent = new Set<string>()
  for (const e of edges) {
    if (!children.has(e.from)) children.set(e.from, [])
    children.get(e.from)!.push(e.to)
    hasParent.add(e.to)
  }

  // Root nodes (no parent in edge list, or unknown parent)
  const nodeMap = new Map(nodes.map((n) => [n.sessionId, n]))
  const roots = nodes.filter((n) => !hasParent.has(n.sessionId) || !nodeMap.has(n.parentSessionId))

  // Orphaned nodes (parent referenced but not in dataset) — treat as roots too
  const positionedIds = new Set<string>()
  const layoutNodes: LayoutNode[] = []

  let col = 0

  function subtreeWidth(id: string): number {
    const kids = children.get(id) ?? []
    if (kids.length === 0) return 1
    return kids.reduce((sum, kid) => sum + subtreeWidth(kid), 0)
  }

  function place(id: string, depth: number, leftCol: number): number {
    if (positionedIds.has(id)) return leftCol
    positionedIds.add(id)
    const node = nodeMap.get(id)
    if (!node) return leftCol

    const kids = children.get(id) ?? []
    const width = subtreeWidth(id)

    let childCol = leftCol
    for (const kid of kids) {
      childCol = place(kid, depth + 1, childCol)
    }

    // Center this node over its children
    const centerCol = kids.length > 0 ? leftCol + (childCol - leftCol - 1) / 2 : leftCol
    layoutNodes.push({ ...node, x: centerCol, y: depth, depth })
    return kids.length > 0 ? childCol : leftCol + 1
  }

  col = 0
  for (const root of roots) {
    col = place(root.sessionId, 0, col)
  }

  // Any nodes not yet placed (disconnected)
  for (const node of nodes) {
    if (!positionedIds.has(node.sessionId)) {
      layoutNodes.push({ ...node, x: col, y: 0, depth: 0 })
      col++
    }
  }

  const maxX = Math.max(...layoutNodes.map((n) => n.x), 0)
  const maxY = Math.max(...layoutNodes.map((n) => n.y), 0)

  const width = (maxX + 1) * (nodeRadius * 2 + hGap) + PADDING * 2
  const height = (maxY + 1) * (nodeRadius * 2 + V_GAP) + PADDING * 2

  // Convert grid positions to pixel positions
  const pixelNodes = layoutNodes.map((n) => ({
    ...n,
    x: PADDING + n.x * (nodeRadius * 2 + hGap) + nodeRadius,
    y: PADDING + n.y * (nodeRadius * 2 + V_GAP) + nodeRadius,
  }))

  return { layoutNodes: pixelNodes, width, height }
}

interface Props {
  nodes: SessionNode[]
  edges: SessionEdge[]
  selectedId: string | null
  onSelect: (sessionId: string) => void
  loading: boolean
  error: string | null
}

export function SessionGraph({ nodes, edges, selectedId, onSelect, loading, error }: Props) {
  const [tooltip, setTooltip] = useState<TooltipState | null>(null)
  const [mode, setMode] = useState<GraphMode>('agent')
  const svgRef = useRef<SVGSVGElement>(null)

  // Agent mode: aggregate session nodes by agentId
  const agentNodes = useMemo(() => {
    if (mode !== 'agent') return []
    const map = new Map<string, SessionNode>()
    for (const n of nodes) {
      const aid = n.agentId || 'unknown'
      const existing = map.get(aid)
      if (existing) {
        existing.callCount += n.callCount
        existing.totalCost += n.totalCost
      } else {
        map.set(aid, {
          sessionId: aid,
          agentId: aid,
          agentType: n.agentType,
          taskLabel: '',
          parentSessionId: '',
          callCount: n.callCount,
          totalCost: n.totalCost,
          tokensIn: n.tokensIn,
          tokensOut: n.tokensOut,
          firstSeen: n.firstSeen,
          lastSeen: n.lastSeen,
          durationMs: n.durationMs,
          hasError: n.hasError,
        })
      }
    }
    return Array.from(map.values())
  }, [nodes, mode])

  const agentEdges = useMemo(() => {
    if (mode !== 'agent') return []
    const sessionToAgent = new Map(nodes.map((n) => [n.sessionId, n.agentId]))
    const edgeSet = new Set<string>()
    for (const e of edges) {
      const fromAgent = sessionToAgent.get(e.from)
      const toAgent = sessionToAgent.get(e.to)
      if (fromAgent && toAgent && fromAgent !== toAgent) {
        edgeSet.add(`${fromAgent}||${toAgent}`)
      }
    }
    return Array.from(edgeSet).map((k) => {
      const [from, to] = k.split('||')
      return { from, to } as SessionEdge
    })
  }, [nodes, edges, mode])

  const displayNodes = mode === 'agent' ? agentNodes : nodes
  const displayEdges = mode === 'agent' ? agentEdges : edges

  const nodeRadius = mode === 'session' ? NODE_RADIUS_SESSION : NODE_RADIUS_BASE
  const hGap = mode === 'session' ? H_GAP_SESSION : H_GAP_BASE

  const { layoutNodes, width, height } = useMemo(
    () => layoutTree(displayNodes, displayEdges, nodeRadius, hGap),
    [displayNodes, displayEdges, nodeRadius, hGap]
  )

  const nodeMap = useMemo(
    () => new Map(layoutNodes.map((n) => [n.sessionId, n])),
    [layoutNodes]
  )

  useEffect(() => {
    const handler = () => setTooltip(null)
    window.addEventListener('click', handler)
    return () => window.removeEventListener('click', handler)
  }, [])

  if (loading) {
    return (
      <div className="flex items-center justify-center h-48 text-slate-500 text-sm">
        <svg className="animate-spin w-5 h-5 mr-2 text-indigo-400" fill="none" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z" />
        </svg>
        Loading session graph…
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-32 text-red-400 text-sm">
        ⚠ {error}
      </div>
    )
  }

  if (nodes.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-48 text-slate-500 text-sm gap-2">
        <svg className="w-10 h-10 text-slate-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
            d="M9 20l-5.447-2.724A1 1 0 013 16.382V5.618a1 1 0 011.447-.894L9 7m0 13l6-3m-6 3V7m6 10l4.553 2.276A1 1 0 0021 18.382V7.618a1 1 0 00-.553-.894L15 4m0 13V4m0 0L9 7" />
        </svg>
        <span>No session data yet.</span>
        <span className="text-xs text-slate-600">Sessions appear once agents route calls through the proxy with session IDs set.</span>
      </div>
    )
  }

  const svgWidth = Math.max(width, 400)
  const svgHeight = Math.max(height, 200)

  return (
    <div className="relative overflow-auto">
      {/* Mode toggle */}
      <div className="flex gap-1 mb-2 justify-end">
        <button
          onClick={() => setMode('agent')}
          className={`px-2 py-1 text-xs rounded ${mode === 'agent' ? 'bg-indigo-600 text-white' : 'bg-slate-700 text-slate-300 hover:bg-slate-600'}`}
        >Agents</button>
        <button
          onClick={() => setMode('session')}
          className={`px-2 py-1 text-xs rounded ${mode === 'session' ? 'bg-indigo-600 text-white' : 'bg-slate-700 text-slate-300 hover:bg-slate-600'}`}
        >Sessions</button>
      </div>

      <svg
        ref={svgRef}
        width={svgWidth}
        height={svgHeight}
        className="block"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Edges */}
        {displayEdges.map((edge) => {
          const from = nodeMap.get(edge.from)
          const to = nodeMap.get(edge.to)
          if (!from || !to) return null
          const midY = (from.y + to.y) / 2
          const path = `M ${from.x} ${from.y + nodeRadius} C ${from.x} ${midY}, ${to.x} ${midY}, ${to.x} ${to.y - nodeRadius}`
          return (
            <path
              key={`${edge.from}->${edge.to}`}
              d={path}
              fill="none"
              stroke="#334155"
              strokeWidth="2"
              strokeDasharray="4 3"
            />
          )
        })}

        {/* Nodes */}
        {layoutNodes.map((node) => {
          const colors = nodeColor(node)
          const isSelected = node.sessionId === selectedId
          const radius = nodeRadius + Math.min(node.callCount * 1.5, 12)

          return (
            <g
              key={node.sessionId}
              transform={`translate(${node.x},${node.y})`}
              style={{ cursor: 'pointer' }}
              onClick={(e) => {
                e.stopPropagation()
                onSelect(node.sessionId)
                setTooltip(null)
              }}
              onMouseEnter={(e) => {
                const svg = svgRef.current
                if (!svg) return
                const rect = svg.getBoundingClientRect()
                setTooltip({
                  node,
                  x: node.x - rect.left + (svg.parentElement?.scrollLeft ?? 0),
                  y: node.y - rect.top + (svg.parentElement?.scrollTop ?? 0),
                })
              }}
              onMouseLeave={() => setTooltip(null)}
            >
              {/* Glow ring when selected */}
              {isSelected && (
                <circle
                  r={radius + 6}
                  fill="none"
                  stroke={colors.stroke}
                  strokeWidth="2"
                  opacity="0.5"
                />
              )}
              {/* Main circle */}
              <circle
                r={radius}
                fill={colors.fill}
                stroke={colors.stroke}
                strokeWidth={isSelected ? 2.5 : 1.5}
              />
              {/* Call count */}
              <text
                textAnchor="middle"
                dominantBaseline="middle"
                fontSize="11"
                fontWeight="bold"
                fill={colors.text}
              >
                {node.callCount}
              </text>
              {/* Label below node — agent ID in agent mode, session ID in session mode */}
              <text
                y={radius + 14}
                textAnchor="middle"
                fontSize="9"
                fill="#94a3b8"
                fontFamily="monospace"
              >
                {mode === 'agent'
                  ? shortId(node.agentId, mode)
                  : shortId(node.sessionId, mode)}
              </text>
              {/* Cost label below ID */}
              {node.totalCost > 0 && (
                <text
                  y={radius + 25}
                  textAnchor="middle"
                  fontSize="9"
                  fill="#64748b"
                >
                  {formatCost(node.totalCost)}
                </text>
              )}
            </g>
          )
        })}
      </svg>

      {/* Tooltip */}
      {tooltip && (
        <div
          className="absolute z-20 pointer-events-none"
          style={{ left: tooltip.x + 16, top: tooltip.y - 10 }}
        >
          <div className="bg-[#0f0f1a] border border-slate-700 rounded-lg p-3 shadow-xl text-xs min-w-[200px] max-w-[280px]">
            <div className="font-semibold text-slate-200 mb-1 truncate font-mono">
              {mode === 'session' ? tooltip.node.sessionId : tooltip.node.agentId}
            </div>
            {mode === 'session' && tooltip.node.taskLabel && (
              <div className="text-indigo-300 mb-1 truncate">📋 {tooltip.node.taskLabel}</div>
            )}
            <div className="space-y-0.5 text-slate-400">
              {mode === 'session' && (
                <div>Agent: <span className="text-slate-300">{tooltip.node.agentId}</span></div>
              )}
              <div>Type: <span className="text-slate-300">{tooltip.node.agentType}</span></div>
              <div>Calls: <span className="text-slate-300">{tooltip.node.callCount}</span></div>
              <div>Cost: <span className="text-emerald-400">{formatCost(tooltip.node.totalCost)}</span></div>
              {tooltip.node.durationMs > 0 && (
                <div>Duration: <span className="text-slate-300">{formatDuration(tooltip.node.durationMs)}</span></div>
              )}
              {mode === 'session' && tooltip.node.parentSessionId && (
                <div className="truncate">Parent: <span className="text-sky-400 font-mono">{shortId(tooltip.node.parentSessionId, mode)}</span></div>
              )}
            </div>
            <div className="mt-2 text-slate-600 text-[10px]">Click to view details →</div>
          </div>
        </div>
      )}

      {/* Legend */}
      <div className="flex items-center gap-4 mt-3 px-2 text-xs text-slate-500 flex-wrap">
        <span className="flex items-center gap-1.5">
          <span className="w-3 h-3 rounded-full bg-indigo-900 border border-indigo-400 inline-block" />
          {mode === 'agent' ? 'Main agent' : 'Main session'}
        </span>
        <span className="flex items-center gap-1.5">
          <span className="w-3 h-3 rounded-full bg-sky-900 border border-sky-400 inline-block" />
          Sub-agent
        </span>
        <span className="flex items-center gap-1.5">
          <span className="w-3 h-3 rounded-full bg-teal-900 border border-teal-400 inline-block" />
          Child session
        </span>
        <span className="flex items-center gap-1.5">
          <span className="w-3 h-3 rounded-full bg-red-900 border border-red-400 inline-block" />
          Error
        </span>
        <span className="text-slate-600 ml-auto">Node size = call count · Click = details</span>
      </div>
    </div>
  )
}
