import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  SignedOutError,
  createStage1Session,
  getAccount,
  getDownloadUrl,
  listHandovers,
  openHandover,
  signOut,
} from '../api/client'
import { LogoMark } from './HomePage'

const REFRESH_MS = 15000
// Statuses that will change without the manager doing anything.
const IN_FLIGHT = new Set(['awaiting_employee', 'interview_complete', 'generating'])

const STATUS_STYLE = {
  setup: 'bg-parchment-100 text-parchment-700',
  awaiting_employee: 'bg-flag-100 text-flag-600',
  interview_complete: 'bg-keeper-50 text-keeper-600',
  generating: 'bg-keeper-50 text-keeper-600',
  ready: 'bg-keeper-500 text-white',
  generation_failed: 'bg-red-50 text-red-700',
  expired: 'bg-parchment-100 text-parchment-500',
}

function formatDate(seconds) {
  return new Date(seconds * 1000).toLocaleDateString(undefined, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  })
}

function DeliveryLine({ delivery }) {
  if (!delivery) return null
  if (delivery.status === 'sent') {
    return <p className="text-xs text-parchment-500 mt-1">Emailed to {delivery.recipients.join(', ')}</p>
  }
  return (
    <p className={`text-xs mt-1 ${delivery.status === 'failed' ? 'text-red-600' : 'text-parchment-500'}`}>
      {delivery.status === 'failed' ? 'Email failed: ' : 'Not emailed: '}
      {delivery.error}
    </p>
  )
}

function HandoverCard({ handover, onOpen, onDownload, busy }) {
  const { status } = handover
  const title = handover.title || (status === 'setup' ? 'New handover' : 'Untitled handover')

  return (
    <li className="bg-white/70 border border-parchment-200 rounded-2xl p-5 flex flex-col sm:flex-row sm:items-center gap-4">
      <div className="flex-1 min-w-0">
        <div className="flex flex-wrap items-center gap-2 mb-1">
          <h3 className="font-display text-lg text-ink-heading truncate">{title}</h3>
          <span className={`text-xs font-medium px-2.5 py-1 rounded-full ${STATUS_STYLE[status] || STATUS_STYLE.setup}`}>
            {handover.status_label}
          </span>
        </div>
        <p className="text-xs text-parchment-500">
          {handover.company ? `${handover.company} · ` : ''}Started {formatDate(handover.created_at)}
        </p>
        {handover.document_error && (
          <p className="text-xs text-red-600 mt-1">{handover.document_error}</p>
        )}
        <DeliveryLine delivery={handover.delivery} />
      </div>

      {status !== 'expired' && (
        <div className="flex gap-2 flex-shrink-0">
          {status === 'ready' && (
            <button
              type="button"
              disabled={busy}
              onClick={() => onDownload(handover)}
              className="px-4 py-2.5 rounded-lg bg-keeper-500 text-white text-sm font-medium hover:bg-keeper-400 transition-colors disabled:opacity-60"
            >
              Download pack
            </button>
          )}
          <button
            type="button"
            disabled={busy}
            onClick={() => onOpen(handover)}
            className={
              status === 'ready'
                ? 'px-4 py-2.5 rounded-lg border border-parchment-300 text-ink text-sm font-medium hover:bg-parchment-50 transition-colors disabled:opacity-60'
                : 'px-4 py-2.5 rounded-lg bg-keeper-500 text-white text-sm font-medium hover:bg-keeper-400 transition-colors disabled:opacity-60'
            }
          >
            {status === 'setup' ? 'Continue setup' : status === 'awaiting_employee' ? 'Employee link' : 'Open'}
          </button>
        </div>
      )}
    </li>
  )
}

