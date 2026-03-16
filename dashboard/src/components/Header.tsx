import React from 'react'
import { RefreshCw, Zap } from 'lucide-react'
import { TimeRange } from '../lib/queries'
import { formatDistanceToNow } from 'date-fns'

interface HeaderProps {
  timeRange: TimeRange
  onTimeRangeChange: (range: TimeRange) => void
  onRefresh: () => void
  lastUpdated: Date | null
  tempoError: boolean
}

const TIME_RANGES: { value: TimeRange; label: string }[] = [
  { value: '1h', label: 'Last 1h' },
  { value: '3h', label: 'Last 3h' },
  { value: '6h', label: 'Last 6h' },
  { value: '24h', label: 'Last 24h' },
  { value: '7d', label: 'Last 7d' },
]

export function Header({ timeRange, onTimeRangeChange, onRefresh, lastUpdated, tempoError }: HeaderProps) {
  return (
    <>
      {tempoError && (
        <div className="bg-amber-500/10 border-b border-amber-500/20 px-6 py-2 text-amber-400 text-sm flex items-center gap-2">
          <span className="font-medium">⚠️ Tempo unavailable</span>
          <span className="text-amber-400/70">— showing cached data or empty panels</span>
        </div>
      )}
      <header className="sticky top-0 z-50 bg-[#0a0a0f]/95 backdrop-blur border-b border-[#1e1e2e] px-4 sm:px-6 py-4">
        <div className="max-w-screen-2xl mx-auto flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 sm:gap-4">
          {/* Logo */}
          <div className="flex items-center gap-2 sm:flex-shrink-0">
            <div className="w-8 h-8 rounded-lg bg-indigo-600 flex items-center justify-center">
              <Zap className="w-4 h-4 text-white" />
            </div>
            <div>
              <div className="text-white font-bold text-lg leading-none">AgentWeave</div>
              <div className="text-gray-500 text-xs leading-none mt-0.5">Agent Activity Dashboard</div>
            </div>
          </div>

          {/* Right controls */}
          <div className="flex items-center gap-2 sm:gap-3 sm:ml-auto overflow-x-auto">
            {lastUpdated && (
              <span className="text-gray-500 text-xs hidden sm:block">
                Updated {formatDistanceToNow(lastUpdated, { addSuffix: true })}
              </span>
            )}

            {/* Time range selector */}
            <div className="flex items-center bg-[#111118] border border-[#1e1e2e] rounded-lg overflow-hidden text-nowrap">
              {TIME_RANGES.map((r) => (
                <button
                  key={r.value}
                  onClick={() => onTimeRangeChange(r.value)}
                  className={`px-2 py-1 text-xs sm:px-3 sm:py-1.5 sm:text-sm font-medium transition-colors whitespace-nowrap ${
                    timeRange === r.value
                      ? 'bg-indigo-600 text-white'
                      : 'text-gray-400 hover:text-white hover:bg-[#1e1e2e]'
                  }`}
                >
                  {r.label}
                </button>
              ))}
            </div>

            {/* Refresh button */}
            <button
              onClick={onRefresh}
              className="flex items-center gap-1 px-2 py-1 sm:px-3 sm:py-1.5 bg-[#111118] border border-[#1e1e2e] rounded-lg text-xs sm:text-sm text-gray-400 hover:text-white hover:border-indigo-500/50 transition-colors flex-shrink-0"
            >
              <RefreshCw className="w-3.5 h-3.5" />
              <span className="hidden sm:block">Refresh</span>
            </button>
          </div>
        </div>
      </header>
    </>
  )
}
