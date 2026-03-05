import { useState } from 'react'
import { generateDocument } from '../api/client'

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

function Stage1Complete({ sessionId, profile }) {
  const [copied, setCopied] = useState(false)
  const stage2Link = `${window.location.origin}/stage2/${sessionId}`

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

      {/* Copy link */}
      <div className="flex items-center gap-2">
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
    </div>
  )
}

function Stage2Complete({ sessionId }) {
  const [format, setFormat] = useState('docx')
  const [generating, setGenerating] = useState(false)
  const [downloadUrl, setDownloadUrl] = useState(null)
  const [error, setError] = useState(null)

  async function handleGenerate() {
    setGenerating(true)
    setError(null)
    try {
      const data = await generateDocument(sessionId, format)
      setDownloadUrl(data.download_url)
    } catch (err) {
      setError(err.message)
    } finally {
      setGenerating(false)
    }
  }

  return (
    <div className="p-6 animate-page-in">
      <CheckmarkIcon />
      <h2 className="font-display text-xl text-ink-heading text-center mb-2">
        Interview Complete
      </h2>
      <p className="text-sm text-parchment-500 text-center mb-6">
        Thank you for sharing your knowledge. Generate your handover document below.
      </p>

      {!downloadUrl ? (
        <div className="space-y-4">
          {/* Format picker */}
          <div className="flex gap-3 justify-center">
            {['docx', 'pdf'].map((f) => (
              <button
                key={f}
                onClick={() => setFormat(f)}
                className={`px-5 py-2.5 rounded-lg text-sm font-medium border transition-colors ${
                  format === f
                    ? 'border-keeper-500 bg-keeper-500 text-white'
                    : 'border-parchment-300 bg-white text-ink hover:border-keeper-400'
                }`}
              >
                {f.toUpperCase()}
              </button>
            ))}
          </div>

          <button
            onClick={handleGenerate}
            disabled={generating}
            className="w-full py-3 rounded-xl bg-keeper-500 text-white font-medium hover:bg-keeper-400 transition-colors disabled:opacity-60 disabled:cursor-not-allowed flex items-center justify-center gap-2"
          >
            {generating ? (
              <>
                <span className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                Generating document...
              </>
            ) : (
              'Generate Document'
            )}
          </button>
        </div>
      ) : (
        <a
          href={downloadUrl}
          download={`KnowledgeKeeper-Handover.${format === 'pdf' ? 'pdf' : 'md'}`}
          className="block w-full py-3 rounded-xl bg-keeper-500 text-white font-medium text-center hover:bg-keeper-400 transition-colors"
        >
          Download {format.toUpperCase()}
        </a>
      )}

      {error && (
        <p className="mt-3 text-sm text-red-600 text-center">{error}</p>
      )}
    </div>
  )
}

export default function SessionComplete({ stage, sessionId, profile }) {
  if (stage === 1) {
    return <Stage1Complete sessionId={sessionId} profile={profile} />
  }
  return <Stage2Complete sessionId={sessionId} />
}
