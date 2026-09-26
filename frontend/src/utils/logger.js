/**
 * T-Music Frontend Logger
 * =======================
 * Structured client-side logging with buffered delivery to the backend
 * (POST /api/system/client-logs → backend/storage/log/frontend.log).
 *
 * Design:
 * - Levels: debug < info < warn < error < fatal (standard names mapped to
 *   backend DEBUG/INFO/WARNING/ERROR/CRITICAL).
 * - Console mirror is human-readable; the wire format is structured JSON.
 * - Buffer flushes: every 4s, on ≥25 events, on pagehide/visibilitychange
 *   (sendBeacon-style keepalive fetch), and immediately for error+ events
 *   (debounced).
 * - Never throws, never loops (a failed log-ship is swallowed after one
 *   console warn), payload sanitized (passwords/tokens stripped, depth cap).
 * - VITE_LOG_LEVEL env overrides the minimum shipped level (default: debug
 *   in dev, info in prod builds).
 */

const LEVELS = { debug: 10, info: 20, warn: 30, error: 40, fatal: 50 }
const BACKEND_LEVEL = { debug: 'DEBUG', info: 'INFO', warn: 'WARNING', error: 'ERROR', fatal: 'CRITICAL' }

const envLevel = (typeof import.meta !== 'undefined' && import.meta.env && import.meta.env.VITE_LOG_LEVEL) || ''
const isDev = !!(typeof import.meta !== 'undefined' && import.meta.env && import.meta.env.DEV)
const MIN_LEVEL = LEVELS[envLevel] ?? (isDev ? LEVELS.debug : LEVELS.info)

const FLUSH_INTERVAL_MS = 4000
const FLUSH_BATCH = 25
const MAX_BUFFER = 200
const MAX_MSG = 2000
const MAX_DEPTH = 4

const sessionId = (typeof crypto !== 'undefined' && crypto.randomUUID)
  ? crypto.randomUUID()
  : `s-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`

const buffer = []
let flushTimer = null
let errorFlushTimer = null
let shipping = false
let shipFailures = 0

const SENSITIVE_KEY = /(pass|pwd|secret|token|authorization|cookie|session_string|api_hash|api_key|password_hash|csrf)/i

function sanitize(value, depth = 0) {
  if (value == null) return value
  if (depth > MAX_DEPTH) return '[depth]'
  if (value instanceof Error) {
    return { name: value.name, message: value.message, stack: (value.stack || '').split('\n').slice(0, 8).join('\n') }
  }
  if (Array.isArray(value)) return value.slice(0, 20).map((v) => sanitize(v, depth + 1))
  if (typeof value === 'object') {
    const out = {}
    for (const [k, v] of Object.entries(value)) {
      if (SENSITIVE_KEY.test(k)) { out[k] = '[redacted]'; continue }
      out[k] = sanitize(v, depth + 1)
    }
    return out
  }
  if (typeof value === 'string') return value.length > MAX_MSG ? value.slice(0, MAX_MSG) + '…' : value
  if (typeof value === 'bigint') return String(value)
  if (typeof value === 'function') return '[fn]'
  return value
}

function consoleArgs(level, scope, msg, data) {
  const prefix = `%c[${level.toUpperCase().padEnd(5)}] ${scope}`
  const colors = { debug: '#888', info: '#2f9e44', warn: '#e8940a', error: '#e03131', fatal: '#000;background:#e03131' }
  const args = [prefix, `color:${colors[level] || '#000'};font-weight:bold`, msg]
  if (data !== undefined) args.push(data)
  return args
}

function push(level, scope, msg, data) {
  if (LEVELS[level] < MIN_LEVEL) return
  const text = typeof msg === 'string' ? msg : String(msg)
  // Console mirror (human readable)
  const fn = level === 'debug' ? console.debug
    : level === 'info' ? console.info
    : level === 'warn' ? console.warn
    : console.error
  try { fn(...consoleArgs(level, scope, text, data)) } catch { /* console may be absent */ }

  buffer.push({
    ts: new Date().toISOString(),
    // Backend ClientEvent.level is a UPPERCASE Literal whitelist — sending the
    // lowercase internal name fails Pydantic validation with HTTP 422.
    level: BACKEND_LEVEL[level],
    logger: scope,
    msg: text.slice(0, MAX_MSG),
    data: data !== undefined ? sanitize(data) : undefined,
  })
  if (buffer.length > MAX_BUFFER) buffer.splice(0, buffer.length - MAX_BUFFER)

  if (buffer.length >= FLUSH_BATCH) { flush(); return }
  if (level === 'error' || level === 'fatal') scheduleErrorFlush()
  else scheduleFlush()
}

