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

// ---- Manager account (magic-link sign-in) ----
//
// The account token lives in localStorage so a manager stays signed in across
// tabs and visits (it expires server-side after ACCOUNT_SESSION_DAYS). It never
// opens a session by itself: the dashboard exchanges it, per handover, for the
// same session-scoped manager token the rest of the app already uses.
const ACCOUNT_KEY = 'kk_account'

export class SignedOutError extends Error {}

export function getAccount() {
  if (DEMO_MODE) return { token: 'demo', email: 'demo@example.com', expiresAt: null }
  try {
    const raw = localStorage.getItem(ACCOUNT_KEY)
    if (!raw) return null
    const account = JSON.parse(raw)
    if (!account?.token || (account.expiresAt && account.expiresAt * 1000 < Date.now())) {
      localStorage.removeItem(ACCOUNT_KEY)
      return null
    }
    return account
  } catch {
    return null
  }
}

function saveAccount(account) {
  try {
    localStorage.setItem(ACCOUNT_KEY, JSON.stringify(account))
  } catch {
    // Private mode etc. — signed in for this page only.
  }
}

export function signOut() {
  try {
    localStorage.removeItem(ACCOUNT_KEY)
  } catch {
    // nothing to clear
  }
}

async function accountRequest(url, options = {}) {
  const account = getAccount()
  if (!account) throw new SignedOutError('Please sign in')
  const res = await fetch(`${BASE}${url}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${account.token}`,
      ...(options.headers || {}),
    },
  })
  if (res.status === 401) {
    signOut()
    throw new SignedOutError('Your sign-in has expired. Please sign in again.')
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail || `Request failed: ${res.status}`)
  }
  return res
}

export async function requestSignInLink(email) {
  const res = await fetch(`${BASE}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email }),
  })
  const body = await res.json().catch(() => ({}))
  if (!res.ok) {
    const detail = Array.isArray(body.detail) ? 'Enter a valid email address' : body.detail
    throw new Error(detail || `Request failed: ${res.status}`)
  }
  return body.message
}

export async function completeSignIn(linkToken) {
  const res = await fetch(`${BASE}/auth/verify`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token: linkToken }),
  })
  const body = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(body.detail || 'This sign-in link did not work.')
  const account = { token: body.account_token, email: body.email, expiresAt: body.expires_at }
  saveAccount(account)
  return account
}

export async function listHandovers() {
  if (DEMO_MODE) return []
  const res = await accountRequest('/handovers')
  return res.json()
}

/**
 * Exchange the account token for a manager token for one handover, and keep it
 * where the Stage 1 page and the completion panel expect to find it.
 */
export async function openHandover(stage1SessionId) {
  const res = await accountRequest(`/handovers/${stage1SessionId}/access`, { method: 'POST' })
  const data = await res.json()
  storeToken(data.session_id, data.token)
  return data
}

export async function createStage1Session() {
  if (DEMO_MODE) return mock.createStage1Session()
  const res = await accountRequest('/sessions/stage1', { method: 'POST' })
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
/** One status read for a document, including whether it was emailed. */
export async function getDocumentStatus(documentId, authSessionId) {
  if (DEMO_MODE) return { document_id: documentId, status: 'complete', delivery: null }
  const res = await request(`/documents/${documentId}/status`, { sessionId: authSessionId })
  return res.json()
}

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
