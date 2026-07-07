import * as mock from './mock.js'

const BASE = '/api'

// Control via VITE_DEMO_MODE env var. Defaults to false (use real backend).
// The Vercel preview builds with VITE_DEMO_MODE=true so it runs entirely on
// the canned mock data below — no backend, no API key required.
export const DEMO_MODE = import.meta.env.VITE_DEMO_MODE === 'true'

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

// The employee link carries a single-use invite token — never the manager's
// Stage 1 session ID. Requires explicit consent (GDPR).
export async function createStage2Session(inviteToken, consentAcknowledged = false) {
  if (DEMO_MODE) return mock.createStage2Session(inviteToken, consentAcknowledged)
  const res = await request('/sessions/stage2', {
    method: 'POST',
    body: JSON.stringify({ invite_token: inviteToken, consent_acknowledged: consentAcknowledged }),
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

export async function getSessionHistory(sessionId) {
  if (DEMO_MODE) return mock.getSessionHistory(sessionId)
  const res = await request(`/sessions/${sessionId}/history`)
  return res.json()
}

// ── Manager-facing (require the manager token) ───────────────────────────────

export async function getManagerOverview(stage1SessionId, token) {
  if (DEMO_MODE) return mock.getManagerOverview(stage1SessionId, token)
  const res = await request(
    `/manager/${stage1SessionId}/handover?token=${encodeURIComponent(token)}`
  )
  return res.json()
}

export async function managerGenerateDocument(stage1SessionId, token, format = 'docx') {
  if (DEMO_MODE) return mock.managerGenerateDocument(stage1SessionId, token, format)
  const res = await request(
    `/manager/${stage1SessionId}/generate?token=${encodeURIComponent(token)}`,
    {
      method: 'POST',
      body: JSON.stringify({ format }),
    }
  )
  return res.json()
}

export async function setDeliveryEmail(stage1SessionId, token, email) {
  if (DEMO_MODE) return mock.setDeliveryEmail(stage1SessionId, token, email)
  const res = await request(
    `/manager/${stage1SessionId}/delivery-email?token=${encodeURIComponent(token)}`,
    {
      method: 'POST',
      body: JSON.stringify({ email }),
    }
  )
  return res.json()
}

export async function deleteEngagement(stage1SessionId, token) {
  if (DEMO_MODE) return mock.deleteEngagement(stage1SessionId, token)
  const res = await request(
    `/manager/${stage1SessionId}?token=${encodeURIComponent(token)}`,
    { method: 'DELETE' }
  )
  return res.json()
}

export function getDownloadUrl(documentId, token) {
  if (DEMO_MODE) return mock.getDownloadUrl(documentId, token)
  return `${BASE}/documents/${documentId}?token=${encodeURIComponent(token)}`
}
