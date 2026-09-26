/**
 * Shared cover-existence registry.
 * MusicLibrary prefetches a manifest for the current grid items; ImgWithFallback
 * consults it to render the fallback badge immediately instead of firing a GET
 * (plus up to 2 retries) that is doomed to 404. Empty by default = old behavior.
 *
 * `probedCoverKeys` records that a (custom,key) pair was already answered by a
 * manifest request — a known-missing pair never re-enters the next manifest,
 * and a known-found pair stops being probed at all. The browser/SW cover cache
 * makes repeat image fetches network-free already, so only the manifest probe
 * needs the positive memory.
 */

export const missingCoverKeys = new Set()
export const probedCoverKeys = new Set()

export function setMissingCoverKeys(keys) {
  missingCoverKeys.clear()
  if (keys) {
    for (const k of keys) missingCoverKeys.add(k)
  }
}

export function markCoverProbed(custom, key) {
  probedCoverKeys.add(`${custom ? 1 : 0}:${key}`)
}

/** Catalog changed (covers/rename) or VFS import: forget both registries. */
export function resetCoverProbes() {
  missingCoverKeys.clear()
  probedCoverKeys.clear()
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
