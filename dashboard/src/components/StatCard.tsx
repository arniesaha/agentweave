import React from 'react'
import { LucideIcon } from 'lucide-react'

interface StatCardProps {
  icon: LucideIcon
  iconColor: string
  iconBg: string
  label: string
  value: string | null
  loading: boolean
  error?: string | null
  delta?: string | null
  deltaPositive?: boolean
}

function SkeletonBar({ width = 'w-24' }: { width?: string }) {
  return <div className={`h-8 ${width} skeleton`} />
}

export function StatCard({
  icon: Icon,
  iconColor,
  iconBg,
  label,
  value,
  loading,
  error,
  delta,
  deltaPositive,
}: StatCardProps) {
  return (
    <div className="bg-[#111118] border border-[#1e1e2e] rounded-xl p-5 flex flex-col gap-3 hover:border-indigo-500/20 transition-colors">
      <div className="flex items-center justify-between">
        <div className={`w-10 h-10 rounded-lg ${iconBg} flex items-center justify-center`}>
          <Icon className={`w-5 h-5 ${iconColor}`} />
        </div>
        {error && (
          <span className="text-xs text-amber-400/70 bg-amber-400/10 px-2 py-0.5 rounded-full">
            unavailable
          </span>
        )}
      </div>

      <div>
        {loading ? (
          <SkeletonBar />
        ) : error ? (
          <div className="text-2xl font-bold text-gray-600">—</div>
        ) : (
          <div className="text-3xl font-bold text-white tabular-nums">{value ?? '—'}</div>
        )}
        <div className="text-sm text-gray-500 mt-1 font-medium">{label}</div>
      </div>

      {delta && !loading && !error && (
        <div className={`text-xs font-medium ${deltaPositive ? 'text-emerald-400' : 'text-red-400'}`}>
          {delta} vs last period
        </div>
      )}
    </div>
  )
}
