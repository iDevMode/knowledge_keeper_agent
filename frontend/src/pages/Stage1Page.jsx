import { useEffect, useState } from 'react'
import { useParams, useLocation } from 'react-router-dom'
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
  } = useChat(sessionId)

  useEffect(() => {
    if (initialized) return
    setInitialized(true)

    const greeting = location.state?.greeting
    if (greeting) {
      addAgentMessage(greeting)
      setCurrentBlock('business_context')
    }
  }, [initialized, location.state, addAgentMessage, setCurrentBlock])

  return (
    <ChatWindow
      messages={messages}
      onSend={sendMessage}
      loading={loading}
      stage={1}
      currentBlock={currentBlock}
      sessionComplete={sessionComplete}
      title="Stage 1 — Interview Setup"
    >
      {sessionComplete && (
        <SessionComplete
          stage={1}
          sessionId={sessionId}
          profile={profile}
        />
      )}
      {error && (
        <div className="px-6 py-3 bg-red-50 text-red-700 text-sm">
          {error}
        </div>
      )}
    </ChatWindow>
  )
}
