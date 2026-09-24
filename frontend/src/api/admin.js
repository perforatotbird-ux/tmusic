/**
 * T-Music Admin API client (cookie JWT + CSRF via request()).
 */
import { request } from './client'

export const adminApi = {
  overview: () => request('/api/admin/overview'),
  dbStats: () => request('/api/admin/db/stats'),
  vacuum: (analyze = true) =>
    request(`/api/admin/db/vacuum?analyze=${analyze ? 'true' : 'false'}`, { method: 'POST' }),
  analyze: () => request('/api/admin/db/analyze', { method: 'POST' }),
  reindex: () => request('/api/admin/db/reindex', { method: 'POST' }),
  ensureSchema: () => request('/api/admin/db/ensure-schema', { method: 'POST' }),
  purgeTokens: () => request('/api/admin/db/purge-tokens', { method: 'POST' }),

  listBackups: () => request('/api/admin/backups'),
  runBackup: () => request('/api/admin/backups/run', { method: 'POST' }),
  deleteBackup: (name) => request(`/api/admin/backups/${encodeURIComponent(name)}`, { method: 'DELETE' }),
  backupDownloadUrl: (name) => `/api/admin/backups/${encodeURIComponent(name)}/download`,

  getSettings: () => request('/api/admin/settings'),
  putSettings: (body) =>
    request('/api/admin/settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),

  listUsers: () => request('/api/admin/users'),
  unlockUser: (id) => request(`/api/admin/users/${id}/unlock`, { method: 'POST' }),
  revokeUserSessions: (id) => request(`/api/admin/users/${id}/revoke-sessions`, { method: 'POST' }),
  deleteUser: (id) => request(`/api/admin/users/${id}`, { method: 'DELETE' }),
  listAudit: (params = {}) => {
    const qs = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== null && v !== undefined && v !== '')
    ).toString()
    return request(qs ? `/api/admin/audit?${qs}` : '/api/admin/audit')
  },
  createUser: (body) =>
    request('/api/admin/users', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  patchUser: (id, body) =>
    request(`/api/admin/users/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),

  tgCluster: () => request('/api/admin/tg/cluster'),

  catalogArtists: (params = {}) => {
    const qs = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== null && v !== undefined && v !== '')
    ).toString()
    return request(qs ? `/api/admin/catalog/artists?${qs}` : '/api/admin/catalog/artists')
  },
  patchArtist: (id, body) =>
    request(`/api/admin/catalog/artists/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  createCollection: (body) =>
    request('/api/admin/catalog/artists', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  deleteArtist: (id) => request(`/api/admin/catalog/artists/${id}`, { method: 'DELETE' }),

  catalogAlbums: (params = {}) => {
    const qs = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== null && v !== undefined && v !== '')
    ).toString()
    return request(qs ? `/api/admin/catalog/albums?${qs}` : '/api/admin/catalog/albums')
  },
  patchAlbum: (id, body) =>
    request(`/api/admin/catalog/albums/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
}
