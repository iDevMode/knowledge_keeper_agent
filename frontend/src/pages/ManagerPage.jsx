import { useCallback, useEffect, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import { getManagerOverview, managerGenerateDocument, getDownloadUrl } from '../api/client'

const POLL_INTERVAL_MS = 5000

const STAGE2_STATUS_LABELS = {
  not_started: 'Waiting for the employee to start their interview',
  in_progress: 'Employee interview in progress',
  complete: 'Employee interview complete',
}

export default function ManagerPage() {
  const { stage1SessionId } = useParams()
  const token = localStorage.getItem(`kk_manager_token_${stage1SessionId}`)

  const [overview, setOverview] = useState(null)
  const [error, setError] = useState(null)
  const [regenerating, setRegenerating] = useState(false)
  const pollRef = useRef(null)

  const refresh = useCallback(async () => {
    if (!token) return
    try {
      const data = await getManagerOverview(stage1SessionId, token)
      setOverview(data)
      setError(null)
    } catch (err) {
      setError(err.message)
    }
  }, [stage1SessionId, token])

  useEffect(() => {
    refresh()
    pollRef.current = setInterval(refresh, POLL_INTERVAL_MS)
    return () => clearInterval(pollRef.current)
  }, [refresh])

  async function regenerate(format) {
    setRegenerating(true)
    try {
      await managerGenerateDocument(stage1SessionId, token, format)
      await refresh()
    } catch (err) {
      setError(err.message)
    } finally {
      setRegenerating(false)
    }
  }

  if (!token) {
    return (
      <div className="min-h-screen flex items-center justify-center px-6">
        <div className="max-w-md text-center animate-page-in">
          <h2 className="font-display text-xl text-ink-heading mb-2">Dashboard Unavailable</h2>
          <p className="text-sm text-parchment-500">
            This dashboard can only be opened from the browser where the interview
            setup was completed. Please return to the device you used for Stage 1.
          </p>
        </div>
      </div>
    )
  }

  const docReady = overview?.document_status === 'complete' && overview?.document_id

  return (
    <div className="min-h-screen flex items-center justify-center px-6">
      <div className="max-w-lg w-full animate-page-in">
        <h1 className="font-display text-2xl text-ink-heading mb-1">Handover Dashboard</h1>
        <p className="text-sm text-parchment-500 mb-6">
          Progress of the employee interview and the handover document.
        </p>

        {error && (
          <div className="px-4 py-3 mb-4 rounded-lg bg-red-50 text-red-700 text-sm">{error}</div>
        )}

        {/* Interview status */}
        <div className="bg-parchment-50 border border-parchment-200 rounded-xl p-4 mb-4 text-sm">
          <div className="flex items-center justify-between">
            <span className="text-parchment-500">Employee interview</span>
            <span className="text-ink font-medium">
              {STAGE2_STATUS_LABELS[overview?.stage2_status] || 'Loading…'}
            </span>
          </div>
          {overview?.risk_flag_count != null && (
            <div className="flex items-center justify-between mt-2">
              <span className="text-parchment-500">Risk flags identified</span>
              <span className="text-ink font-medium">{overview.risk_flag_count}</span>
            </div>
          )}
        </div>

        {/* Document status */}
        <div className="bg-parchment-50 border border-parchment-200 rounded-xl p-4 text-sm">
          <div className="flex items-center justify-between mb-3">
            <span className="text-parchment-500">Handover document</span>
            <span className="text-ink font-medium">
              {overview?.document_status === 'complete' && 'Ready'}
              {overview?.document_status === 'generating' && 'Generating…'}
              {overview?.document_status === 'failed' && 'Generation failed'}
              {!overview?.document_status && 'Not generated yet'}
            </span>
          </div>

          {overview?.document_status === 'failed' && overview?.document_error && (
            <p className="text-xs text-red-600 mb-3">{overview.document_error}</p>
          )}

          {docReady && (
            <a
              href={getDownloadUrl(overview.document_id, token)}
              className="block w-full py-3 rounded-xl bg-keeper-500 text-white font-medium text-center hover:bg-keeper-400 transition-colors mb-3"
            >
              Download Handover Document
            </a>
          )}

          {overview?.stage2_status === 'complete' && (
            <div className="flex gap-2">
              {['docx', 'pdf'].map((f) => (
                <button
                  key={f}
                  onClick={() => regenerate(f)}
                  disabled={regenerating || overview?.document_status === 'generating'}
                  className="flex-1 px-4 py-2 rounded-lg border border-parchment-300 bg-white text-sm text-ink hover:border-keeper-400 transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
                >
                  {docReady ? `Regenerate as ${f.toUpperCase()}` : `Generate ${f.toUpperCase()}`}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
