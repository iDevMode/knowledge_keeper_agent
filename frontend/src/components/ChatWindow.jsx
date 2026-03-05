import { useRef, useEffect } from 'react'
import ChatMessage from './ChatMessage'
import ChatInput from './ChatInput'
import ProgressBar from './ProgressBar'
import LoadingDots from './LoadingDots'

export default function ChatWindow({
  messages,
  onSend,
  loading,
  stage,
  currentBlock,
  riskFlagCount,
  sessionComplete,
  title,
  children,
}) {
  const messagesEndRef = useRef(null)

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading])

  return (
    <div className="flex flex-col h-screen max-w-3xl mx-auto">
      {/* Header */}
      <header className="flex items-center justify-between px-6 py-4 border-b border-parchment-200 bg-white/60 backdrop-blur-sm">
        <div>
          <h1 className="font-display text-xl text-ink-heading">KnowledgeKeeper</h1>
          <p className="text-xs text-parchment-500 mt-0.5">{title}</p>
        </div>
        <div className="w-8 h-8 rounded-full bg-keeper-500 flex items-center justify-center">
          <span className="text-white text-xs font-semibold">{stage === 1 ? 'S1' : 'S2'}</span>
        </div>
      </header>

      {/* Progress */}
      {!sessionComplete && (
        <ProgressBar
          stage={stage}
          currentBlock={currentBlock}
          riskFlagCount={riskFlagCount}
        />
      )}

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
        {messages.map((msg, i) => (
          <ChatMessage key={i} role={msg.role} content={msg.content} />
        ))}
        {loading && (
          <div className="flex justify-start animate-message-in">
            <div className="bg-parchment-100 rounded-2xl rounded-bl-md">
              <LoadingDots />
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Session complete content or input */}
      {sessionComplete ? (
        <div className="border-t border-parchment-200 bg-white/60 backdrop-blur-sm">
          {children}
        </div>
      ) : (
        <ChatInput onSend={onSend} disabled={loading} />
      )}
    </div>
  )
}
