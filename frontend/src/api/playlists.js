/**
 * T-Music Playlists API Client
 */
import { request } from './client'

export const playlistsApi = {
  list: () => request('/api/playlists'),

  create: (name) =>
    request('/api/playlists', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    }),

  get: (id) => request(`/api/playlists/${id}`),

  remove: (id) => request(`/api/playlists/${id}`, { method: 'DELETE' }),

  addTrack: (playlistId, fileId) =>
    request(`/api/playlists/${playlistId}/items`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file_id: fileId }),
    }),

  removeItem: (playlistId, itemId) =>
    request(`/api/playlists/${playlistId}/items/${itemId}`, { method: 'DELETE' }),
}
