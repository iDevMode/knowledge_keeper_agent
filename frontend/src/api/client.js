import * as mock from './mock.js'

const BASE = '/api'

// Set to true to force demo mode (no backend needed)
// Set to false to use the real backend on port 8321
const DEMO_MODE = true

async function request(url, options = {}) {
  const res = await fetch(`${BASE}${url}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail || `Request failed: ${res.status}`)
  }
  return res
}

export async function createStage1Session() {
  if (DEMO_MODE) return mock.createStage1Session()
  const res = await request('/sessions/stage1', { method: 'POST' })
  return res.json()
}

export async function createStage2Session(stage1SessionId) {
  if (DEMO_MODE) return mock.createStage2Session(stage1SessionId)
  const res = await request('/sessions/stage2', {
    method: 'POST',
    body: JSON.stringify({ stage1_session_id: stage1SessionId }),
  })
  return res.json()
}

export async function sendMessage(sessionId, message) {
  if (DEMO_MODE) return mock.sendMessage(sessionId, message)
  const res = await request(`/sessions/${sessionId}/message`, {
    method: 'POST',
    body: JSON.stringify({ message }),
  })
  return res.json()
}

export async function getSessionStatus(sessionId) {
  if (DEMO_MODE) return mock.getSessionStatus(sessionId)
  const res = await request(`/sessions/${sessionId}/status`)
  return res.json()
}

export async function generateDocument(sessionId, format = 'docx') {
  if (DEMO_MODE) return mock.generateDocument(sessionId, format)
  const res = await request(`/sessions/${sessionId}/generate`, {
    method: 'POST',
    body: JSON.stringify({ format }),
  })
  return res.json()
}

export function getDownloadUrl(documentId) {
  if (DEMO_MODE) return mock.getDownloadUrl(documentId)
  return `${BASE}/documents/${documentId}`
}
