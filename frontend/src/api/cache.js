/**
 * Shared TTL cache with in-flight request deduplication (single flight).
 *
 * Fast typing / parallel effects fire the same request several times before
 * the first response lands; every duplicate used to hit the network. Now
 * concurrent callers await the same promise, and a fresh entry within ttlMs
 * never hits the network at all.
 */
const cache = new Map() // key -> { data, ts, ttlMs }
const inflight = new Map() // key -> Promise

export function memoize(key, ttlMs, fetcher) {
  const hit = cache.get(key)
  if (hit && Date.now() - hit.ts < hit.ttlMs) return Promise.resolve(hit.data)
  const pending = inflight.get(key)
  if (pending) return pending
  const p = fetcher()
    .then((data) => {
      cache.set(key, { data, ts: Date.now(), ttlMs })
      return data
    })
    .finally(() => inflight.delete(key))
  inflight.set(key, p)
  return p
}

/** @param prefix remove all entries whose key starts with prefix ('' = all). */
export function bustCache(prefix) {
  const p = prefix || ''
  for (const key of [...cache.keys()]) {
    if (key.startsWith(p)) cache.delete(key)
  }
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
