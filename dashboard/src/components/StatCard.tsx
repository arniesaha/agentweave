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
  return <div className={`h-7 ${width} skeleton`} />
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
    <div className="card glow-hover p-5 flex flex-col gap-4 group">
      <div className="flex items-center justify-between">
        <div className={`w-9 h-9 rounded-lg ${iconBg} flex items-center justify-center transition-transform duration-300 group-hover:scale-105`}>
          <Icon className={`w-4.5 h-4.5 ${iconColor}`} strokeWidth={1.5} />
        </div>
        {error && (
          <span className="text-[10px] text-signal-amber/70 bg-signal-amber/8 px-2 py-0.5 rounded-full font-medium uppercase tracking-wider">
            offline
          </span>
        )}
      </div>

      <div>
        <div className="text-xs text-ink-faint font-medium uppercase tracking-wider mb-2">{label}</div>
        {loading ? (
          <SkeletonBar />
        ) : error ? (
          <div className="text-2xl font-semibold text-ink-faint mono">--</div>
        ) : (
          <div className="text-2xl font-semibold text-ink mono tracking-tight">{value ?? '--'}</div>
        )}
      </div>

      {delta && !loading && !error && (
        <div className={`text-xs font-medium mono ${deltaPositive ? 'text-signal-lime' : 'text-signal-coral'}`}>
          {delta}
        </div>
      )}
    </div>
  )
}
