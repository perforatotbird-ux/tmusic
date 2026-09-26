import { request } from './client'
import { memoize, cacheTtl } from './cache'

export const streamApi = {
  // Cookie is sent automatically for same-origin <audio> — no token in URL.
  getStreamUrl: (fileId) => `/api/stream/${fileId}`,

  // Tracklists change only through catalog ops/VFS imports; those call
  // bustMediaCaches(). Single-flight also collapses the duplicate fires from
  // double-clicks and parallel play handlers.
  getFolderTracks: (folderId, recursive = true) => {
    const key = `/api/stream/tracks/${folderId}?recursive=${recursive}`
    return memoize(key, cacheTtl.tracks, () => request(key))
  },

  // Short-lived (10 min) single-purpose token for players that cannot send
  // cookies/headers. Never put the session JWT into ?token= — the backend
  // rejects full session tokens in the query string.
  mintStreamToken: () =>
    request('/api/stream/token', { method: 'POST' }),

  getStreamUrlWithToken: async (fileId) => {
    const { stream_token } = await streamApi.mintStreamToken()
    if (!stream_token || typeof stream_token !== 'string') {
      throw new Error('Stream token unavailable')
    }
    return `/api/stream/${fileId}?token=${encodeURIComponent(stream_token)}`
  },
}
