import React from 'react'
import {
  AreaChart,
  Area,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from 'recharts'
import { format } from 'date-fns'

interface TimeSeriesPoint {
  time: number
  value: number
}

interface Series {
  label: string
  points: TimeSeriesPoint[]
}

interface TimeSeriesChartProps {
  title: string
  subtitle?: string
  series: Series[]
  loading: boolean
  error?: string | null
  type?: 'line' | 'area'
  valueFormatter?: (v: number) => string
  yAxisLabel?: string
}

const COLORS = [
  '#6366f1', '#8b5cf6', '#06b6d4', '#10b981', '#f59e0b', '#ef4444',
  '#ec4899', '#84cc16', '#f97316', '#3b82f6',
]

// Merge multiple series into a single array by time
function mergeSeries(series: Series[]): Record<string, number | string>[] {
  if (!series.length) return []

  // collect all timestamps
  const times = new Set<number>()
  series.forEach((s) => s.points.forEach((p) => times.add(p.time)))
  const sortedTimes = Array.from(times).sort((a, b) => a - b)

  return sortedTimes.map((t) => {
    const row: Record<string, number | string> = { time: t }
    series.forEach((s) => {
      const pt = s.points.find((p) => p.time === t)
      row[s.label] = pt?.value ?? 0
    })
    return row
  })
}

const CustomTooltip = ({
  active,
  payload,
  label,
  valueFormatter,
}: {
  active?: boolean
  payload?: Array<{ name: string; value: number; color: string }>
  label?: number
  valueFormatter?: (v: number) => string
}) => {
  if (!active || !payload?.length) return null
  return (
    <div className="bg-[#111118] border border-[#1e1e2e] rounded-lg p-3 shadow-xl">
      <p className="text-gray-400 text-xs mb-2">{label ? format(label, 'MMM d HH:mm') : ''}</p>
      {payload.map((p) => (
        <div key={p.name} className="flex items-center gap-2 text-xs">
          <span className="w-2 h-2 rounded-full" style={{ background: p.color }} />
          <span className="text-gray-400">{p.name}:</span>
          <span className="text-white font-medium">{valueFormatter ? valueFormatter(p.value) : p.value.toFixed(2)}</span>
        </div>
      ))}
    </div>
  )
}

function SkeletonChart() {
  return (
    <div className="h-48 flex items-end gap-1 px-4">
      {Array.from({ length: 20 }).map((_, i) => (
        <div
          key={i}
          className="flex-1 skeleton rounded-t"
          style={{ height: `${20 + Math.random() * 60}%` }}
        />
      ))}
    </div>
  )
}

export function TimeSeriesChart({
  title,
  subtitle,
  series,
  loading,
  error,
  type = 'line',
  valueFormatter,
}: TimeSeriesChartProps) {
  const data = mergeSeries(series)

  return (
    <div className="bg-[#111118] border border-[#1e1e2e] rounded-xl p-5 flex flex-col gap-4">
      <div>
        <h3 className="text-white font-semibold text-sm">{title}</h3>
        {subtitle && <p className="text-gray-500 text-xs mt-0.5">{subtitle}</p>}
      </div>

      {loading ? (
        <SkeletonChart />
      ) : error ? (
        <div className="h-48 flex items-center justify-center text-gray-600 text-sm">
          Data unavailable
        </div>
      ) : !data.length || series.every((s) => s.points.length === 0) ? (
        <div className="h-48 flex items-center justify-center text-gray-600 text-sm">
          No data for this period
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={200}>
          {type === 'area' ? (
            <AreaChart data={data} margin={{ top: 4, right: 8, left: -20, bottom: 0 }}>
              <defs>
                {series.map((s, i) => (
                  <linearGradient key={s.label} id={`grad-${i}`} x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor={COLORS[i % COLORS.length]} stopOpacity={0.3} />
                    <stop offset="95%" stopColor={COLORS[i % COLORS.length]} stopOpacity={0} />
                  </linearGradient>
                ))}
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#1e1e2e" vertical={false} />
              <XAxis
                dataKey="time"
                tickFormatter={(v) => format(v, 'HH:mm')}
                tick={{ fill: '#6b7280', fontSize: 11 }}
                axisLine={false}
                tickLine={false}
              />
              <YAxis
                tick={{ fill: '#6b7280', fontSize: 11 }}
                axisLine={false}
                tickLine={false}
                tickFormatter={valueFormatter}
              />
              <Tooltip content={<CustomTooltip valueFormatter={valueFormatter} />} />
              {series.length > 1 && <Legend />}
              {series.map((s, i) => (
                <Area
                  key={s.label}
                  type="monotone"
                  dataKey={s.label}
                  stroke={COLORS[i % COLORS.length]}
                  fill={`url(#grad-${i})`}
                  strokeWidth={2}
                  dot={false}
                  activeDot={{ r: 4 }}
                />
              ))}
            </AreaChart>
          ) : (
            <LineChart data={data} margin={{ top: 4, right: 8, left: -20, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1e1e2e" vertical={false} />
              <XAxis
                dataKey="time"
                tickFormatter={(v) => format(v, 'HH:mm')}
                tick={{ fill: '#6b7280', fontSize: 11 }}
                axisLine={false}
                tickLine={false}
              />
              <YAxis
                tick={{ fill: '#6b7280', fontSize: 11 }}
                axisLine={false}
                tickLine={false}
                tickFormatter={valueFormatter}
              />
              <Tooltip content={<CustomTooltip valueFormatter={valueFormatter} />} />
              {series.length > 1 && <Legend />}
              {series.map((s, i) => (
                <Line
                  key={s.label}
                  type="monotone"
                  dataKey={s.label}
                  stroke={COLORS[i % COLORS.length]}
                  strokeWidth={2}
                  dot={false}
                  activeDot={{ r: 4 }}
                />
              ))}
            </LineChart>
          )}
        </ResponsiveContainer>
      )}
    </div>
  )
}
