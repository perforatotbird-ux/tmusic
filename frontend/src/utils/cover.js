/**
 * Single source of truth for cover-image fallback priority.
 *
 * Backend cards expose up to three URL fields:
 *   custom_image_url (user-uploaded override, `/api/library/cover?key=..&custom=1`) →
 *   cover_url / image_url (enriched, always `/api/library/cover?key=..` from the
 *   local disk cache — never a remote URL) →
 *   DEFAULT_COVER fallback.
 *
 * Album cards use `cover_url`, artist cards use `image_url`; this helper
 * accepts either shape so all call sites resolve covers identically.
 *
 * Covers are NEVER inherited from a parent entity. An album without its own
 * cover falls back to the placeholder, never to its artist's cover — otherwise
 * a manually assigned artist cover leaks into every one of its albums. The
 * artist is passed to the card renderers for the queue metadata (`artistName`)
 * and is deliberately not a cover source.
 */

export const DEFAULT_COVER = '/icon.svg'

function firstTruthy(...values) {
  for (const v of values) {
    if (v) return v
  }
  return null
}

/**
 * Resolve the display cover URL for an album/artist card object.
 * Only the entity's own cover is considered; `fallback` is used when the
 * entity has none.
 *
 * `fallback` must be a string, or the explicit `null` meaning "no fallback at
 * all" (queue metadata relies on that: a stored '/icon.svg' would mask the
 * «no cover» badge). Anything else — e.g. the artist object that the previous
 * two-argument signature used to accept here — is ignored rather than handed
 * to an <img src>.
 */
export function resolveCoverUrl(entity, fallback = DEFAULT_COVER) {
  const own =
    entity?.custom_image_url || entity?.cover_url || entity?.image_url || null
  const safeFallback =
    fallback === null || typeof fallback === 'string' ? fallback : DEFAULT_COVER
  return firstTruthy(own, safeFallback)
}

/** Resolve the cover URL for a player release object { coverUrl }. */
export function resolveReleaseCoverUrl(release, fallback = DEFAULT_COVER) {
  return release?.coverUrl || fallback
}
