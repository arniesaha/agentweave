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
  '#00E5CC', '#5BA4F5', '#FFBF47', '#7DDB80', '#FF6B6B',
  '#B88CFF', '#FF8C94', '#5CECC6', '#FFD166', '#88A4F8',
]

function mergeSeries(series: Series[]): Record<string, number | string>[] {
  if (!series.length) return []
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
    <div className="bg-surface-overlay border border-edge rounded-lg p-3 shadow-xl backdrop-blur-sm">
      <p className="text-ink-faint text-[10px] uppercase tracking-wider mb-2 mono">
        {label ? format(label, 'MMM d HH:mm') : ''}
      </p>
      {payload.map((p) => (
        <div key={p.name} className="flex items-center gap-2 text-xs py-0.5">
          <span className="w-1.5 h-1.5 rounded-full" style={{ background: p.color }} />
          <span className="text-ink-muted">{p.name}</span>
          <span className="text-ink font-medium mono ml-auto">{valueFormatter ? valueFormatter(p.value) : p.value.toFixed(2)}</span>
        </div>
      ))}
    </div>
  )
}

function SkeletonChart() {
  return (
    <div className="h-48 flex items-end gap-1 px-4">
      {Array.from({ length: 24 }).map((_, i) => (
        <div
          key={i}
          className="flex-1 skeleton rounded-t"
          style={{ height: `${15 + Math.random() * 55}%`, animationDelay: `${i * 40}ms` }}
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
    <div className="card glow-hover p-5 flex flex-col gap-4">
      <div>
        <h3 className="text-ink text-sm font-medium">{title}</h3>
        {subtitle && <p className="text-ink-faint text-xs mt-0.5">{subtitle}</p>}
      </div>

      {loading ? (
        <SkeletonChart />
      ) : error ? (
        <div className="h-48 flex items-center justify-center text-ink-faint text-sm">
          Data unavailable
        </div>
      ) : !data.length || series.every((s) => s.points.length === 0) ? (
        <div className="h-48 flex items-center justify-center text-ink-faint text-sm">
          No data for this period
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={200}>
          {type === 'area' ? (
            <AreaChart data={data} margin={{ top: 4, right: 8, left: -20, bottom: 0 }}>
              <defs>
                {series.map((s, i) => (
                  <linearGradient key={s.label} id={`grad-${i}`} x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor={COLORS[i % COLORS.length]} stopOpacity={0.25} />
                    <stop offset="95%" stopColor={COLORS[i % COLORS.length]} stopOpacity={0} />
                  </linearGradient>
                ))}
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#1E2433" vertical={false} />
              <XAxis
                dataKey="time"
                tickFormatter={(v) => format(v, 'HH:mm')}
                tick={{ fill: '#4A5568', fontSize: 10, fontFamily: 'JetBrains Mono' }}
                axisLine={false}
                tickLine={false}
              />
              <YAxis
                tick={{ fill: '#4A5568', fontSize: 10, fontFamily: 'JetBrains Mono' }}
                axisLine={false}
                tickLine={false}
                tickFormatter={valueFormatter}
              />
              <Tooltip content={<CustomTooltip valueFormatter={valueFormatter} />} />
              {series.length > 1 && <Legend wrapperStyle={{ fontSize: 11, color: '#8892A6' }} />}
              {series.map((s, i) => (
                <Area
                  key={s.label}
                  type="monotone"
                  dataKey={s.label}
                  stroke={COLORS[i % COLORS.length]}
                  fill={`url(#grad-${i})`}
                  strokeWidth={1.5}
                  dot={false}
                  activeDot={{ r: 3, strokeWidth: 0, fill: COLORS[i % COLORS.length] }}
                />
              ))}
            </AreaChart>
          ) : (
            <LineChart data={data} margin={{ top: 4, right: 8, left: -20, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1E2433" vertical={false} />
              <XAxis
                dataKey="time"
                tickFormatter={(v) => format(v, 'HH:mm')}
                tick={{ fill: '#4A5568', fontSize: 10, fontFamily: 'JetBrains Mono' }}
                axisLine={false}
                tickLine={false}
              />
              <YAxis
                tick={{ fill: '#4A5568', fontSize: 10, fontFamily: 'JetBrains Mono' }}
                axisLine={false}
                tickLine={false}
                tickFormatter={valueFormatter}
              />
              <Tooltip content={<CustomTooltip valueFormatter={valueFormatter} />} />
              {series.length > 1 && <Legend wrapperStyle={{ fontSize: 11, color: '#8892A6' }} />}
              {series.map((s, i) => (
                <Line
                  key={s.label}
                  type="monotone"
                  dataKey={s.label}
                  stroke={COLORS[i % COLORS.length]}
                  strokeWidth={1.5}
                  dot={false}
                  activeDot={{ r: 3, strokeWidth: 0, fill: COLORS[i % COLORS.length] }}
                />
              ))}
            </LineChart>
          )}
        </ResponsiveContainer>
      )}
    </div>
  )
}