export default function DashboardPage() {
  const navigate = useNavigate()
  const account = getAccount()
  const [handovers, setHandovers] = useState(null)
  const [error, setError] = useState(null)
  const [busyId, setBusyId] = useState(null)
  const [starting, setStarting] = useState(false)

  const handleError = useCallback(
    (err) => {
      if (err instanceof SignedOutError) {
        navigate('/', { replace: true })
        return
      }
      setError(err.message)
    },
    [navigate]
  )

  const refresh = useCallback(async () => {
    try {
      setHandovers(await listHandovers())
      setError(null)
    } catch (err) {
      handleError(err)
    }
  }, [handleError])

  useEffect(() => {
    if (!account) {
      navigate('/', { replace: true })
      return
    }
    refresh()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Keep in-flight handovers current without a manual refresh.
  useEffect(() => {
    if (!handovers?.some((h) => IN_FLIGHT.has(h.status))) return
    const timer = setInterval(refresh, REFRESH_MS)
    return () => clearInterval(timer)
  }, [handovers, refresh])

  async function startHandover() {
    setStarting(true)
    setError(null)
    try {
      const data = await createStage1Session()
      navigate(`/stage1/${data.session_id}`, { state: { greeting: data.message } })
    } catch (err) {
      handleError(err)
      setStarting(false)
    }
  }

  async function open(handover) {
    setBusyId(handover.stage1_session_id)
    try {
      await openHandover(handover.stage1_session_id)
      navigate(`/stage1/${handover.stage1_session_id}`)
    } catch (err) {
      handleError(err)
    } finally {
      setBusyId(null)
    }
  }

  async function download(handover) {
    setBusyId(handover.stage1_session_id)
    try {
      await openHandover(handover.stage1_session_id)
      window.location.href = getDownloadUrl(handover.document_id, handover.stage1_session_id)
    } catch (err) {
      handleError(err)
    } finally {
      setBusyId(null)
    }
  }

  function handleSignOut() {
    signOut()
    navigate('/', { replace: true })
  }

  if (!account) return null

  return (
    <div className="min-h-screen">
      <header className="border-b border-parchment-200 bg-white/60 backdrop-blur-sm">
        <div className="max-w-3xl mx-auto px-4 sm:px-6 py-4 flex items-center justify-between gap-4">
          <div className="flex items-center gap-3 min-w-0">
            <LogoMark size="sm" />
            <div className="min-w-0">
              <h1 className="font-display text-xl text-ink-heading">KnowledgeKeeper</h1>
              <p className="text-xs text-parchment-500 truncate">{account.email}</p>
            </div>
          </div>
          <button
            type="button"
            onClick={handleSignOut}
            className="text-sm text-parchment-600 hover:text-ink transition-colors flex-shrink-0"
          >
            Sign out
          </button>
        </div>
      </header>

      <main className="max-w-3xl mx-auto px-4 sm:px-6 py-8 animate-page-in">
        <div className="flex flex-col sm:flex-row sm:items-end justify-between gap-4 mb-6">
          <div>
            <h2 className="font-display text-3xl text-ink-heading">Your handovers</h2>
            <p className="text-sm text-parchment-500 mt-1">
              Every handover you start is kept here, so you can come back for the employee link or the finished pack.
            </p>
          </div>
          <button
            type="button"
            onClick={startHandover}
            disabled={starting}
            className="inline-flex items-center justify-center gap-2 px-5 py-3 rounded-xl bg-keeper-500 text-white font-medium hover:bg-keeper-400 transition-colors disabled:opacity-60 flex-shrink-0"
          >
            {starting ? (
              <>
                <span className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                Starting...
              </>
            ) : (
              'Start a new handover'
            )}
          </button>
        </div>

        {error && <p className="mb-4 text-sm text-red-600">{error}</p>}

        {handovers === null ? (
          <div className="flex items-center gap-2 py-10 justify-center text-sm text-parchment-500">
            <span className="w-4 h-4 border-2 border-keeper-500/30 border-t-keeper-500 rounded-full animate-spin" />
            Loading your handovers...
          </div>
        ) : handovers.length === 0 ? (
          <div className="border border-dashed border-parchment-300 rounded-2xl p-10 text-center">
            <h3 className="font-display text-xl text-ink-heading mb-2">No handovers yet</h3>
            <p className="text-sm text-parchment-500 max-w-sm mx-auto">
              Start one when someone hands in their notice. You'll answer a short setup interview, then share a link with the departing employee.
            </p>
          </div>
        ) : (
          <ul className="space-y-3">
            {handovers.map((h) => (
              <HandoverCard
                key={h.stage1_session_id}
                handover={h}
                onOpen={open}
                onDownload={download}
                busy={busyId === h.stage1_session_id}
              />
            ))}
          </ul>
        )}
      </main>
    </div>
  )
}
