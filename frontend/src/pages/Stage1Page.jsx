import { useEffect, useState } from 'react'
import { Link, useParams, useLocation } from 'react-router-dom'
import { getSessionStatus, getToken } from '../api/client'
import ChatWindow from '../components/ChatWindow'
import SessionComplete from '../components/SessionComplete'
import useChat from '../hooks/useChat'

export default function Stage1Page() {
  const { sessionId } = useParams()
  const location = useLocation()
  const [initialized, setInitialized] = useState(false)

  const {
    messages,
    loading,
    sessionComplete,
    profile,
    currentBlock,
    error,
    sendMessage,
    addAgentMessage,
    setCurrentBlock,
    resume,
  } = useChat(sessionId)
  const [loadError, setLoadError] = useState(null)

  useEffect(() => {
    if (initialized) return
    setInitialized(true)

    const greeting = location.state?.greeting
    if (greeting) {
      addAgentMessage(greeting)
      setCurrentBlock('business_context')
      return
    }

    // Opened from the dashboard (or refreshed): pick up where it left off.
    if (!getToken(sessionId)) {
      setLoadError('Open this handover from your dashboard.')
      return
    }
    getSessionStatus(sessionId)
      .then(resume)
      .catch((err) => setLoadError(err.message))
  }, [initialized, location.state, addAgentMessage, setCurrentBlock, resume, sessionId])

  if (loadError) {
    return (
      <div className="min-h-screen flex items-center justify-center px-6">
        <div className="text-center max-w-sm animate-page-in">
          <p className="text-sm text-ink-light mb-6">{loadError}</p>
          <Link
            to="/dashboard"
            className="inline-block px-6 py-3 rounded-xl bg-keeper-500 text-white font-medium hover:bg-keeper-400 transition-colors"
          >
            Go to your dashboard
          </Link>
        </div>
      </div>
    )
  }

  return (
    <ChatWindow
      messages={messages}
      onSend={sendMessage}
      loading={loading}
      stage={1}
      currentBlock={currentBlock}
      sessionComplete={sessionComplete}
      error={error}
      title="Stage 1 — Interview Setup"
    >
      {sessionComplete && (
        <SessionComplete
          stage={1}
          sessionId={sessionId}
          profile={profile}
        />
      )}
    </ChatWindow>
  )
}
