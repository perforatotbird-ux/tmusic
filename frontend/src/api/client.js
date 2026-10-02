/**
 * T-Music API Client
 * Auth relies on HttpOnly session cookie (same-origin). No JWT in localStorage.
 */
import { createLogger } from '../utils/logger'
import { retryAfterSeconds } from '../utils/retryAfter'

const log = createLogger('api')

const BASE_URL = ''

export class ApiError extends Error {
  constructor(message, { status = 0, code = 'request_failed', issues = [], retryAfter = null } = {}) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.issues = issues
    this.retryAfter = retryAfter
  }
}

function httpError(status, body) {
  if (status >= 500) return new ApiError('Сервис временно недоступен. Повторите попытку позже.', { status, code: 'server_error' })
  const detail = body?.detail
  const issues = Array.isArray(detail) ? detail.map((item) => ({
    field: Array.isArray(item?.loc) ? item.loc.filter((part) => typeof part === 'string' || typeof part === 'number').join('.') : '',
    message: typeof item?.msg === 'string' ? item.msg.slice(0, 300) : 'Некорректное значение',
  })) : []
  const message = issues.length
    ? `Проверьте введённые данные: ${issues.map((item) => `${item.field}: ${item.message}`).join('; ')}`
    : typeof detail === 'string' ? detail.slice(0, 500)
    : typeof detail?.message === 'string' ? detail.message.slice(0, 500)
    : 'Не удалось выполнить запрос.'
  const code = typeof body?.code === 'string' ? body.code : typeof detail?.code === 'string' ? detail.code : 'request_failed'
  return new ApiError(message, { status, code, issues })
}

async function fetchResponse(url, config) {
  try {
    return await fetch(url, config)
  } catch (error) {
    if (error?.name === 'AbortError') throw error
    throw new ApiError('Не удалось связаться с сервером. Проверьте соединение.', { code: 'network_error' })
  }
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

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
    response = await fetchResponse(url, config)
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
    throw new ApiError('Требуется вход в аккаунт.', { status: 401, code: 'authentication_required' })
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
      const rearm = await fetchResponse('/api/auth/csrf', { credentials: 'same-origin' })
      if (rearm.ok) {
        return request(endpoint, {
          ...options,
          headers: { ...headers, 'X-CSRF-Token-Replayed': '1' },
        })
      }
    }
  }

  if (response.status === 429) {
    const retryAfter = retryAfterSeconds(response.headers.get('Retry-After'))
    // Never block the request for the full Retry-After window: a 60–300s freeze
    // with no feedback is worse than a fast failure. Only auto-retry when the
    // server asks for a short, bounded delay, and cap the number of attempts so
    // we can't loop forever. Otherwise fail fast with a recoverable error.
    const attempt = Number(options.headers?.['X-RateLimit-Attempt'] || 0)
    const SHORT_WAIT_SEC = 3
    const MAX_ATTEMPTS = 2
    if (retryAfter > 0 && retryAfter <= SHORT_WAIT_SEC && attempt < MAX_ATTEMPTS) {
      await delay(Math.min(retryAfter, SHORT_WAIT_SEC) * 1000)
      return request(endpoint, {
        ...options,
        headers: { ...headers, 'X-RateLimit-Attempt': String(attempt + 1) },
      })
    }
    const errorDetail = `Слишком много запросов. Повторите через ${retryAfter} секунд.`
    window.dispatchEvent(new CustomEvent('tmusic:rate-limited', { detail: { retryAfter } }))
    throw new ApiError(errorDetail, { status: 429, code: 'rate_limited', retryAfter })
  }

  if (!response.ok) {
    let body = null
    try { body = await response.json() } catch { /* Non-JSON errors use a safe fallback. */ }
    throw httpError(response.status, body)
  }

  if (response.status === 204) {
    return null
  }

  const contentType = response.headers.get('content-type')
  if (contentType && contentType.includes('application/json')) {
    try { return await response.json() } catch {
      throw new ApiError('Сервер вернул некорректный ответ.', { status: response.status, code: 'invalid_response' })
    }
  }

  return response
}
