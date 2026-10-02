/**
 * T-Music Search API Client
 * Supports unified search across tracks, artists, and albums,
 * as well as category-specific paginated queries with AbortSignal.
 */
import { request } from './client'

function toQuery(params = {}) {
  const q = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v === null || v === undefined || v === '') continue
    q.set(k, String(v))
  }
  return q.toString()
}

export const searchApi = {
  /**
   * Run unified search across artists, albums, and tracks.
   * @param {string} query
   * @param {{ signal?: AbortSignal, limitArtists?: number, limitAlbums?: number, limitTracks?: number }} options
   */
  unified: (query, options = {}) => {
    const qs = toQuery({
      q: query,
      limit_artists: options.limitArtists ?? 6,
      limit_albums: options.limitAlbums ?? 8,
      limit_tracks: options.limitTracks ?? 15,
    })
    return request(`/api/search?${qs}`, { signal: options.signal })
  },

  /**
   * Search tracks with pagination.
   * @param {string} query
   * @param {{ signal?: AbortSignal, limit?: number, offset?: number }} options
   */
  tracks: (query, options = {}) => {
    const qs = toQuery({
      q: query,
      limit: options.limit ?? 30,
      offset: options.offset ?? 0,
    })
    return request(`/api/search/tracks?${qs}`, { signal: options.signal })
  },

  /**
   * Search artists with pagination.
   * @param {string} query
   * @param {{ signal?: AbortSignal, limit?: number, offset?: number }} options
   */
  artists: (query, options = {}) => {
    const qs = toQuery({
      q: query,
      limit: options.limit ?? 30,
      offset: options.offset ?? 0,
    })
    return request(`/api/search/artists?${qs}`, { signal: options.signal })
  },

  /**
   * Search albums with pagination.
   * @param {string} query
   * @param {{ signal?: AbortSignal, limit?: number, offset?: number }} options
   */
  albums: (query, options = {}) => {
    const qs = toQuery({
      q: query,
      limit: options.limit ?? 30,
      offset: options.offset ?? 0,
    })
    return request(`/api/search/albums?${qs}`, { signal: options.signal })
  },
}
