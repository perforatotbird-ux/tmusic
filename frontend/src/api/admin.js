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
  systemHealth: () => request('/api/admin/system/health'),
  reconnectTelegram: () => request('/api/admin/tg/reconnect', { method: 'POST' }),
  restoreBackup: (name, body) =>
    request(`/api/admin/backups/${encodeURIComponent(name)}/restore`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
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
  subsonicCredentials: () => request('/api/subsonic/credentials'),
  rotateSubsonicCredentials: () => request('/api/subsonic/credentials/rotate', { method: 'POST' }),
  analytics: (days = 30) => request(`/api/admin/analytics?days=${days}`),
  vfsFolders: (params = {}) => {
    const qs = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== null && v !== undefined && v !== '')
    ).toString()
    return request(qs ? `/api/admin/vfs/folders?${qs}` : '/api/admin/vfs/folders')
  },
  patchVfsFolder: (id, body) =>
    request(`/api/admin/vfs/folders/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  vfsFiles: (params = {}) => {
    const qs = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== null && v !== undefined && v !== '')
    ).toString()
    return request(qs ? `/api/admin/vfs/files?${qs}` : '/api/admin/vfs/files')
  },
  patchVfsFile: (id, body) =>
    request(`/api/admin/vfs/files/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  moveVfsFiles: (fileIds, folderId) =>
    request('/api/admin/vfs/files/move', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file_ids: fileIds, folder_id: folderId }),
    }),
  rebuildVfsClosures: () => request('/api/admin/vfs/folders/rebuild-closures', { method: 'POST' }),
  uploadVfsBackup: (file) => {
    const form = new FormData()
    form.append('file', file)
    return request('/api/admin/vfs/import/upload', { method: 'POST', body: form })
  },
  getVfsImportChildren: (importId, parentId) => {
    const qs = parentId !== undefined && parentId !== null ? `?parent_id=${encodeURIComponent(parentId)}` : ''
    return request(`/api/admin/vfs/import/${encodeURIComponent(importId)}/children${qs}`)
  },
  getLiveVfsChildren: (parentId) => {
    const qs = parentId !== undefined && parentId !== null ? `?parent_id=${encodeURIComponent(parentId)}` : ''
    return request(`/api/admin/vfs/live-children${qs}`)
  },
  previewTreeImport: (importId, body = {}) =>
    request(`/api/admin/vfs/import/${encodeURIComponent(importId)}/tree-preview`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  runTreeImport: (importId, body = {}) =>
    request(`/api/admin/vfs/import/${encodeURIComponent(importId)}/tree-run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  getVfsImportStatus: (importId) =>
    request(`/api/admin/vfs/import/${encodeURIComponent(importId)}/status`),
  previewVfsImport: (file, options = {}) => {
    const form = new FormData()
    form.append('file', file)
    form.append('source_folder_path', options.sourceFolderPath || 'Music')
    if (options.sourceFolderId) form.append('source_folder_id', String(options.sourceFolderId))
    if (options.targetRootId) form.append('target_root_id', String(options.targetRootId))
    form.append('include_non_audio', options.includeNonAudio ? 'true' : 'false')
    return request('/api/admin/vfs/import/preview', { method: 'POST', body: form })
  },
  runVfsImport: (importId, body = { confirm: true }) =>
    request(`/api/admin/vfs/import/${encodeURIComponent(importId)}/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  getVfsImport: (importId) =>
    request(`/api/admin/vfs/import/${encodeURIComponent(importId)}`),
  catalogArtists: (params = {}) => {
    const qs = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== null && v !== undefined && v !== '')
    ).toString()
    return request(qs ? `/api/admin/catalog/artists?${qs}` : '/api/admin/catalog/artists')
  },
  batchArtists: (body) =>
    request('/api/admin/catalog/artists/batch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  scanArtist: (id) => request(`/api/admin/catalog/artists/${id}/scan`, { method: 'POST' }),
  scanAlbum: (id) => request(`/api/admin/catalog/albums/${id}/scan`, { method: 'POST' }),
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
  batchAlbums: (body) =>
    request('/api/admin/catalog/albums/batch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  patchAlbum: (id, body) =>
    request(`/api/admin/catalog/albums/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
}
