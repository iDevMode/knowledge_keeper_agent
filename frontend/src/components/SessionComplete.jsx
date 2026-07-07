import { useState } from 'react'
import { Link } from 'react-router-dom'

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

function Stage1Complete({ sessionId, profile, inviteToken, managerToken }) {
  const [copied, setCopied] = useState(false)

  // The employee link carries a single-use invite token — the Stage 1 session
  // ID must never appear in it. The manager token is kept in this browser so
  // the manager can return to their dashboard to collect the handover.
  const stage2Link = inviteToken ? `${window.location.origin}/stage2/i/${inviteToken}` : null
  if (managerToken) {
    localStorage.setItem(`kk_manager_token_${sessionId}`, managerToken)
  }

  function copyLink() {
    navigator.clipboard.writeText(stage2Link).then(() => {
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

      {/* Profile summary */}
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

      {/* Copy employee link */}
      {stage2Link && (
        <div className="flex items-center gap-2 mb-6">
          <input
            type="text"
            value={stage2Link}
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
      )}

      {/* Manager dashboard link */}
      <div className="bg-parchment-50 border border-parchment-200 rounded-xl p-4 text-sm">
        <p className="text-parchment-500 mb-2">
          Once the employee finishes their interview, the handover document will be
          compiled automatically. Track progress and download it from your dashboard:
        </p>
        <Link
          to={`/manager/${sessionId}`}
          className="inline-block px-4 py-2 rounded-lg border border-keeper-500 text-keeper-500 text-sm font-medium hover:bg-keeper-500 hover:text-white transition-colors"
        >
          Open Manager Dashboard
        </Link>
        <p className="text-xs text-parchment-400 mt-2">
          Bookmark this page — the dashboard is tied to this browser.
        </p>
      </div>
    </div>
  )
}

function Stage2Complete() {
  return (
    <div className="p-6 animate-page-in">
      <CheckmarkIcon />
      <h2 className="font-display text-xl text-ink-heading text-center mb-2">
        Interview Complete
      </h2>
      <p className="text-sm text-parchment-500 text-center">
        Thank you for sharing your knowledge — it will make a real difference to
        whoever steps into your role next.
      </p>
      <p className="text-sm text-parchment-500 text-center mt-3">
        Your answers are now being compiled into a handover document, which will be
        shared with the designated recipients. There's nothing else you need to do —
        you can close this window.
      </p>
    </div>
  )
}

export default function SessionComplete({ stage, sessionId, profile, inviteToken, managerToken }) {
  if (stage === 1) {
    return (
      <Stage1Complete
        sessionId={sessionId}
        profile={profile}
        inviteToken={inviteToken}
        managerToken={managerToken}
      />
    )
  }
  return <Stage2Complete />
}
