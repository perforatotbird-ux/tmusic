/**
 * VFS folder hierarchy API client.
 */
import { request } from './client'

export const vfsApi = {
  roots: () => request('/api/vfs/roots'),

  folders: (parentId = null) =>
    parentId == null
      ? request('/api/vfs/folders')
      : request(`/api/vfs/folders?parent_id=${parentId}`),

  folder: (id) => request(`/api/vfs/folders/${id}`),

  children: (id) => request(`/api/vfs/folders/${id}/children`),

  tracks: (folderId, recursive = true) =>
    request(`/api/vfs/folders/${folderId}/tracks?recursive=${recursive ? 'true' : 'false'}`),
}