function scheduleFlush() {
  if (flushTimer) return
  flushTimer = setTimeout(() => { flushTimer = null; flush() }, FLUSH_INTERVAL_MS)
}

function scheduleErrorFlush() {
  // Errors ship quickly but coalesce bursts into one request (500ms).
  if (errorFlushTimer) return
  errorFlushTimer = setTimeout(() => { errorFlushTimer = null; flush() }, 500)
}

// The logger cannot import api/client (circular: client imports logger), so
// it echoes the CSRF cookie itself — same double-submit contract as request().
function csrfToken() {
  try {
    const match = document.cookie.match(/(?:^|; )csrf_token=([^;]*)/)
    return match ? decodeURIComponent(match[1]) : null
  } catch {
    return null
  }
}

async function flush() {
  if (shipping || buffer.length === 0) return
  if (shipFailures >= 3) return // circuit breaker: backend down → stop trying this session
  shipping = true
  const events = buffer.splice(0, 50)
  try {
    const headers = { 'Content-Type': 'application/json' }
    const csrf = csrfToken()
    if (csrf) headers['X-CSRF-Token'] = csrf
    const res = await fetch('/api/system/client-logs', {
      method: 'POST',
      headers,
      credentials: 'same-origin',
      keepalive: true,
      body: JSON.stringify({ source: 'web', session_id: sessionId, events }),
    })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    shipFailures = 0
  } catch (err) {
    shipFailures += 1
    // Put events back (front of queue) unless the buffer overflowed meanwhile.
    buffer.unshift(...events)
    if (buffer.length > MAX_BUFFER) buffer.length = MAX_BUFFER
    if (shipFailures === 1) {
      try { console.warn('[LOG ] logger: log ship failed:', err?.message || err) } catch { /* ignore */ }
    }
  } finally {
    shipping = false
    if (buffer.length >= FLUSH_BATCH) scheduleFlush()
  }
}

// Flush on tab hide/close so the last events are not lost.
if (typeof window !== 'undefined') {
  window.addEventListener('pagehide', () => { flush() })
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') flush()
  })
}

/**
 * Create a scoped logger: const log = createLogger('player')
 * Methods: log.debug/info/warn/error/fatal(msg, data?)
 */
export function createLogger(scope) {
  const s = scope || 'app'
  return {
    debug: (msg, data) => push('debug', s, msg, data),
    info: (msg, data) => push('info', s, msg, data),
    warn: (msg, data) => push('warn', s, msg, data),
    error: (msg, data) => push('error', s, msg, data),
    fatal: (msg, data) => push('fatal', s, msg, data),
    /** Time a synchronous/async operation; logs duration at debug (errors at error). */
    async time(label, fn) {
      const t0 = performance.now()
      try {
        const result = await fn()
        push('debug', s, `${label} ok`, { duration_ms: Math.round(performance.now() - t0) })
        return result
      } catch (err) {
        push('error', s, `${label} failed`, { duration_ms: Math.round(performance.now() - t0), error: err })
        throw err
      }
    },
    flush,
  }
}

export const log = createLogger('app')

/**
 * Install global error capture (window.onerror + unhandledrejection).
 * Call once from main.jsx. Safe to call twice.
 */
let globalsInstalled = false
export function installGlobalErrorLogging() {
  if (globalsInstalled || typeof window === 'undefined') return
  globalsInstalled = true

  window.addEventListener('error', (ev) => {
    push('error', 'window.error', ev.message || 'Uncaught error', {
      filename: ev.filename,
      lineno: ev.lineno,
      colno: ev.colno,
      error: ev.error,
      url: location.href,
    })
  })

  window.addEventListener('unhandledrejection', (ev) => {
    const reason = ev.reason
    push('error', 'window.unhandledrejection',
      (reason && reason.message) || 'Unhandled promise rejection', {
        error: reason,
        url: location.href,
      })
  })

  // First paint marker — useful to bound "session start" when reading logs.
  push('info', 'app', 'frontend booted', {
    url: location.href,
    viewport: `${window.innerWidth}x${window.innerHeight}`,
    user_agent: navigator.userAgent,
  })
}

export default log
