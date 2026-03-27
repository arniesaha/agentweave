import React from 'react'
import { RefreshCw, Hexagon, ChevronDown } from 'lucide-react'
import { TimeRange } from '../lib/queries'
import { formatDistanceToNow } from 'date-fns'

interface HeaderProps {
  timeRange: TimeRange
  onTimeRangeChange: (range: TimeRange) => void
  onRefresh: () => void
  lastUpdated: Date | null
  tempoError: boolean
  projects?: string[]
  selectedProject: string | null
  onProjectChange: (project: string | null) => void
}

const TIME_RANGES: { value: TimeRange; label: string }[] = [
  { value: '15m', label: '15m' },
  { value: '1h', label: '1h' },
  { value: '3h', label: '3h' },
  { value: '6h', label: '6h' },
  { value: '24h', label: '24h' },
  { value: '7d', label: '7d' },
]

export function Header({ timeRange, onTimeRangeChange, onRefresh, lastUpdated, tempoError, projects, selectedProject, onProjectChange }: HeaderProps) {
  return (
    <>
      {tempoError && (
        <div className="bg-signal-amber/5 border-b border-signal-amber/15 px-6 py-2 text-signal-amber text-sm flex items-center gap-2">
          <span className="w-1.5 h-1.5 rounded-full bg-signal-amber animate-pulse-subtle" />
          <span className="font-medium">Tempo unavailable</span>
          <span className="text-signal-amber/50">— showing cached data</span>
        </div>
      )}
      <header className="sticky top-0 z-50 bg-void/90 backdrop-blur-xl border-b border-edge px-4 sm:px-6 py-3.5">
        <div className="max-w-screen-2xl mx-auto flex items-center justify-between gap-4">
          {/* Logo */}
          <div className="flex items-center gap-3 flex-shrink-0">
            <div className="relative">
              <Hexagon className="w-7 h-7 text-accent" strokeWidth={1.5} />
              <div className="absolute inset-0 flex items-center justify-center">
                <div className="w-2 h-2 rounded-full bg-accent shadow-glow-sm" />
              </div>
            </div>
            <div>
              <div className="text-ink font-semibold text-base tracking-tight leading-none">AgentWeave</div>
              <div className="text-ink-faint text-[10px] uppercase tracking-[0.15em] leading-none mt-1">Observability</div>
            </div>
          </div>

          {/* Right controls */}
          <div className="flex items-center gap-3 overflow-x-auto">
            {lastUpdated && (
              <span className="text-ink-faint text-xs hidden sm:flex items-center gap-1.5">
                <span className="w-1 h-1 rounded-full bg-signal-lime animate-pulse-subtle" />
                {formatDistanceToNow(lastUpdated, { addSuffix: true })}
              </span>
            )}

            {/* Project filter */}
            {projects && projects.length > 0 && (
              <div className="relative">
                <select
                  value={selectedProject ?? ''}
                  onChange={(e) => onProjectChange(e.target.value || null)}
                  className="pl-2.5 pr-7 py-1.5 text-xs bg-surface border border-edge rounded-lg text-ink-muted hover:border-edge-hover focus:border-accent/40 focus:outline-none transition-colors appearance-none cursor-pointer"
                >
                  <option value="">All projects</option>
                  {projects.map((p) => (
                    <option key={p} value={p}>{p}</option>
                  ))}
                </select>
                <ChevronDown className="absolute right-2 top-1/2 -translate-y-1/2 w-3 h-3 text-ink-faint pointer-events-none" />
              </div>
            )}

            {/* Time range selector */}
            <div className="flex items-center bg-surface border border-edge rounded-lg overflow-hidden">
              {TIME_RANGES.map((r) => (
                <button
                  key={r.value}
                  onClick={() => onTimeRangeChange(r.value)}
                  className={`px-2.5 py-1.5 text-xs font-medium transition-all duration-200 ${
                    timeRange === r.value
                      ? 'bg-accent/12 text-accent border-accent/0'
                      : 'text-ink-faint hover:text-ink-muted hover:bg-surface-raised'
                  }`}
                >
                  {r.label}
                </button>
              ))}
            </div>

            {/* Refresh button */}
            <button
              onClick={onRefresh}
              className="flex items-center gap-1.5 px-2.5 py-1.5 bg-surface border border-edge rounded-lg text-xs text-ink-faint hover:text-accent hover:border-accent/30 transition-all duration-200 flex-shrink-0 group"
            >
              <RefreshCw className="w-3.5 h-3.5 group-hover:rotate-90 transition-transform duration-300" />
              <span className="hidden sm:block">Refresh</span>
            </button>
          </div>
        </div>
      </header>
    </>
  )
}
