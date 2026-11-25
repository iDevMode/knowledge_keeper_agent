import { useState } from 'react'
import { Navigate } from 'react-router-dom'
import { getAccount, requestSignInLink } from '../api/client'

export function LogoMark({ size = 'lg' }) {
  const box = size === 'lg' ? 'w-16 h-16 rounded-2xl' : 'w-9 h-9 rounded-xl'
  const icon = size === 'lg' ? 32 : 18
  return (
    <div className={`${box} bg-keeper-500 flex items-center justify-center flex-shrink-0`}>
      <svg width={icon} height={icon} viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M4 19.5A2.5 2.5 0 016.5 17H20" />
        <path d="M6.5 2H20v20H6.5A2.5 2.5 0 014 19.5v-15A2.5 2.5 0 016.5 2z" />
        <path d="M8 7h8" />
        <path d="M8 11h6" />
      </svg>
    </div>
  )
}

/**
 * Sign-in. Managers now have accounts: they sign in with an emailed one-time
 * link, and every handover they start is listed on their dashboard — so
 * closing a tab no longer loses the pack.
 */
export default function HomePage() {
  const [email, setEmail] = useState('')
  const [sending, setSending] = useState(false)
  const [sentTo, setSentTo] = useState(null)
  const [notice, setNotice] = useState(null)
  const [error, setError] = useState(null)

  if (getAccount()) return <Navigate to="/dashboard" replace />

  async function handleSubmit(e) {
    e.preventDefault()
    const address = email.trim()
    if (!address) return
    setSending(true)
    setError(null)
    try {
      const message = await requestSignInLink(address)
      setSentTo(address)
      setNotice(message)
    } catch (err) {
      setError(err.message)
    } finally {
      setSending(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center px-6">
      <div className="max-w-lg w-full text-center animate-page-in">
        <div className="mx-auto mb-8 w-16">
          <LogoMark />
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

        {sentTo ? (
          <div className="max-w-sm mx-auto bg-white/70 border border-parchment-200 rounded-2xl p-6 text-left">
            <h2 className="font-display text-xl text-ink-heading mb-2">Check your email</h2>
            <p className="text-sm text-ink-light">
              We've sent a sign-in link to <span className="font-medium text-ink">{sentTo}</span>.
            </p>
            <p className="text-xs text-parchment-500 mt-2">{notice}</p>
            <button
              type="button"
              onClick={() => { setSentTo(null); setNotice(null) }}
              className="mt-4 text-sm text-keeper-500 hover:text-keeper-400 underline underline-offset-2"
            >
              Use a different email
            </button>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="max-w-sm mx-auto text-left">
            <label htmlFor="email" className="block text-sm font-medium text-ink mb-2">
              Sign in to start or continue a handover
            </label>
            <input
              id="email"
              type="email"
              autoComplete="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@company.co.uk"
              className="w-full px-4 py-3 rounded-xl border border-parchment-300 bg-white text-ink placeholder:text-parchment-400 focus:outline-none focus:ring-2 focus:ring-keeper-300"
            />
            <button
              type="submit"
              disabled={sending}
              className="mt-3 w-full inline-flex items-center justify-center gap-2 px-8 py-3.5 rounded-xl bg-keeper-500 text-white font-medium text-base hover:bg-keeper-400 transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
            >
              {sending ? (
                <>
                  <span className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                  Sending link...
                </>
              ) : (
                'Email me a sign-in link'
              )}
            </button>
            <p className="mt-3 text-xs text-parchment-500 text-center">
              No password needed — we'll email you a link that signs you in.
            </p>
            {error && <p className="mt-3 text-sm text-red-600 text-center">{error}</p>}
          </form>
        )}

        <p className="mt-12 text-xs text-parchment-500">
          Built by Nukode
        </p>
      </div>
    </div>
  )
}
