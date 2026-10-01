/**
 * T-Music Playlists API Client
 */
import { request } from './client'

export const playlistsApi = {
  list: () => request('/api/playlists'),

  create: (name, initialFileId = null) =>
    request('/api/playlists', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, ...(initialFileId == null ? {} : { initial_file_id: initialFileId }) }),
    }),

  mirror: (name, fileIds) =>
    request('/api/playlists/mirror', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, file_ids: fileIds }),
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

  reorder: (playlistId, itemIds) =>
    request(`/api/playlists/${playlistId}/items/order`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ item_ids: itemIds }),
    }),
}
