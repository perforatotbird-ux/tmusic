/** Bounded TTL/LRU cache with identity-safe single-flight invalidation. */
const cache = new Map()
const inflight = new Map()
const MAX_ENTRIES = 128
const MAX_ESTIMATED_BYTES = 16 * 1024 * 1024
let expiryTimer = null

function pruneCache() {
  const now = Date.now()
  for (const [key, entry] of cache) {
    if (entry.expires <= now) cache.delete(key)
  }
  let bytes = [...cache.values()].reduce((n, entry) => n + entry.bytes, 0)
  while (cache.size > MAX_ENTRIES || bytes > MAX_ESTIMATED_BYTES) {
    const key = cache.keys().next().value
    bytes -= cache.get(key).bytes
    cache.delete(key)
  }
}

function scheduleExpiry() {
  if (expiryTimer !== null) clearTimeout(expiryTimer)
  expiryTimer = null
  if (!cache.size || typeof setTimeout !== 'function') return
  const expires = Math.min(...[...cache.values()].map((entry) => entry.expires))
  expiryTimer = setTimeout(() => {
    expiryTimer = null
    pruneCache()
    scheduleExpiry()
  }, Math.max(1, Math.min(2147483647, expires - Date.now())))
  expiryTimer?.unref?.()
}

export function memoize(key, ttlMs, fetcher) {
  pruneCache()
  const hit = cache.get(key)
  if (hit) {
    cache.delete(key)
    cache.set(key, hit) // LRU access, without extending TTL.
    return Promise.resolve(hit.data)
  }
  const pending = inflight.get(key)
  if (pending) return pending
  const p = Promise.resolve().then(fetcher)
    .then((data) => {
      if (inflight.get(key) === p && Number.isFinite(ttlMs) && ttlMs > 0) {
        // Approximate serialized UTF-16 payload budget, not a JS heap measurement.
        // Non-JSON values still return normally but are not retained in this cache.
        let bytes = Infinity
        try { bytes = JSON.stringify(data).length * 2 } catch { /* not cacheable */ }
        if (bytes <= MAX_ESTIMATED_BYTES) {
          cache.set(key, { data, expires: Date.now() + ttlMs, bytes })
        }
        pruneCache()
        scheduleExpiry()
      }
      return data
    })
    .finally(() => { if (inflight.get(key) === p) inflight.delete(key) })
  inflight.set(key, p)
  return p
}

/** @param prefix remove all entries whose key starts with prefix ('' = all). */
export function bustCache(prefix) {
  const p = prefix || ''
  for (const key of new Set([...cache.keys(), ...inflight.keys()])) {
    if (key.startsWith(p)) { cache.delete(key); inflight.delete(key) }
  }
  scheduleExpiry()
}

/** Shared TTLs (ms) per data class — keep policy in one place. */
export const cacheTtl = {
  catalog: 5 * 60 * 1000, // artist/album lists
  detail: 5 * 60 * 1000, // artist page
  stats: 10 * 60 * 1000, // header counters change rarely
  tracks: 2 * 60 * 1000, // folder/album tracklists — refreshed after VFS import
  vfsTree: 2 * 60 * 1000, // folder children listings
}

/**
 * Invalidate everything derived from the library/vfs data model. Called on
 * catalog mutations (covers, renames) and after VFS imports, where folder
 * ids, tracks and counts all shift at once.
 */
export function bustMediaCaches() {
  for (const prefix of ['/api/library', '/api/stream', '/api/vfs']) bustCache(prefix)
}
