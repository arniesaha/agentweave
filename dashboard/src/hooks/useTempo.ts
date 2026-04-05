import { useState, useEffect, useCallback } from 'react'
import {
  TimeRange, getTimeRangeBounds, getStepForRange, TempoSpan, TempoMetricResult,
  SessionNode, SessionEdge, buildSessionGraph, tempoSessionGraphQuery,
  ReplayTurn, buildReplayTurns, tempoSessionReplayQuery,
} from '../lib/queries'

const TEMPO_BASE = '/tempo'
const TEMPO_METRICS_MAX_WINDOW_SECONDS = 3 * 60 * 60

interface UseTempoSearchResult {
  traces: TempoSpan[]
  loading: boolean
  error: string | null
}

export function useTempoSearch(query: string, timeRange: TimeRange, refreshKey: number): UseTempoSearchResult {
  const [traces, setTraces] = useState<TempoSpan[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const fetch_ = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const { start, end } = getTimeRangeBounds(timeRange)
      const params = new URLSearchParams({
        q: query,
        limit: '1000',
        start: String(start),
        end: String(end),
      })
      const resp = await fetch(`${TEMPO_BASE}/api/search?${params}`)
      if (!resp.ok) throw new Error(`Tempo search failed: ${resp.status}`)
      const data = await resp.json()
      setTraces(data.traces ?? [])
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Tempo unavailable')
      setTraces([])
    } finally {
      setLoading(false)
    }
  }, [query, timeRange, refreshKey]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { fetch_() }, [fetch_])

  return { traces, loading, error }
}

interface UseTempoSearchCountResult {
  count: number | null
  loading: boolean
  error: string | null
}

export function useTempoSearchCount(query: string, timeRange: TimeRange, refreshKey: number): UseTempoSearchCountResult {
  const [count, setCount] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const fetch_ = useCallback(async () => {
    setLoading(true)
    setError(null)
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
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Tempo unavailable')
      setCount(null)
    } finally {
      setLoading(false)
    }
  }, [query, timeRange, refreshKey]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { fetch_() }, [fetch_])

  return { count, loading, error }
}

interface UseTempoMetricsResult {
  result: TempoMetricResult | null
  loading: boolean
  error: string | null
}

export function useTempoMetrics(
  query: string,
  timeRange: TimeRange,
  refreshKey: number
): UseTempoMetricsResult {
  const [result, setResult] = useState<TempoMetricResult | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const fetch_ = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const { start, end } = getTimeRangeBounds(timeRange)
      const step = getStepForRange(timeRange)

      const mergedSeries: Array<{
        labels?: Record<string, string> | Array<{ key: string; value: { stringValue?: string } }>
        samples: Array<{ timestamp_ms?: number; timestampMs?: number | string; value: string | number }>
      }> = []

      for (
        let chunkStart = start;
        chunkStart <= end;
        chunkStart += TEMPO_METRICS_MAX_WINDOW_SECONDS + step
      ) {
        const chunkEnd = Math.min(chunkStart + TEMPO_METRICS_MAX_WINDOW_SECONDS, end)
        const params = new URLSearchParams({
          q: query,
          start: String(chunkStart),
          end: String(chunkEnd),
          step: `${step}s`,
        })
        const resp = await fetch(`${TEMPO_BASE}/api/metrics/query_range?${params}`)
        if (!resp.ok) {
          const details = await resp.text().catch(() => '')
          throw new Error(`Tempo metrics failed: ${resp.status}${details ? ` — ${details}` : ''}`)
        }
        const data = await resp.json()
        if (Array.isArray(data?.series)) {
          mergedSeries.push(...data.series)
        }
      }

      setResult({ series: mergedSeries })
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Tempo unavailable')
      setResult(null)
    } finally {
      setLoading(false)
    }
  }, [query, timeRange, refreshKey]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { fetch_() }, [fetch_])

  return { result, loading, error }
}

// ─── Session Graph Hook ──────────────────────────────────────────────────────

interface UseSessionGraphResult {
  nodes: SessionNode[]
  edges: SessionEdge[]
  rawTraces: TempoSpan[]
  loading: boolean
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
  const [error, setError] = useState<string | null>(null)

  const fetch_ = useCallback(async () => {
    setLoading(true)
    setError(null)
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
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Tempo unavailable')
      setNodes([])
      setEdges([])
      setRawTraces([])
    } finally {
      setLoading(false)
    }
  }, [timeRange, refreshKey]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { fetch_() }, [fetch_])

  return { nodes, edges, rawTraces, loading, error }
}

// ─── Session Replay Hook ──────────────────────────────────────────────────────

interface UseSessionReplayResult {
  turns: ReplayTurn[]
  rawTraces: TempoSpan[]
  loading: boolean
  error: string | null
}

export function useSessionReplay(sessionId: string, timeRange: TimeRange, refreshKey: number): UseSessionReplayResult {
  const [turns, setTurns] = useState<ReplayTurn[]>([])
  const [rawTraces, setRawTraces] = useState<TempoSpan[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const fetch_ = useCallback(async () => {
    if (!sessionId.trim()) {
      setTurns([])
      setRawTraces([])
      setLoading(false)
      return
    }
    setLoading(true)
    setError(null)
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
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Tempo unavailable')
      setTurns([])
      setRawTraces([])
    } finally {
      setLoading(false)
    }
  }, [sessionId, timeRange, refreshKey]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { fetch_() }, [fetch_])

  return { turns, rawTraces, loading, error }
}
