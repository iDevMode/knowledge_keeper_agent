import { useEffect, useState, useRef } from 'react'
import { useParams } from 'react-router-dom'
import ChatWindow from '../components/ChatWindow'
import SessionComplete from '../components/SessionComplete'
import ConsentScreen from '../components/ConsentScreen'
import useChat from '../hooks/useChat'
import { createStage2Session } from '../api/client'

export default function Stage2Page() {
  const { inviteToken } = useParams()
  const [sessionId, setSessionId] = useState(() => {
    return sessionStorage.getItem(`stage2_session_${inviteToken}`)
  })
  const [initError, setInitError] = useState(null)
  const [initializing, setInitializing] = useState(false)
  const initRef = useRef(false)

  const {
    messages,
    loading,
    sessionComplete,
    currentBlock,
    progress,
    error,
    sendMessage,
    addAgentMessage,
    restoreHistory,
    setCurrentBlock,
  } = useChat(sessionId)

  // Session creation is deferred until the employee gives consent.
  async function handleConsent() {
    if (initRef.current || sessionId) return
    initRef.current = true
    setInitializing(true)
    try {
      const data = await createStage2Session(inviteToken, true)
      setSessionId(data.session_id)
      sessionStorage.setItem(`stage2_session_${inviteToken}`, data.session_id)
      addAgentMessage(data.message)
      setCurrentBlock('role_orientation')
    } catch (err) {
      setInitError(err.message)
    } finally {
      setInitializing(false)
    }
  }

  // Handle page refresh / later sitting — session exists in storage but the
  // in-memory transcript is gone. Restore it from the server.
  const restoreRef = useRef(false)
  useEffect(() => {
    if (sessionId && messages.length === 0 && !initializing && !initRef.current && !restoreRef.current) {
      restoreRef.current = true
      restoreHistory().then((restored) => {
        if (!restored) {
          addAgentMessage('Welcome back. Please continue where you left off by sending a message.')
        }
      })
    }
  }, [sessionId, messages.length, initializing, restoreHistory, addAgentMessage])

  // Fresh employee (no session yet) must consent before the interview starts.
  // Returning employees (session in storage) skip straight to their transcript.
  if (!sessionId && !initError) {
    return <ConsentScreen onAccept={handleConsent} loading={initializing} />
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
      progress={progress}
      sessionComplete={sessionComplete}
      title="Stage 2 — Employee Interview"
    >
      {sessionComplete && (
        <SessionComplete stage={2} sessionId={sessionId} />
      )}
      {error && (
        <div className="px-6 py-3 bg-red-50 text-red-700 text-sm">
          {error}
        </div>
      )}
    </ChatWindow>
  )
}
