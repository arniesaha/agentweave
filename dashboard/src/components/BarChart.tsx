import React from 'react'
import {
  BarChart as RechartsBarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Cell,
} from 'recharts'

interface BarData {
  label: string
  value: number
}

interface BarChartProps {
  title: string
  subtitle?: string
  data: BarData[]
  loading: boolean
  error?: string | null
  valueFormatter?: (v: number) => string
  color?: string
  onBarClick?: (label: string) => void
  selectedLabel?: string | null
}

const COLORS = [
  '#6366f1', '#818cf8', '#8b5cf6', '#a78bfa', '#7c3aed',
  '#4f46e5', '#06b6d4', '#0891b2', '#0ea5e9', '#38bdf8',
]

const CustomTooltip = ({
  active,
  payload,
  valueFormatter,
}: {
  active?: boolean
  payload?: Array<{ name: string; value: number; payload: BarData }>
  valueFormatter?: (v: number) => string
}) => {
  if (!active || !payload?.length) return null
  const p = payload[0]
  return (
    <div className="bg-[#111118] border border-[#1e1e2e] rounded-lg p-3 shadow-xl">
      <p className="text-white text-xs font-medium">{p.payload.label}</p>
      <p className="text-indigo-400 text-xs mt-1">
        {valueFormatter ? valueFormatter(p.value) : p.value.toFixed(0)}
      </p>
    </div>
  )
}

function SkeletonBars() {
  return (
    <div className="flex flex-col gap-2 px-2 py-2">
      {Array.from({ length: 6 }).map((_, i) => (
        <div key={i} className="flex items-center gap-2">
          <div className="skeleton h-3 rounded" style={{ width: `${30 + Math.random() * 40}%` }} />
          <div className="skeleton h-6 rounded" style={{ width: `${20 + Math.random() * 50}%` }} />
        </div>
      ))}
    </div>
  )
}

export function BarChartPanel({
  title,
  subtitle,
  data,
  loading,
  error,
  valueFormatter,
  onBarClick,
  selectedLabel,
}: BarChartProps) {
  const chartData = data.map((d) => ({ ...d, name: d.label.length > 20 ? d.label.slice(0, 20) + '…' : d.label }))

  return (
    <div className="bg-[#111118] border border-[#1e1e2e] rounded-xl p-5 flex flex-col gap-4">
      <div>
        <h3 className="text-white font-semibold text-sm">{title}</h3>
        {subtitle && <p className="text-gray-500 text-xs mt-0.5">{subtitle}</p>}
      </div>

      {loading ? (
        <SkeletonBars />
      ) : error ? (
        <div className="h-48 flex items-center justify-center text-gray-600 text-sm">
          Data unavailable
        </div>
      ) : !chartData.length ? (
        <div className="h-48 flex items-center justify-center text-gray-600 text-sm">
          No data for this period
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={Math.max(180, chartData.length * 36)}>
          <RechartsBarChart
            layout="vertical"
            data={chartData}
            margin={{ top: 0, right: 16, left: 0, bottom: 0 }}
          >
            <CartesianGrid strokeDasharray="3 3" stroke="#1e1e2e" horizontal={false} />
            <XAxis
              type="number"
              tick={{ fill: '#6b7280', fontSize: 11 }}
              axisLine={false}
              tickLine={false}
              tickFormatter={valueFormatter}
            />
            <YAxis
              type="category"
              dataKey="name"
              tick={{ fill: '#9ca3af', fontSize: 11 }}
              axisLine={false}
              tickLine={false}
              width={110}
            />
            <Tooltip content={<CustomTooltip valueFormatter={valueFormatter} />} cursor={{ fill: '#1e1e2e' }} />
            <Bar
              dataKey="value"
              radius={[0, 4, 4, 0]}
              maxBarSize={24}
              cursor={onBarClick ? 'pointer' : undefined}
              onClick={onBarClick ? (entry: BarData & { name: string }) => onBarClick(entry.label) : undefined}
            >
              {chartData.map((entry, i) => (
                <Cell
                  key={i}
                  fill={COLORS[i % COLORS.length]}
                  opacity={selectedLabel && selectedLabel !== entry.label ? 0.35 : 1}
                />
              ))}
            </Bar>
          </RechartsBarChart>
        </ResponsiveContainer>
      )}
    </div>
  )
}
