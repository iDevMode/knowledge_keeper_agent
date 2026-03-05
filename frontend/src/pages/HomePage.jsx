import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { createStage1Session } from '../api/client'

export default function HomePage() {
  const navigate = useNavigate()
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  async function handleStart() {
    setLoading(true)
    setError(null)
    try {
      const data = await createStage1Session()
      navigate(`/stage1/${data.session_id}`, {
        state: { greeting: data.message },
      })
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center px-6">
      <div className="max-w-lg w-full text-center animate-page-in">
        {/* Logo mark */}
        <div className="mx-auto w-16 h-16 rounded-2xl bg-keeper-500 flex items-center justify-center mb-8">
          <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M4 19.5A2.5 2.5 0 016.5 17H20" />
            <path d="M6.5 2H20v20H6.5A2.5 2.5 0 014 19.5v-15A2.5 2.5 0 016.5 2z" />
            <path d="M8 7h8" />
            <path d="M8 11h6" />
          </svg>
        </div>

        <h1 className="font-display text-4xl text-ink-heading mb-3">
          KnowledgeKeeper
        </h1>
        <p className="text-lg text-ink-light mb-2">
          Capture what matters before it walks out the door.
        </p>
        <p className="text-sm text-parchment-500 mb-10 max-w-sm mx-auto">
          A guided interview system that captures institutional knowledge from departing employees and produces comprehensive handover documentation.
        </p>

        <button
          onClick={handleStart}
          disabled={loading}
          className="inline-flex items-center gap-2 px-8 py-3.5 rounded-xl bg-keeper-500 text-white font-medium text-base hover:bg-keeper-400 transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
        >
          {loading ? (
            <>
              <span className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
              Setting up...
            </>
          ) : (
            'Begin Interview Setup'
          )}
        </button>

        {error && (
          <p className="mt-4 text-sm text-red-600">{error}</p>
        )}

        <p className="mt-12 text-xs text-parchment-500">
          Built by Nukode
        </p>
      </div>
    </div>
  )
}
