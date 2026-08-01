import { useEffect, useRef, useState } from 'react'
import {
  awaitDocument,
  createStage2Session,
  getDownloadUrl,
  getSessionStatus,
} from '../api/client'

const POLL_INTERVAL_MS = 5000

function CheckmarkIcon() {
  return (
    <svg width="48" height="48" viewBox="0 0 48 48" className="mx-auto mb-4">
      <circle cx="24" cy="24" r="22" fill="none" stroke="#2a9d8f" strokeWidth="2" opacity="0.2" />
      <circle cx="24" cy="24" r="22" fill="none" stroke="#2a9d8f" strokeWidth="2"
        strokeDasharray="138" strokeDashoffset="0"
        className="animate-check-draw" />
      <path
        d="M15 24l6 6 12-12"
        fill="none" stroke="#1b6b61" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"
        className="checkmark-path animate-check-draw"
      />
    </svg>
  )
}

/**
 * Manager's view once Stage 1 is confirmed.
 *
 * This screen now creates the employee's session and mints their token, then
 * watches that session. Previously it shared a link built from the MANAGER's
 * session id, which handed the employee a credential for the manager's own
 * interview.
 */
function Stage1Complete({ sessionId, profile }) {
  const [copied, setCopied] = useState(false)
  const [link, setLink] = useState(null)
  const [stage2SessionId, setStage2SessionId] = useState(null)
  const [linkError, setLinkError] = useState(null)

  const [employeeComplete, setEmployeeComplete] = useState(false)
  const [downloadUrl, setDownloadUrl] = useState(null)
  const [documentError, setDocumentError] = useState(null)

  const createdRef = useRef(false)

  useEffect(() => {
    if (createdRef.current) return
    createdRef.current = true

    createStage2Session(sessionId)
      .then((data) => {
        setStage2SessionId(data.session_id)
        setLink(
          `${window.location.origin}/stage2/${data.session_id}` +
            `?t=${encodeURIComponent(data.employee_token)}`
        )
      })
      .catch((err) => setLinkError(err.message))
  }, [sessionId])

  // Watch the employee's interview. Stage 3 now starts server-side the moment
  // it completes, so the manager can collect the pack even if the employee
  // closed the tab.
  useEffect(() => {
    if (!stage2SessionId || downloadUrl) return

    let cancelled = false
    let timer

    async function poll() {
      try {
        const status = await getSessionStatus(stage2SessionId, sessionId)
        if (cancelled) return

        if (status.session_complete) setEmployeeComplete(true)

        if (status.generation_error) {
          setDocumentError(
            `The handover pack could not be generated: ${status.generation_error}`
          )
          return
        }

        if (status.document_id) {
          const doc = await awaitDocument(status.document_id, sessionId)
          if (cancelled) return
          setDownloadUrl(getDownloadUrl(doc.document_id, sessionId))
          return
        }
      } catch (err) {
        if (!cancelled) setDocumentError(err.message)
      }
      if (!cancelled) timer = setTimeout(poll, POLL_INTERVAL_MS)
    }

    poll()
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [stage2SessionId, sessionId, downloadUrl])

  function copyLink() {
    if (!link) return
    navigator.clipboard.writeText(link).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  return (
    <div className="p-6 animate-page-in">
      <CheckmarkIcon />
      <h2 className="font-display text-xl text-ink-heading text-center mb-2">
        Interview Setup Complete
      </h2>
      <p className="text-sm text-parchment-500 text-center mb-6">
        The Role Intelligence Profile has been generated. Share the link below with the departing employee to begin their interview.
      </p>

      {profile && (
        <div className="bg-parchment-50 border border-parchment-200 rounded-xl p-4 mb-6 text-sm space-y-1.5">
          <div><span className="text-parchment-500">Role:</span> <span className="text-ink font-medium">{profile.job_title}</span></div>
          <div><span className="text-parchment-500">Department:</span> <span className="text-ink">{profile.department}</span></div>
          <div><span className="text-parchment-500">Type:</span> <span className="text-ink capitalize">{profile.role_type}</span></div>
          <div><span className="text-parchment-500">Priority 1:</span> <span className="text-ink">{profile.priority_1}</span></div>
          <div><span className="text-parchment-500">Priority 2:</span> <span className="text-ink">{profile.priority_2}</span></div>
          <div><span className="text-parchment-500">Priority 3:</span> <span className="text-ink">{profile.priority_3}</span></div>
        </div>
      )}

      {linkError ? (
        <p className="text-sm text-red-600 text-center">{linkError}</p>
      ) : !link ? (
        <div className="flex items-center justify-center gap-2 py-3 text-sm text-parchment-500">
          <span className="w-4 h-4 border-2 border-keeper-500/30 border-t-keeper-500 rounded-full animate-spin" />
          Preparing the employee's link...
        </div>
      ) : (
        <>
          <div className="flex items-center gap-2">
            <input
              type="text"
              value={link}
              readOnly
              className="flex-1 px-3 py-2.5 rounded-lg border border-parchment-300 bg-white text-sm text-ink truncate"
            />
            <button
              onClick={copyLink}
              className="flex-shrink-0 px-4 py-2.5 rounded-lg bg-keeper-500 text-white text-sm font-medium hover:bg-keeper-400 transition-colors"
            >
              {copied ? 'Copied!' : 'Copy Link'}
            </button>
          </div>
          <p className="mt-2 text-xs text-parchment-500">
            This link is personal to the employee and expires. Keep this page — the
            completed handover pack appears here when their interview finishes.
          </p>
        </>
      )}

      {/* Handover pack */}
      {link && (
        <div className="mt-6 pt-6 border-t border-parchment-200">
          {downloadUrl ? (
            <a
              href={downloadUrl}
              className="block w-full py-3 rounded-xl bg-keeper-500 text-white font-medium text-center hover:bg-keeper-400 transition-colors"
            >
              Download Handover Pack
            </a>
          ) : documentError ? (
            <p className="text-sm text-red-600 text-center">{documentError}</p>
          ) : (
            <p className="text-sm text-parchment-500 text-center">
              {employeeComplete
                ? 'Interview complete — preparing the handover pack...'
                : 'Waiting for the employee to complete their interview.'}
            </p>
          )}
        </div>
      )}
    </div>
  )
}

/**
 * Employee's view. No generate or download control: the pack contains the Risk
 * Summary, which is the manager's to read.
 */
function Stage2Complete() {
  return (
    <div className="p-6 animate-page-in">
      <CheckmarkIcon />
      <h2 className="font-display text-xl text-ink-heading text-center mb-2">
        Interview Complete
      </h2>
      <p className="text-sm text-parchment-500 text-center">
        Thank you for sharing your knowledge. Your handover pack is being prepared
        and sent to your manager — there is nothing more you need to do.
      </p>
    </div>
  )
}

export default function SessionComplete({ stage, sessionId, profile }) {
  if (stage === 1) {
    return <Stage1Complete sessionId={sessionId} profile={profile} />
  }
  return <Stage2Complete />
}
