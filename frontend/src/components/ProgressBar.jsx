const STAGE1_BLOCKS = [
  { key: 'business_context', label: 'Business' },
  { key: 'vacant_role', label: 'Role' },
  { key: 'replacement_profile', label: 'Replacement' },
  { key: 'knowledge_priorities', label: 'Priorities' },
  { key: 'output_preferences', label: 'Output' },
  { key: 'departure_sensitivity', label: 'Departure' },
  { key: 'profile_generation', label: 'Review' },
]

export default function ProgressBar({ stage, currentBlock, riskFlagCount, progress }) {
  if (stage === 1) {
    const currentIndex = STAGE1_BLOCKS.findIndex((b) => b.key === currentBlock)

    return (
      <div className="flex items-center gap-1 px-4 py-2">
        {STAGE1_BLOCKS.map((block, i) => (
          <div key={block.key} className="flex-1 flex flex-col items-center gap-1">
            <div
              className={`h-1.5 w-full rounded-full transition-all duration-400 ${
                i < currentIndex
                  ? 'bg-keeper-400'
                  : i === currentIndex
                  ? 'bg-keeper-500'
                  : 'bg-parchment-200'
              }`}
            />
            <span
              className={`text-[10px] font-medium transition-colors ${
                i <= currentIndex ? 'text-keeper-500' : 'text-parchment-500'
              }`}
            >
              {block.label}
            </span>
          </div>
        ))}
      </div>
    )
  }

  // Stage 2: honest progress (section n of m + percent bar) + risk flag badge
  const percent = progress?.percent ?? null

  return (
    <div className="flex items-center justify-between gap-4 px-4 py-2">
      <div className="flex-1 min-w-0">
        <div className="flex items-center justify-between mb-1">
          <span className="text-sm font-medium text-ink-light capitalize truncate">
            {currentBlock ? currentBlock.replace(/_/g, ' ') : 'Starting...'}
          </span>
          {progress && (
            <span className="text-[11px] text-parchment-500 whitespace-nowrap ml-2">
              Section {progress.section_number} of {progress.section_total}
              {percent !== null && ` · ${percent}%`}
            </span>
          )}
        </div>
        {percent !== null && (
          <div className="h-1.5 w-full rounded-full bg-parchment-200 overflow-hidden">
            <div
              className="h-full rounded-full bg-keeper-500 transition-all duration-500"
              style={{ width: `${percent}%` }}
            />
          </div>
        )}
      </div>
      {riskFlagCount > 0 && (
        <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-flag-100 text-flag-600">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z" />
            <line x1="4" y1="22" x2="4" y2="15" />
          </svg>
          {riskFlagCount} {riskFlagCount === 1 ? 'flag' : 'flags'}
        </span>
      )}
    </div>
  )
}
