import { useEffect, useState, useRef } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'
import ChatWindow from '../components/ChatWindow'
import SessionComplete from '../components/SessionComplete'
import useChat from '../hooks/useChat'
import { getSessionStatus, storeToken, getToken } from '../api/client'

export default function Stage2Page() {
  // The employee's own session id. The manager created this session and shared
  // the link with a token in it; the employee's browser no longer creates the
  // session, because doing so meant the link carried the manager's session id.
  const { sessionId } = useParams()
  const [searchParams] = useSearchParams()

  const [initError, setInitError] = useState(null)
  const [initializing, setInitializing] = useState(true)
  const initRef = useRef(false)

  const {
    messages,
    loading,
    sessionComplete,
    currentBlock,
    riskFlagCount,
    error,
    sendMessage,
    addAgentMessage,
    setCurrentBlock,
  } = useChat(sessionId)

  useEffect(() => {
    if (initRef.current) return
    initRef.current = true

    // Take the token out of the URL and keep it for the rest of the session,
    // then strip it from the address bar so it does not linger in history, in
    // a screenshot, or in a Referer header on any outbound link.
    const urlToken = searchParams.get('t')
    if (urlToken) {
      storeToken(sessionId, urlToken)
      window.history.replaceState({}, '', window.location.pathname)
    }

    if (!getToken(sessionId)) {
      setInitError(
        'This interview link is incomplete. Ask your manager to resend it.'
      )
      setInitializing(false)
      return
    }

    async function init() {
      try {
        // The opening question was produced when the manager created this
        // session, so it lives in the graph state rather than in a response
        // the employee ever saw. This also restores the last question after a
        // page refresh.
        const status = await getSessionStatus(sessionId)
        if (status.last_agent_message) addAgentMessage(status.last_agent_message)
        setCurrentBlock(status.current_block || 'role_orientation')
      } catch (err) {
        setInitError(err.message)
      } finally {
        setInitializing(false)
      }
    }

    init()
  }, [sessionId, searchParams, addAgentMessage, setCurrentBlock])

  if (initializing) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="text-center animate-page-in">
          <div className="w-8 h-8 border-2 border-keeper-500/30 border-t-keeper-500 rounded-full animate-spin mx-auto mb-4" />
          <p className="text-sm text-parchment-500">Preparing your interview...</p>
        </div>
      </div>
    )
  }

  if (initError) {
    return (
      <div className="min-h-screen flex items-center justify-center px-6">
        <div className="max-w-md text-center animate-page-in">
          <div className="w-16 h-16 rounded-2xl bg-red-50 flex items-center justify-center mx-auto mb-4">
            <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="#dc2626" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="10" />
              <line x1="12" y1="8" x2="12" y2="12" />
              <line x1="12" y1="16" x2="12.01" y2="16" />
            </svg>
          </div>
          <h2 className="font-display text-xl text-ink-heading mb-2">Unable to Start Interview</h2>
          <p className="text-sm text-parchment-500">{initError}</p>
        </div>
      </div>
    )
  }

  return (
    <ChatWindow
      messages={messages}
      onSend={sendMessage}
      loading={loading}
      stage={2}
      currentBlock={currentBlock}
      riskFlagCount={riskFlagCount}
      sessionComplete={sessionComplete}
      title="Stage 2 — Employee Interview"
    >
      {sessionComplete && <SessionComplete stage={2} sessionId={sessionId} />}
      {error && (
        <div className="px-6 py-3 bg-red-50 text-red-700 text-sm">
          {error}
        </div>
      )}
    </ChatWindow>
  )
}
