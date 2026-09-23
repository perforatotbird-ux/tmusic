/**
 * T-Music Metadata API Client (Last.fm).
 */
import { request } from './client'

export const metadataApi = {
  status: () => request('/api/metadata/status'),

  artist: (name) =>
    request(`/api/metadata/artist?name=${encodeURIComponent(name)}`),

  track: (artist, track, album) => {
    const q = new URLSearchParams({ artist, track })
    if (album) q.set('album', album)
    return request(`/api/metadata/track?${q.toString()}`)
  },

  enrichArtist: (artistId) =>
    request(`/api/metadata/artists/${artistId}/enrich`, { method: 'POST' }),

  /**
   * LRCLIB lyrics. Optional duration (sec) improves exact matching.
   * Returns { found, plain, synced, ... } — never throws on "not found".
   */
  lyrics: ({ artist, track, album, duration, fileName } = {}) => {
    const q = new URLSearchParams()
    if (artist) q.set('artist', artist)
    if (track) q.set('track', track)
    if (album) q.set('album', album)
    if (duration != null && Number.isFinite(duration) && duration > 0) {
      q.set('duration', String(Math.round(duration)))
    }
    if (fileName) q.set('file_name', fileName)
    return request(`/api/metadata/lyrics?${q.toString()}`)
  },
}
