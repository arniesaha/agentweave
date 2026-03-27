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
  '#00E5CC', '#5BA4F5', '#FFBF47', '#7DDB80', '#B88CFF',
  '#FF8C94', '#5CECC6', '#FFD166', '#88A4F8', '#FF6B6B',
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
    <div className="bg-surface-overlay border border-edge rounded-lg p-3 shadow-xl backdrop-blur-sm">
      <p className="text-ink text-xs font-medium">{p.payload.label}</p>
      <p className="text-accent text-xs mt-1 mono">
        {valueFormatter ? valueFormatter(p.value) : p.value.toFixed(0)}
      </p>
    </div>
  )
}

function SkeletonBars() {
  return (
    <div className="flex flex-col gap-3 px-2 py-2">
      {Array.from({ length: 5 }).map((_, i) => (
        <div key={i} className="flex items-center gap-3">
          <div className="skeleton h-3 w-20 rounded" />
          <div className="skeleton h-5 rounded" style={{ width: `${25 + Math.random() * 45}%` }} />
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
  const chartData = data.map((d) => ({ ...d, name: d.label.length > 20 ? d.label.slice(0, 20) + '...' : d.label }))

  return (
    <div className="card glow-hover p-5 flex flex-col gap-4">
      <div>
        <h3 className="text-ink text-sm font-medium">{title}</h3>
        {subtitle && <p className="text-ink-faint text-xs mt-0.5">{subtitle}</p>}
      </div>

      {loading ? (
        <SkeletonBars />
      ) : error ? (
        <div className="h-48 flex items-center justify-center text-ink-faint text-sm">
          Data unavailable
        </div>
      ) : !chartData.length ? (
        <div className="h-48 flex items-center justify-center text-ink-faint text-sm">
          No data for this period
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={Math.max(180, chartData.length * 36)}>
          <RechartsBarChart
            layout="vertical"
            data={chartData}
            margin={{ top: 0, right: 16, left: 0, bottom: 0 }}
          >
            <CartesianGrid strokeDasharray="3 3" stroke="#1E2433" horizontal={false} />
            <XAxis
              type="number"
              tick={{ fill: '#4A5568', fontSize: 10, fontFamily: 'JetBrains Mono' }}
              axisLine={false}
              tickLine={false}
              tickFormatter={valueFormatter}
            />
            <YAxis
              type="category"
              dataKey="name"
              tick={{ fill: '#8892A6', fontSize: 11 }}
              axisLine={false}
              tickLine={false}
              width={110}
            />
            <Tooltip content={<CustomTooltip valueFormatter={valueFormatter} />} cursor={{ fill: '#1E243320' }} />
            <Bar
              dataKey="value"
              radius={[0, 3, 3, 0]}
              maxBarSize={22}
              cursor={onBarClick ? 'pointer' : undefined}
              onClick={onBarClick ? (entry: BarData & { name: string }) => onBarClick(entry.label) : undefined}
            >
              {chartData.map((entry, i) => (
                <Cell
                  key={i}
                  fill={COLORS[i % COLORS.length]}
                  opacity={selectedLabel && selectedLabel !== entry.label ? 0.25 : 0.85}
                />
              ))}
            </Bar>
          </RechartsBarChart>
        </ResponsiveContainer>
      )}
    </div>
  )
}
