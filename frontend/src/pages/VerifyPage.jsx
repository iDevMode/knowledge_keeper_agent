import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { completeSignIn } from '../api/client'

/**
 * Landing page for the emailed sign-in link. The token arrives in the URL
 * fragment (#token=...), which browsers never send to a server, and is removed
 * from the address bar before anything else happens.
 */
export default function VerifyPage() {
  const navigate = useNavigate()
  const [error, setError] = useState(null)
  const startedRef = useRef(false)

  useEffect(() => {
    if (startedRef.current) return
    startedRef.current = true

    const params = new URLSearchParams(window.location.hash.slice(1))
    const token = params.get('token')
    window.history.replaceState({}, '', window.location.pathname)

    if (!token) {
      setError('This sign-in link is incomplete. Request a new one.')
      return
    }

    completeSignIn(token)
      .then(() => navigate('/dashboard', { replace: true }))
      .catch((err) => setError(err.message))
  }, [navigate])

  return (
    <div className="min-h-screen flex items-center justify-center px-6">
      <div className="text-center animate-page-in max-w-sm">
        {error ? (
          <>
            <h1 className="font-display text-2xl text-ink-heading mb-3">Sign-in link didn't work</h1>
            <p className="text-sm text-ink-light mb-6">{error}</p>
            <Link
              to="/"
              className="inline-block px-6 py-3 rounded-xl bg-keeper-500 text-white font-medium hover:bg-keeper-400 transition-colors"
            >
              Get a new link
            </Link>
          </>
        ) : (
          <>
            <div className="w-8 h-8 border-2 border-keeper-500/30 border-t-keeper-500 rounded-full animate-spin mx-auto mb-4" />
            <p className="text-sm text-parchment-500">Signing you in...</p>
          </>
        )}
      </div>
    </div>
  )
}
