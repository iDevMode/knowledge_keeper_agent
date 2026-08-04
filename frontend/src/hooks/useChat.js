import { useState, useCallback } from 'react'
import { sendMessage as sendMessageApi, getSessionStatus } from '../api/client'

export default function useChat(sessionId) {
  const [messages, setMessages] = useState([])
  const [loading, setLoading] = useState(false)
  const [sessionComplete, setSessionComplete] = useState(false)
  const [profile, setProfile] = useState(null)
  const [currentBlock, setCurrentBlock] = useState(null)
  const [riskFlagCount, setRiskFlagCount] = useState(0)
  const [error, setError] = useState(null)

  const addAgentMessage = useCallback((content) => {
    setMessages((prev) => [...prev, { role: 'agent', content }])
  }, [])

  /**
   * Returns true if the message reached the agent, false if it did not.
   * ChatInput uses that to put the text back in the box rather than lose it:
   * on failure the user message is removed from the transcript, so without
   * this the words are gone from both places and the send looks like a no-op.
   */
  const sendMessage = useCallback(
    async (text) => {
      if (!sessionId) {
        // Was a silent return. Nothing is more confusing than a send that
        // clears the box and does nothing at all.
        setError('This session is not loaded properly. Try reopening your link.')
        return false
      }
      if (loading || sessionComplete) return false

      setMessages((prev) => [...prev, { role: 'user', content: text }])
      setLoading(true)
      setError(null)

      try {
        const data = await sendMessageApi(sessionId, text)
        setMessages((prev) => [...prev, { role: 'agent', content: data.message }])

        if (data.session_complete) {
          setSessionComplete(true)
          if (data.profile) {
            setProfile(data.profile)
          }
        }

        // Fetch updated status for progress tracking
        try {
          const status = await getSessionStatus(sessionId)
          setCurrentBlock(status.current_block)
          if (status.risk_flag_count != null) {
            setRiskFlagCount(status.risk_flag_count)
          }
        } catch {
          // Non-critical — don't block chat for status failures
        }
        return true
      } catch (err) {
        setError(err.message || 'Something went wrong sending that message.')
        // Drop the user message again: the agent never received it, so leaving
        // it in the transcript would show a turn that did not happen. The text
        // itself is not lost — returning false puts it back in the input.
        setMessages((prev) => prev.slice(0, -1))
        return false
      } finally {
        setLoading(false)
      }
    },
    [sessionId, loading, sessionComplete]
  )

  return {
    messages,
    loading,
    sessionComplete,
    profile,
    currentBlock,
    riskFlagCount,
    error,
    sendMessage,
    addAgentMessage,
    setCurrentBlock,
  }
}
