/**
 * T-Music API Client
 * Auth relies on HttpOnly session cookie (same-origin). No JWT in localStorage.
 */

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
  const response = await fetch(url, config)

  if (response.status === 401) {
    window.dispatchEvent(new CustomEvent('tmusic:unauthorized'))
    throw new Error('Authentication required')
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
