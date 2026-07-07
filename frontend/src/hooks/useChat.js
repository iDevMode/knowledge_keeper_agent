import { useState, useCallback } from 'react'
import { sendMessage as sendMessageApi, getSessionStatus, getSessionHistory } from '../api/client'

export default function useChat(sessionId) {
  const [messages, setMessages] = useState([])
  const [loading, setLoading] = useState(false)
  const [sessionComplete, setSessionComplete] = useState(false)
  const [profile, setProfile] = useState(null)
  const [inviteToken, setInviteToken] = useState(null)
  const [managerToken, setManagerToken] = useState(null)
  const [currentBlock, setCurrentBlock] = useState(null)
  const [progress, setProgress] = useState(null)
  const [error, setError] = useState(null)

  const addAgentMessage = useCallback((content) => {
    setMessages((prev) => [...prev, { role: 'agent', content }])
  }, [])

  // Restore an in-progress conversation after a refresh or a later sitting.
  // Returns true if a transcript was restored, so callers can skip re-greeting.
  const restoreHistory = useCallback(async () => {
    if (!sessionId) return false
    try {
      const data = await getSessionHistory(sessionId)
      if (data.messages && data.messages.length > 0) {
        setMessages(data.messages.map((m) => ({ role: m.role, content: m.content })))
        if (data.session_complete) setSessionComplete(true)
        if (data.current_block) setCurrentBlock(data.current_block)
        return true
      }
    } catch {
      // Non-critical — fall back to whatever the page does without history
    }
    return false
  }, [sessionId])

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
          if (data.invite_token) {
            setInviteToken(data.invite_token)
          }
          if (data.manager_token) {
            setManagerToken(data.manager_token)
          }
        }

        // Fetch updated status for progress tracking
        try {
          const status = await getSessionStatus(sessionId)
          setCurrentBlock(status.current_block)
          if (status.progress) setProgress(status.progress)
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
    inviteToken,
    managerToken,
    currentBlock,
    progress,
    error,
    sendMessage,
    addAgentMessage,
    restoreHistory,
    setCurrentBlock,
  }
}
