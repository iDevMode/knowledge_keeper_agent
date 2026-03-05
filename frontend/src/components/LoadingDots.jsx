export default function LoadingDots() {
  return (
    <div className="flex items-center gap-1 px-4 py-3">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className={`block w-2 h-2 rounded-full bg-parchment-500 animate-bounce-dot dot-${i + 1}`}
        />
      ))}
    </div>
  )
}
