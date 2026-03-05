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

  const sendMessage = useCallback(
    async (text) => {
      if (!sessionId || loading || sessionComplete) return

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
      } catch (err) {
        setError(err.message)
        // Remove the user message on error so they can retry
        setMessages((prev) => prev.slice(0, -1))
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
