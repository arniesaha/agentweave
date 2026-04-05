import React, { useState, useMemo, useRef, useEffect } from 'react'
import { Maximize2, RotateCcw } from 'lucide-react'
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

type NodePositions = Record<string, { x: number; y: number }>

interface DragState {
  id: string
  moved: boolean
  pointerOffsetX: number
  pointerOffsetY: number
}

const NODE_RADIUS_BASE = 28
const NODE_RADIUS_SESSION = 30
const H_GAP_BASE = 100
const H_GAP_SESSION = 90   // tighter horizontal — keeps deep trees narrower
const V_GAP = 100
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
  if (node.hasError) return { fill: '#2A1215', stroke: '#FF6B6B', text: '#FF6B6B' }
  if (node.agentType === 'subagent') return { fill: '#0D1B2A', stroke: '#5BA4F5', text: '#5BA4F5' }
  if (!node.parentSessionId) return { fill: '#0A1A1A', stroke: '#00E5CC', text: '#00E5CC' }
  return { fill: '#0D1F0D', stroke: '#7DDB80', text: '#7DDB80' }
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

  // Guard against cycles (e.g. nix-v1 → max-v1 → nix-v1 in agent mode)
  const visitStack = new Set<string>()

  function subtreeWidth(id: string, depth = 0): number {
    if (depth > 20 || visitStack.has(id)) return 1   // cycle / depth guard
    visitStack.add(id)
    const kids = children.get(id) ?? []
    const w = kids.length === 0 ? 1 : kids.reduce((sum, kid) => sum + subtreeWidth(kid, depth + 1), 0)
    visitStack.delete(id)
    return w
  }

  function place(id: string, depth: number, leftCol: number): number {
    if (positionedIds.has(id)) return leftCol  // already placed — cycle guard
    positionedIds.add(id)
    const node = nodeMap.get(id)
    if (!node) return leftCol

    const kids = (children.get(id) ?? []).filter((k) => !positionedIds.has(k))  // skip already-placed

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

  // Slightly wider spacing for readability in screenshots.
  const effectiveGap = hGap + 15
  const width = (maxX + 1) * (nodeRadius * 2 + effectiveGap) + PADDING * 2
  const height = (maxY + 1) * (nodeRadius * 2 + V_GAP) + PADDING * 2

  // Convert grid positions to pixel positions
  const pixelNodes = layoutNodes.map((n) => ({
    ...n,
    x: PADDING + n.x * (nodeRadius * 2 + effectiveGap) + nodeRadius,
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
  fixedMode?: GraphMode  // if set, locks mode and hides the toggle
  title?: string         // optional label shown above graph
  onFullscreen?: () => void  // if provided, shows expand button in header
}

export function SessionGraph({ nodes, edges, selectedId, onSelect, loading, error, fixedMode, title, onFullscreen }: Props) {
  const [tooltip, setTooltip] = useState<TooltipState | null>(null)
  const [mode, setMode] = useState<GraphMode>(fixedMode ?? 'agent')
  const svgRef = useRef<SVGSVGElement>(null)

  const [nodePositions, setNodePositions] = useState<NodePositions>({})
  const [dragState, setDragState] = useState<DragState | null>(null)

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
          project: n.project,
          piiDetected: n.piiDetected,
          piiKinds: n.piiKinds,
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

  const rawDisplayNodes = mode === 'agent' ? agentNodes : nodes
  const rawDisplayEdges = mode === 'agent' ? agentEdges : edges

  // In session mode: drop orphaned leaf nodes (old demo sessions whose parent isn't
  // in the current dataset and who have no children). Keeps the graph clean.
  const { displayNodes, displayEdges } = useMemo(() => {
    if (mode !== 'session') return { displayNodes: rawDisplayNodes, displayEdges: rawDisplayEdges }
    const sessionSet = new Set(rawDisplayNodes.map((n) => n.sessionId))
    const hasChildren = new Set(rawDisplayEdges.map((e) => e.from))
    const filtered = rawDisplayNodes.filter((n) => {
      if (!n.parentSessionId) return true                 // true root (nix-main, max-main)
      if (sessionSet.has(n.parentSessionId)) return true  // parent is in dataset → connected
      if (hasChildren.has(n.sessionId)) return true       // has children → keep as sub-root
      return false                                         // orphaned leaf → drop
    })
    const keptIds = new Set(filtered.map((n) => n.sessionId))
    const filteredEdges = rawDisplayEdges.filter((e) => keptIds.has(e.from) && keptIds.has(e.to))
    return { displayNodes: filtered, displayEdges: filteredEdges }
  }, [rawDisplayNodes, rawDisplayEdges, mode])

  const nodeRadius = mode === 'session' ? NODE_RADIUS_SESSION : NODE_RADIUS_BASE
  const hGap = mode === 'session' ? H_GAP_SESSION : H_GAP_BASE

  const { layoutNodes, width, height } = useMemo(
    () => layoutTree(displayNodes, displayEdges, nodeRadius, hGap),
    [displayNodes, displayEdges, nodeRadius, hGap]
  )

  const renderedNodes = useMemo(
    () => layoutNodes.map((n) => {
      const overridden = nodePositions[n.sessionId]
      return overridden ? { ...n, x: overridden.x, y: overridden.y } : n
    }),
    [layoutNodes, nodePositions]
  )

  const nodeMap = useMemo(
    () => new Map(renderedNodes.map((n) => [n.sessionId, n])),
    [renderedNodes]
  )

  const positionedIdsKey = useMemo(
    () => renderedNodes.map((n) => n.sessionId).sort().join('|'),
    [renderedNodes]
  )

  useEffect(() => {
    // Clean stale drag overrides if the visible graph changed.
    setNodePositions((prev) => {
      const valid = new Set(renderedNodes.map((n) => n.sessionId))
      const next: NodePositions = {}
      for (const [id, pos] of Object.entries(prev)) {
        if (valid.has(id)) next[id] = pos
      }
      return Object.keys(next).length === Object.keys(prev).length ? prev : next
    })
  }, [positionedIdsKey, renderedNodes])

  useEffect(() => {
    const handler = () => setTooltip(null)
    window.addEventListener('click', handler)
    return () => window.removeEventListener('click', handler)
  }, [])

  useEffect(() => {
    if (!dragState) return

    const getPointerPositionInSvg = (clientX: number, clientY: number) => {
      const svg = svgRef.current
      if (!svg) return null
      const rect = svg.getBoundingClientRect()
      const container = svg.parentElement
      const scrollLeft = container?.scrollLeft ?? 0
      const scrollTop = container?.scrollTop ?? 0
      return {
        x: clientX - rect.left + scrollLeft,
        y: clientY - rect.top + scrollTop,
      }
    }

    const handleMove = (e: MouseEvent) => {
      const pointer = getPointerPositionInSvg(e.clientX, e.clientY)
      if (!pointer) return

      setNodePositions((prev) => ({
        ...prev,
        [dragState.id]: {
          x: pointer.x - dragState.pointerOffsetX,
          y: pointer.y - dragState.pointerOffsetY,
        },
      }))
      setDragState((prev) => (prev ? { ...prev, moved: true } : prev))
      setTooltip(null)
    }

    const handleUp = () => {
      setDragState(null)
    }

    window.addEventListener('mousemove', handleMove)
    window.addEventListener('mouseup', handleUp)
    return () => {
      window.removeEventListener('mousemove', handleMove)
      window.removeEventListener('mouseup', handleUp)
    }
  }, [dragState])

  if (loading) {
    return (
      <div className="flex items-center justify-center h-48 text-ink-muted text-sm">
        <svg className="animate-spin w-5 h-5 mr-2 text-accent" fill="none" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z" />
        </svg>
        Loading session graph…
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-32 text-signal-coral text-sm">
        ⚠ {error}
      </div>
    )
  }

  if (nodes.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-48 text-ink-muted text-sm gap-2">
        <svg className="w-10 h-10 text-ink-faint" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
            d="M9 20l-5.447-2.724A1 1 0 013 16.382V5.618a1 1 0 011.447-.894L9 7m0 13l6-3m-6 3V7m6 10l4.553 2.276A1 1 0 0021 18.382V7.618a1 1 0 00-.553-.894L15 4m0 13V4m0 0L9 7" />
        </svg>
        <span>No session data yet.</span>
        <span className="text-xs text-ink-faint">Sessions appear once agents route calls through the proxy with session IDs set.</span>
      </div>
    )
  }

  const svgWidth = Math.max(width, 400)
  const svgHeight = Math.max(height, 200)

  return (
    <div className="relative overflow-auto">
      {/* Header: title (fixed mode) or toggle */}
      <div className="flex items-center justify-between mb-2 flex-wrap gap-2">
        {/* Legend */}
        <div className="flex items-center gap-3 text-xs text-ink-muted">
          {title && <span className="text-ink font-medium text-sm mr-1">{title}</span>}
          {onFullscreen && (
            <button
              onClick={onFullscreen}
              className="p-1 rounded-md bg-surface-overlay text-ink-muted hover:bg-surface-raised hover:text-ink transition-colors"
              aria-label="Expand to fullscreen"
              title="Expand"
            >
              <Maximize2 size={13} />
            </button>
          )}
          <button
            onClick={() => setNodePositions({})}
            className="p-1 rounded-md bg-surface-overlay text-ink-muted hover:bg-surface-raised hover:text-ink transition-colors"
            aria-label="Reset manual node positions"
            title="Reset layout"
          >
            <RotateCcw size={13} />
          </button>
          <span className="text-[10px] text-ink-faint">drag nodes to explore causality paths</span>
          <span className="flex items-center gap-1.5">
            <svg width="28" height="10"><line x1="0" y1="5" x2="22" y2="5" stroke="#00E5CC" strokeWidth="1.5" strokeDasharray="6 4" opacity="0.6"/><polygon points="22,2 22,8 28,5" fill="#00E5CC" opacity="0.7"/></svg>
            <span>delegates to</span>
          </span>
          <span className="flex items-center gap-1.5">
            <svg width="28" height="10"><line x1="0" y1="5" x2="22" y2="5" stroke="#FFBF47" strokeWidth="1.5" strokeDasharray="5 3" opacity="0.7"/><polygon points="22,2 22,8 28,5" fill="#FFBF47" opacity="0.8"/></svg>
            <span>callback</span>
          </span>
        </div>
        {/* Toggle only shown when not in fixed mode */}
        {!fixedMode && (
          <div className="flex gap-1">
            <button
              onClick={() => setMode('agent')}
              className={`px-2 py-1 text-xs rounded ${mode === 'agent' ? 'bg-accent/12 text-accent border border-accent/25' : 'bg-surface-overlay text-ink-muted hover:bg-surface-raised'}`}
            >Agents</button>
            <button
              onClick={() => setMode('session')}
              className={`px-2 py-1 text-xs rounded ${mode === 'session' ? 'bg-accent/12 text-accent border border-accent/25' : 'bg-surface-overlay text-ink-muted hover:bg-surface-raised'}`}
            >Sessions</button>
          </div>
        )}
      </div>

      <svg
        ref={svgRef}
        width={svgWidth}
        height={svgHeight}
        className="block"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Forward edges — teal dashed line with arrowhead */}
        {displayEdges.map((edge) => {
          const from = nodeMap.get(edge.from)
          const to = nodeMap.get(edge.to)
          if (!from || !to) return null
          if (to.depth <= from.depth) return null
          const fromR = nodeRadius + Math.min(from.callCount * 1.5, 12)
          const toR   = nodeRadius + Math.min(to.callCount   * 1.5, 12)
          const midY = (from.y + to.y) / 2
          const ex = to.x
          const ey = to.y - toR - 2
          const path = `M ${from.x} ${from.y + fromR} C ${from.x} ${midY}, ${ex} ${midY}, ${ex} ${ey}`
          // Position label at 60% along the edge (closer to target), offset left to avoid node overlap
          const labelX = from.x + (ex - from.x) * 0.3 - 10
          const labelY = from.y + fromR + (ey - from.y - fromR) * 0.45
          return (
            <g key={`fwd-${edge.from}->${edge.to}`}>
              <path d={path} fill="none" stroke="#00E5CC" strokeWidth="1.8" strokeDasharray="6 4" opacity="0.65" />
              <polygon
                points={`${ex - 4},${ey - 7} ${ex + 4},${ey - 7} ${ex},${ey}`}
                fill="#00E5CC" opacity="0.7" />
              {edge.taskLabel && (
                <text x={labelX} y={labelY} textAnchor="end" fontSize="7.5" fill="#00E5CC" opacity="0.7"
                  fontFamily="'DM Sans', system-ui">
                  {edge.taskLabel.length > 22 ? edge.taskLabel.slice(0, 21) + '...' : edge.taskLabel}
                </text>
              )}
            </g>
          )
        })}

        {/* Back-edges — amber curved arrows looping to the right */}
        {displayEdges.map((edge) => {
          const from = nodeMap.get(edge.from)
          const to = nodeMap.get(edge.to)
          if (!from || !to) return null
          if (to.depth > from.depth) return null
          const fromR = nodeRadius + Math.min(from.callCount * 1.5, 12)
          const toR   = nodeRadius + Math.min(to.callCount   * 1.5, 12)
          const rightEdge = svgWidth - 28
          const fx = from.x + fromR
          const fy = from.y
          const tx = to.x + toR + 10
          const ty = to.y
          const path = `M ${fx} ${fy} C ${rightEdge} ${fy}, ${rightEdge} ${ty}, ${tx} ${ty}`
          const labelX = rightEdge - 20
          const labelY = (fy + ty) / 2
          return (
            <g key={`back-${edge.from}->${edge.to}`}>
              <path d={path} fill="none" stroke="#FFBF47" strokeWidth="1.8" strokeDasharray="5 3" opacity="0.72" />
              <polygon
                points={`${tx + 8},${ty - 4} ${tx + 8},${ty + 4} ${tx},${ty}`}
                fill="#FFBF47" opacity="0.8" />
              {edge.taskLabel && (
                <text x={labelX} y={labelY} textAnchor="end" fontSize="8" fill="#FFBF47" opacity="0.7"
                  fontFamily="'DM Sans', system-ui">
                  {edge.taskLabel.length > 24 ? edge.taskLabel.slice(0, 23) + '...' : edge.taskLabel}
                </text>
              )}
            </g>
          )
        })}

        {/* Nodes */}
        {renderedNodes.map((node) => {
          const colors = nodeColor(node)
          const isSelected = node.sessionId === selectedId
          const radius = nodeRadius + Math.min(node.callCount * 1.5, 12)

          return (
            <g
              key={node.sessionId}
              transform={`translate(${node.x},${node.y})`}
              style={{ cursor: dragState?.id === node.sessionId ? 'grabbing' : 'grab' }}
              onMouseDown={(e) => {
                e.stopPropagation()
                const svg = svgRef.current
                if (!svg) return
                const rect = svg.getBoundingClientRect()
                const scrollLeft = svg.parentElement?.scrollLeft ?? 0
                const scrollTop = svg.parentElement?.scrollTop ?? 0
                const pointerX = e.clientX - rect.left + scrollLeft
                const pointerY = e.clientY - rect.top + scrollTop
                setDragState({
                  id: node.sessionId,
                  moved: false,
                  pointerOffsetX: pointerX - node.x,
                  pointerOffsetY: pointerY - node.y,
                })
              }}
              onClick={(e) => {
                e.stopPropagation()
                if (!dragState?.moved) {
                  onSelect(node.sessionId)
                }
                setTooltip(null)
              }}
              onMouseEnter={(e) => {
                if (dragState) return
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
                fill="#8892A6"
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
                  fill="#4A5568"
                >
                  {formatCost(node.totalCost)}
                </text>
              )}
              {/* Project badge (issue #101) */}
              {node.project && (
                <g transform={`translate(0, ${radius + (node.totalCost > 0 ? 37 : 27)})`}>
                  <rect
                    x={-(node.project.length * 3 + 6)}
                    y={-7}
                    width={node.project.length * 6 + 12}
                    height={14}
                    rx={7}
                    fill="#00E5CC"
                    opacity={0.15}
                  />
                  <text
                    textAnchor="middle"
                    dominantBaseline="middle"
                    fontSize="8"
                    fill="#00E5CC"
                    fontFamily="monospace"
                  >
                    {node.project}
                  </text>
                </g>
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
          <div className="bg-surface border border-edge rounded-lg p-3 shadow-xl text-xs min-w-[200px] max-w-[280px]">
            <div className="font-semibold text-ink mb-1 truncate mono">
              {mode === 'session' ? tooltip.node.sessionId : tooltip.node.agentId}
            </div>
            {mode === 'session' && tooltip.node.taskLabel && (
              <div className="text-accent mb-1 truncate">📋 {tooltip.node.taskLabel}</div>
            )}
            <div className="space-y-0.5 text-ink-muted">
              {mode === 'session' && (
                <div>Agent: <span className="text-ink">{tooltip.node.agentId}</span></div>
              )}
              <div>Type: <span className="text-ink">{tooltip.node.agentType}</span></div>
              <div>Calls: <span className="text-ink">{tooltip.node.callCount}</span></div>
              <div>Cost: <span className="text-signal-lime">{formatCost(tooltip.node.totalCost)}</span></div>
              {tooltip.node.durationMs > 0 && (
                <div>Duration: <span className="text-ink">{formatDuration(tooltip.node.durationMs)}</span></div>
              )}
              {mode === 'session' && tooltip.node.parentSessionId && (
                <div className="truncate">Parent: <span className="text-signal-sky mono">{shortId(tooltip.node.parentSessionId, mode)}</span></div>
              )}
              {tooltip.node.piiDetected && (
                <div className="mt-1 text-signal-amber">⚠️ PII detected{tooltip.node.piiKinds ? `: ${tooltip.node.piiKinds}` : ''}</div>
              )}
            </div>
            <div className="mt-2 text-ink-faint text-[10px]">Click to view details →</div>
          </div>
        </div>
      )}

      {/* Legend */}
      <div className="flex items-center gap-4 mt-3 px-2 text-xs text-ink-muted flex-wrap">
        <span className="flex items-center gap-1.5">
          <span className="w-3 h-3 rounded-full bg-[#0A1A1A] border border-[#00E5CC] inline-block" />
          {mode === 'agent' ? 'Main agent' : 'Main session'}
        </span>
        <span className="flex items-center gap-1.5">
          <span className="w-3 h-3 rounded-full bg-[#0D1B2A] border border-[#5BA4F5] inline-block" />
          Sub-agent
        </span>
        <span className="flex items-center gap-1.5">
          <span className="w-3 h-3 rounded-full bg-[#0D1F0D] border border-[#7DDB80] inline-block" />
          Child session
        </span>
        <span className="flex items-center gap-1.5">
          <span className="w-3 h-3 rounded-full bg-[#2A1215] border border-[#FF6B6B] inline-block" />
          Error
        </span>
        <span className="text-ink-faint ml-auto">Node size = call count · Drag = reposition · Click = details</span>
      </div>
    </div>
  )
}
