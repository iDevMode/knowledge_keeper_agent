import * as mock from './mock.js'

const BASE = '/api'

// Control via VITE_DEMO_MODE env var. Defaults to false (use real backend).
const DEMO_MODE = import.meta.env.VITE_DEMO_MODE === 'true'

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

  // Start generation (returns immediately)
  const res = await request(`/sessions/${sessionId}/generate`, {
    method: 'POST',
    body: JSON.stringify({ format }),
  })
  const data = await res.json()

  // Poll until complete
  const documentId = data.document_id
  while (true) {
    await new Promise((resolve) => setTimeout(resolve, 3000))
    const statusRes = await request(`/documents/${documentId}/status`)
    const status = await statusRes.json()

    if (status.status === 'complete') {
      return { document_id: documentId, download_url: status.download_url }
    }
    if (status.status === 'failed') {
      throw new Error(status.error || 'Document generation failed')
    }
    // Still generating — keep polling
  }
}

export function getDownloadUrl(documentId) {
  if (DEMO_MODE) return mock.getDownloadUrl(documentId)
  return `${BASE}/documents/${documentId}`
}
