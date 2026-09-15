/**
 * Shared missing-cover registry.
 * MusicLibrary prefetches a manifest for the current grid items; ImgWithFallback
 * consults it to render the fallback badge immediately instead of firing a GET
 * (plus up to 2 retries) that is doomed to 404. Empty by default = old behavior.
 */

export const missingCoverKeys = new Set()

export function setMissingCoverKeys(keys) {
  missingCoverKeys.clear()
  if (keys) {
    for (const k of keys) missingCoverKeys.add(k)
  }
}

/**
 * Extract {key, custom} from a card cover URL (/api/library/cover?key=..&custom=1).
 * Returns null for remote-fetch URLs (?url=..) and non-cover URLs.
 */
export function parseCoverKey(src) {
  if (!src || typeof src !== 'string') return null
  const q = src.indexOf('?')
  if (q === -1 || !src.startsWith('/api/library/cover')) return null
  const params = new URLSearchParams(src.slice(q + 1))
  const key = params.get('key')
  if (!key) return null
  return { key, custom: params.get('custom') === '1' }
}
