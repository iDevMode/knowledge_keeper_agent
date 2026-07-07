import { DEMO_MODE } from '../api/client'

/**
 * Honest "this is a demo" strip, shown only when the build runs in demo mode
 * (the Vercel preview). Makes clear the flow is realistic canned data, not a
 * live backend, so nobody mistakes the mock for the real interview agent.
 */
export default function DemoBanner() {
  if (!DEMO_MODE) return null
  return (
    <div className="w-full bg-keeper-500 text-white text-xs sm:text-sm text-center px-4 py-1.5">
      <span className="font-semibold">Demo mode</span>
      <span className="opacity-90">
        {' '}— realistic canned data, no live backend. Type anything to advance the interview.
      </span>
    </div>
  )
}
