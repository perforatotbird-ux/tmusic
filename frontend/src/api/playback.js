/**
 * T-Music Playback API Client (frontend).
 * Cookie session + CSRF handled by request().
 */
import { request } from './client'

export const playbackApi = {
  getPosition: (fileId) => request(`/api/playback/position/${fileId}`),

  savePosition: (fileId, positionSec, durationSec) =>
    request(`/api/playback/position/${fileId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        position_sec: positionSec,
        duration_sec: durationSec ?? null,
      }),
    }),

  listPositions: (limit = 30) => request(`/api/playback/positions?limit=${limit}`),

  addHistory: (payload) =>
    request('/api/playback/history', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),

  listHistory: (limit = 50, offset = 0) =>
    request(`/api/playback/history?limit=${limit}&offset=${offset}`),

  searchTracks: (search, limit = 50) => {
    const q = encodeURIComponent(search || '')
    return request(`/api/playback/tracks/search?search=${q}&limit=${limit}`)
  },
}
