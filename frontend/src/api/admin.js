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
}
