import { request } from './client'

export const streamApi = {
  // Cookie is sent automatically for same-origin <audio> — no token in URL.
  getFolderTracks: (folderId, recursive = true) =>
    request(`/api/stream/tracks/${folderId}?recursive=${recursive}`),

  getStreamUrl: (fileId) => `/api/stream/${fileId}`,

  // Short-lived (10 min) single-purpose token for players that cannot send
  // cookies/headers. Never put the session JWT into ?token= — the backend
  // rejects full session tokens in the query string.
  mintStreamToken: () =>
    request('/api/stream/token', { method: 'POST' }),

  getStreamUrlWithToken: async (fileId) => {
    const { stream_token } = await streamApi.mintStreamToken()
    return `/api/stream/${fileId}?token=${encodeURIComponent(stream_token)}`
  },
}
