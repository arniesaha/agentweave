import { useState, useEffect, useCallback } from 'react'
import { TimeRange, getTimeRangeBounds, getStepForRange, TempoSpan, TempoMetricResult } from '../lib/queries'

const TEMPO_BASE = '/tempo'

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
      const params = new URLSearchParams({
        q: query,
        start: String(start),
        end: String(end),
        step: `${step}s`,
      })
      const resp = await fetch(`${TEMPO_BASE}/api/metrics/query_range?${params}`)
      if (!resp.ok) throw new Error(`Tempo metrics failed: ${resp.status}`)
      const data = await resp.json()
      setResult(data)
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
