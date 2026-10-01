/**
 * T-Music API Client
 * Auth relies on HttpOnly session cookie (same-origin). No JWT in localStorage.
 */
import { createLogger } from '../utils/logger'

const log = createLogger('api')

const BASE_URL = ''

function getCookie(name) {
  const match = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'))
  if (!match) return null
  try {
    return decodeURIComponent(match[1])
  } catch {
    // Malformed cookie value (stale garbage): treat as absent rather than
    // throwing URIError on every mutating request.
    return null
  }
}

export async function request(endpoint, options = {}) {
  const method = (options.method || 'GET').toUpperCase()
  const headers = {
    ...(options.headers || {}),
  }

  // Double-submit CSRF: echo the readable csrf_token cookie on mutations.
  // Bearer-authenticated calls don't need it, but sending it is harmless.
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
    const csrf = getCookie('csrf_token')
    if (csrf && !headers['X-CSRF-Token']) {
      headers['X-CSRF-Token'] = csrf
    }
  }

  const config = {
    ...options,
    headers,
    credentials: 'same-origin',
  }

  const url = endpoint.startsWith('http') ? endpoint : `${BASE_URL}${endpoint}`
  const started = performance.now()
  let response
  try {
    response = await fetch(url, config)
  } catch (networkErr) {
    log.error(`${method} ${endpoint} network error`, {
      error: networkErr,
      duration_ms: Math.round(performance.now() - started),
    })
    throw networkErr
  }
  const durationMs = Math.round(performance.now() - started)

  const logLine = { method, endpoint, status: response.status, duration_ms: durationMs }
  if (response.status >= 500) log.error(`${method} ${endpoint} -> ${response.status}`, logLine)
  else if (response.status >= 400) log.warn(`${method} ${endpoint} -> ${response.status}`, logLine)
  else log.debug(`${method} ${endpoint} -> ${response.status}`, logLine)

  if (response.status === 401) {
    window.dispatchEvent(new CustomEvent('tmusic:unauthorized'))
    throw new Error('Authentication required')
  }

  // Fail-closed CSRF recovery: the server rejects cookie-authenticated
  // mutations whose csrf_token cookie is gone (403 code=csrf_cookie_missing).
  // Re-arm the cookie once via GET /api/auth/csrf and replay the request.
  // Only replayed for method+endpoint — body is reused as-is (no streams).
  if (
    response.status === 403 &&
    !options.headers?.['X-CSRF-Token-Replayed'] &&
    !['GET', 'HEAD', 'OPTIONS'].includes(method)
  ) {
    let errCode = null
    try {
      errCode = (await response.clone().json())?.code || null
    } catch {
      errCode = null
    }
    if (errCode === 'csrf_cookie_missing') {
      const rearm = await fetch('/api/auth/csrf', { credentials: 'same-origin' })
      if (rearm.ok) {
        return request(endpoint, {
          ...options,
          headers: { ...headers, 'X-CSRF-Token-Replayed': '1' },
        })
      }
    }
  }

  if (response.status === 429) {
    const retryAfter = response.headers.get('Retry-After') || '60'
    const errorDetail = `Rate limit exceeded. Retry after ${retryAfter} seconds.`
    await new Promise(r => setTimeout(r, 1000 * parseInt(retryAfter, 10)))
    throw new Error(errorDetail)
  }

  if (!response.ok) {
    let errorDetail = 'Request failed'
    try {
      const errJson = await response.json()
      errorDetail = errJson.detail || errorDetail
    } catch {
      // Non-JSON error
    }
    throw new Error(errorDetail)
  }

  if (response.status === 204) {
    return null
  }

  const contentType = response.headers.get('content-type')
  if (contentType && contentType.includes('application/json')) {
    return await response.json()
  }

  return response
}
