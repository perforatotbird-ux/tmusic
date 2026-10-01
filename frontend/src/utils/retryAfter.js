/** Retry-After supports integer seconds or an HTTP-date. Bound UI waits. */
export function retryAfterSeconds(value, now = Date.now()) {
  const fallback = 60
  const maxWait = 300
  if (typeof value !== 'string' || !value.trim()) return fallback
  const raw = value.trim()
  let seconds
  if (/^\d+$/.test(raw)) {
    seconds = Number(raw)
  } else {
    // Do not let Date.parse reinterpret malformed numeric input as a date.
    if (!/^[A-Za-z]/.test(raw)) return fallback
    const timestamp = Date.parse(raw)
    seconds = Math.ceil((timestamp - now) / 1000)
  }
  if (!Number.isFinite(seconds)) return fallback
  return Math.min(maxWait, Math.max(0, seconds))
}