import { useEffect, useState, useRef } from 'react'
import { useParams } from 'react-router-dom'
import ChatWindow from '../components/ChatWindow'
import SessionComplete from '../components/SessionComplete'
import useChat from '../hooks/useChat'
import { createStage2Session } from '../api/client'

export default function Stage2Page() {
  const { stage1SessionId } = useParams()
  const [sessionId, setSessionId] = useState(() => {
    return sessionStorage.getItem(`stage2_session_${stage1SessionId}`)
  })
  const [initError, setInitError] = useState(null)
  const [initializing, setInitializing] = useState(!sessionId)
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
    if (initRef.current || sessionId) return
    initRef.current = true

    async function init() {
      try {
        const data = await createStage2Session(stage1SessionId)
        setSessionId(data.session_id)
        sessionStorage.setItem(`stage2_session_${stage1SessionId}`, data.session_id)
        addAgentMessage(data.message)
        setCurrentBlock('role_orientation')
      } catch (err) {
        setInitError(err.message)
      } finally {
        setInitializing(false)
      }
    }

    init()
  }, [stage1SessionId, sessionId, addAgentMessage, setCurrentBlock])

  // Handle page refresh — session exists in storage but no messages loaded
  useEffect(() => {
    if (sessionId && messages.length === 0 && !initializing && !initRef.current) {
      // Session was restored from sessionStorage but messages are gone
      // The user will need to continue from where they left off
      addAgentMessage('Welcome back. Please continue where you left off by sending a message.')
    }
  }, [sessionId, messages.length, initializing, addAgentMessage])

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
