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
 * Optional `artist` provides the artist-level fallback for album cards.
 */
export function resolveCoverUrl(entity, artist = null, fallback = DEFAULT_COVER) {
  const own =
    entity?.custom_image_url || entity?.cover_url || entity?.image_url || null
  const artistFallback =
    artist?.custom_image_url || artist?.image_url || null
  return firstTruthy(own, artistFallback, fallback)
}

/** Resolve the cover URL for a player release object { coverUrl }. */
export function resolveReleaseCoverUrl(release, fallback = DEFAULT_COVER) {
  return release?.coverUrl || fallback
}
