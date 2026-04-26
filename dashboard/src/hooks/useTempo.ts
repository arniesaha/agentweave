import { useState, useEffect, useCallback } from 'react'
import {
  TimeRange, getTimeRangeBounds, TempoSpan,
  SessionNode, SessionEdge, buildSessionGraph, tempoSessionGraphQuery,
  ReplayTurn, buildReplayTurns, tempoSessionReplayQuery,
} from '../lib/queries'

const TEMPO_BASE = '/tempo'

/**
 * Max traces returned by `useTempoSearch`. When `traces.length >= TEMPO_SEARCH_LIMIT`,
 * client-side aggregations (e.g. total cost) are sampling Tempo's most-recent traces
 * and may under-count. UI should surface this to the user.
 */
export const TEMPO_SEARCH_LIMIT = 1000

// Stale-while-revalidate contract for all hooks in this file:
//   loading    — true only during the initial fetch (no data yet).
//   refetching — true while a background refetch is in flight.
//   On error, last-good data is preserved; only `error` is updated.
// See #175 for the rationale.

interface UseTempoSearchResult {
  traces: TempoSpan[]
  loading: boolean
  refetching: boolean
  error: string | null
}

export function useTempoSearch(query: string, timeRange: TimeRange, refreshKey: number): UseTempoSearchResult {
  const [traces, setTraces] = useState<TempoSpan[]>([])
  const [loading, setLoading] = useState(true)
  const [refetching, setRefetching] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const fetch_ = useCallback(async () => {
    setRefetching(true)
    try {
      const { start, end } = getTimeRangeBounds(timeRange)
      const params = new URLSearchParams({
        q: query,
        limit: String(TEMPO_SEARCH_LIMIT),
        start: String(start),
        end: String(end),
      })
      const resp = await fetch(`${TEMPO_BASE}/api/search?${params}`)
      if (!resp.ok) throw new Error(`Tempo search failed: ${resp.status}`)
      const data = await resp.json()
      setTraces(data.traces ?? [])
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Tempo unavailable')
    } finally {
      setLoading(false)
      setRefetching(false)
    }
  }, [query, timeRange, refreshKey]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { fetch_() }, [fetch_])

  return { traces, loading, refetching, error }
}

interface UseTempoSearchCountResult {
  count: number | null
  loading: boolean
  refetching: boolean
  error: string | null
}

export function useTempoSearchCount(query: string, timeRange: TimeRange, refreshKey: number): UseTempoSearchCountResult {
  const [count, setCount] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [refetching, setRefetching] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const fetch_ = useCallback(async () => {
    setRefetching(true)
    try {
      const { start, end } = getTimeRangeBounds(timeRange)
      const params = new URLSearchParams({
        q: query,
        limit: '10000',
        start: String(start),
        end: String(end),
      })
      const resp = await fetch(`${TEMPO_BASE}/api/search?${params}`)
      if (!resp.ok) throw new Error(`Tempo search count failed: ${resp.status}`)
      const data = await resp.json()
      setCount((data.traces ?? []).length)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Tempo unavailable')
    } finally {
      setLoading(false)
      setRefetching(false)
    }
  }, [query, timeRange, refreshKey]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { fetch_() }, [fetch_])

  return { count, loading, refetching, error }
}

// ─── Session Graph Hook ──────────────────────────────────────────────────────

interface UseSessionGraphResult {
  nodes: SessionNode[]
  edges: SessionEdge[]
  rawTraces: TempoSpan[]
  loading: boolean
  refetching: boolean
  error: string | null
}

export function useSessionGraph(
  timeRange: TimeRange,
  refreshKey: number
): UseSessionGraphResult {
  const [nodes, setNodes] = useState<SessionNode[]>([])
  const [edges, setEdges] = useState<SessionEdge[]>([])
  const [rawTraces, setRawTraces] = useState<TempoSpan[]>([])
  const [loading, setLoading] = useState(true)
  const [refetching, setRefetching] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const fetch_ = useCallback(async () => {
    setRefetching(true)
    try {
      const { start, end } = getTimeRangeBounds(timeRange)
      const params = new URLSearchParams({
        q: tempoSessionGraphQuery(),
        limit: '2000',
        start: String(start),
        end: String(end),
      })
      const resp = await fetch(`${TEMPO_BASE}/api/search?${params}`)
      if (!resp.ok) throw new Error(`Tempo session graph failed: ${resp.status}`)
      const data = await resp.json()
      const traces: TempoSpan[] = data.traces ?? []
      setRawTraces(traces)
      const { nodes: n, edges: e } = buildSessionGraph(traces)
      setNodes(n)
      setEdges(e)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Tempo unavailable')
    } finally {
      setLoading(false)
      setRefetching(false)
    }
  }, [timeRange, refreshKey]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { fetch_() }, [fetch_])

  return { nodes, edges, rawTraces, loading, refetching, error }
}

// ─── Session Replay Hook ──────────────────────────────────────────────────────

interface UseSessionReplayResult {
  turns: ReplayTurn[]
  rawTraces: TempoSpan[]
  loading: boolean
  refetching: boolean
  error: string | null
}

export function useSessionReplay(sessionId: string, timeRange: TimeRange, refreshKey: number): UseSessionReplayResult {
  const [turns, setTurns] = useState<ReplayTurn[]>([])
  const [rawTraces, setRawTraces] = useState<TempoSpan[]>([])
  const [loading, setLoading] = useState(false)
  const [refetching, setRefetching] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const fetch_ = useCallback(async () => {
    if (!sessionId.trim()) {
      setTurns([])
      setRawTraces([])
      setLoading(false)
      setRefetching(false)
      return
    }
    setRefetching(true)
    try {
      const { start, end } = getTimeRangeBounds(timeRange)
      const query = tempoSessionReplayQuery(sessionId)
      const params = new URLSearchParams({
        q: query,
        limit: '2000',
        start: String(start),
        end: String(end),
      })
      const resp = await fetch(`${TEMPO_BASE}/api/search?${params}`)
      if (!resp.ok) throw new Error(`Tempo replay search failed: ${resp.status}`)
      const data = await resp.json()
      const traces: TempoSpan[] = data.traces ?? []
      setRawTraces(traces)
      setTurns(buildReplayTurns(traces, sessionId))
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Tempo unavailable')
    } finally {
      setLoading(false)
      setRefetching(false)
    }
  }, [sessionId, timeRange, refreshKey]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { fetch_() }, [fetch_])

  return { turns, rawTraces, loading, refetching, error }
}
