import * as mock from './mock.js'

const BASE = '/api'

// Control via VITE_DEMO_MODE env var. Defaults to false (use real backend).
const DEMO_MODE = import.meta.env.VITE_DEMO_MODE === 'true'

// Session tokens (backend finding H5). A session id is no longer a credential:
// every call but session creation needs a stage-scoped signed token. Held in
// sessionStorage so a refresh does not end the interview, and scoped per
// session so a manager and an employee can use the same browser without
// clobbering each other.
const TOKEN_KEY = (sessionId) => `kk_token_${sessionId}`

export function storeToken(sessionId, token) {
  if (sessionId && token) sessionStorage.setItem(TOKEN_KEY(sessionId), token)
}

export function getToken(sessionId) {
  return sessionId ? sessionStorage.getItem(TOKEN_KEY(sessionId)) : null
}

async function request(url, { sessionId, ...options } = {}) {
  const headers = { 'Content-Type': 'application/json', ...(options.headers || {}) }

  const token = getToken(sessionId)
  if (token) headers.Authorization = `Bearer ${token}`

  const res = await fetch(`${BASE}${url}`, { ...options, headers })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    if (res.status === 401 || res.status === 403) {
      throw new Error(
        body.detail ||
          'This link is no longer valid. Ask whoever shared it to send a new one.'
      )
    }
    throw new Error(body.detail || `Request failed: ${res.status}`)
  }
  return res
}

export async function createStage1Session() {
  if (DEMO_MODE) return mock.createStage1Session()
  const res = await request('/sessions/stage1', { method: 'POST' })
  const data = await res.json()
  storeToken(data.session_id, data.token)
  return data
}

/**
 * Called by the MANAGER once Stage 1 is confirmed. Returns the employee's
 * session and its token, which the manager forwards in the interview link.
 * The employee's browser no longer creates this session — doing so required
 * handing them the manager's session id.
 */
export async function createStage2Session(stage1SessionId) {
  if (DEMO_MODE) return mock.createStage2Session(stage1SessionId)
  const res = await request('/sessions/stage2', {
    method: 'POST',
    sessionId: stage1SessionId,
    body: JSON.stringify({ stage1_session_id: stage1SessionId }),
  })
  // Deliberately NOT stored here: this runs in the manager's browser, and the
  // employee token belongs in the employee's. The manager only forwards it.
  return res.json()
}

export async function sendMessage(sessionId, message) {
  if (DEMO_MODE) return mock.sendMessage(sessionId, message)
  const res = await request(`/sessions/${sessionId}/message`, {
    method: 'POST',
    sessionId,
    body: JSON.stringify({ message }),
  })
  return res.json()
}

/**
 * `authSessionId` lets the MANAGER poll the employee's session with their own
 * token — needed because only a manager token is told a document exists.
 */
export async function getSessionStatus(sessionId, authSessionId = sessionId) {
  if (DEMO_MODE) return mock.getSessionStatus(sessionId)
  const res = await request(`/sessions/${sessionId}/status`, { sessionId: authSessionId })
  return res.json()
}

/**
 * Poll a document to completion. `authSessionId` is the session whose token
 * authorises the document — the MANAGER's, since the document endpoints are
 * manager-only.
 */
export async function awaitDocument(documentId, authSessionId) {
  if (DEMO_MODE) return mock.awaitDocument?.(documentId) ?? { document_id: documentId }

  while (true) {
    const statusRes = await request(`/documents/${documentId}/status`, {
      sessionId: authSessionId,
    })
    const status = await statusRes.json()

    if (status.status === 'complete') {
      return { document_id: documentId, download_url: status.download_url }
    }
    if (status.status === 'failed') {
      throw new Error(status.error || 'Document generation failed')
    }
    await new Promise((resolve) => setTimeout(resolve, 3000))
  }
}

export async function generateDocument(sessionId, format = 'docx', authSessionId = sessionId) {
  if (DEMO_MODE) return mock.generateDocument(sessionId, format)

  const res = await request(`/sessions/${sessionId}/generate`, {
    method: 'POST',
    sessionId: authSessionId,
    body: JSON.stringify({ format }),
  })
  const data = await res.json()
  return awaitDocument(data.document_id, authSessionId)
}

/**
 * Download links are plain <a href>, which cannot carry an Authorization
 * header — the backend accepts the token as a query parameter on this endpoint
 * for exactly that reason.
 */
export function getDownloadUrl(documentId, authSessionId) {
  if (DEMO_MODE) return mock.getDownloadUrl(documentId)
  const token = getToken(authSessionId)
  const suffix = token ? `?token=${encodeURIComponent(token)}` : ''
  return `${BASE}/documents/${documentId}${suffix}`
}
