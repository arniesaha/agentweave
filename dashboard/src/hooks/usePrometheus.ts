import { useState, useEffect, useCallback } from 'react'
import {
  TimeRange,
  getTimeRangeBounds,
  getStepForRange,
  PrometheusResponse,
  transformPrometheusMatrix,
  transformPrometheusVector,
} from '../lib/queries'

const PROM_BASE = '/prometheus/api/v1'

interface UsePromQueryRangeResult {
  series: Array<{ label: string; points: Array<{ time: number; value: number }> }>
  loading: boolean
  error: string | null
}

export function usePromQueryRange(
  query: string,
  timeRange: TimeRange,
  refreshKey: number,
  labelKey?: string
): UsePromQueryRangeResult {
  const [series, setSeries] = useState<Array<{ label: string; points: Array<{ time: number; value: number }> }>>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const fetch_ = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const { start, end } = getTimeRangeBounds(timeRange)
      const step = getStepForRange(timeRange)
      const params = new URLSearchParams({
        query,
        start: String(start),
        end: String(end),
        step: String(step),
      })
      const resp = await fetch(`${PROM_BASE}/query_range?${params}`)
      if (!resp.ok) throw new Error(`Prometheus query failed: ${resp.status}`)
      const data: PrometheusResponse = await resp.json()
      setSeries(transformPrometheusMatrix(data, labelKey))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Prometheus unavailable')
      setSeries([])
    } finally {
      setLoading(false)
    }
  }, [query, timeRange, refreshKey, labelKey]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { fetch_() }, [fetch_])

  return { series, loading, error }
}

interface UsePromQueryInstantResult {
  bars: Array<{ label: string; value: number }>
  loading: boolean
  error: string | null
}

export function usePromQueryInstant(
  query: string,
  timeRange: TimeRange,
  refreshKey: number,
  labelKey: string
): UsePromQueryInstantResult {
  const [bars, setBars] = useState<Array<{ label: string; value: number }>>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const fetch_ = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      // Use query_range and take the last value for instant-like behavior
      const { start, end } = getTimeRangeBounds(timeRange)
      const step = getStepForRange(timeRange)
      const params = new URLSearchParams({
        query,
        start: String(start),
        end: String(end),
        step: String(step),
      })
      const resp = await fetch(`${PROM_BASE}/query_range?${params}`)
      if (!resp.ok) throw new Error(`Prometheus query failed: ${resp.status}`)
      const data: PrometheusResponse = await resp.json()

      // Extract last value from each series for bar chart
      const matrix = data.data?.result as Array<{ metric: Record<string, string>; values: [number, string][] }>
      if (!matrix?.length) {
        setBars([])
        return
      }
      const result = matrix
        .map((r) => ({
          label: r.metric[labelKey] ?? 'unknown',
          value: r.values.length ? (parseFloat(r.values[r.values.length - 1][1]) || 0) : 0,
        }))
        .filter((r) => r.value > 0)
        .sort((a, b) => b.value - a.value)
        .slice(0, 10)
      setBars(result)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Prometheus unavailable')
      setBars([])
    } finally {
      setLoading(false)
    }
  }, [query, timeRange, refreshKey, labelKey]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { fetch_() }, [fetch_])

  return { bars, loading, error }
}

// Aggregated total from a query_range (sum of latest values)
interface UsePromScalarResult {
  value: number | null
  loading: boolean
  error: string | null
}

export function usePromScalar(
  query: string,
  timeRange: TimeRange,
  refreshKey: number
): UsePromScalarResult {
  const [value, setValue] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const fetch_ = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const { end } = getTimeRangeBounds(timeRange)
      const params = new URLSearchParams({ query, time: String(end) })
      const resp = await fetch(`${PROM_BASE}/query?${params}`)
      if (!resp.ok) throw new Error(`Prometheus instant query failed: ${resp.status}`)
      const data: PrometheusResponse = await resp.json()
      if (data.status !== 'success') throw new Error('Prometheus query not successful')
      const results = transformPrometheusVector(data, '__name__')
      const total = results.reduce((s, r) => s + r.value, 0)
      setValue(total)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Prometheus unavailable')
      setValue(null)
    } finally {
      setLoading(false)
    }
  }, [query, timeRange, refreshKey]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { fetch_() }, [fetch_])

  return { value, loading, error }
}
