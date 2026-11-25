const STAGE1_BLOCKS = [
  { key: 'business_context', label: 'Business' },
  { key: 'vacant_role', label: 'Role' },
  { key: 'replacement_profile', label: 'Replacement' },
  { key: 'knowledge_priorities', label: 'Priorities' },
  { key: 'output_preferences', label: 'Output' },
  { key: 'departure_sensitivity', label: 'Departure' },
  { key: 'profile_generation', label: 'Review' },
]

export default function ProgressBar({ stage, currentBlock }) {
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

  // Stage 2 is the departing employee's own view. It deliberately shows no
  // risk information: risks are recorded for the manager, and a live count
  // shown to the person being interviewed undermines candour.
  return (
    <div className="flex items-center px-4 py-2">
      <span className="text-sm font-medium text-ink-light capitalize">
        {currentBlock ? currentBlock.replace(/_/g, ' ') : 'Starting...'}
      </span>
    </div>
  )
}
