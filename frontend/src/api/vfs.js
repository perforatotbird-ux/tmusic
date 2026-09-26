/**
 * VFS folder hierarchy API client.
 */
import { request } from './client'
import { memoize, cacheTtl } from './cache'

export const vfsApi = {
  roots: () => memoize('/api/vfs/roots', cacheTtl.vfsTree, () => request('/api/vfs/roots')),

  folders: (parentId = null) =>
    parentId == null
      ? memoize('/api/vfs/folders', cacheTtl.vfsTree, () => request('/api/vfs/folders'))
      : request(`/api/vfs/folders?parent_id=${parentId}`),

  folder: (id) => request(`/api/vfs/folders/${id}`),

  children: (id) =>
    memoize(`/api/vfs/folders/${id}/children`, cacheTtl.vfsTree, () =>
      request(`/api/vfs/folders/${id}/children`)
    ),

  tracks: (folderId, recursive = true) => {
    const qs = `recursive=${recursive ? 'true' : 'false'}`
    return memoize(`/api/vfs/folders/${folderId}/tracks?${qs}`, cacheTtl.tracks, () =>
      request(`/api/vfs/folders/${folderId}/tracks?${qs}`)
    )
  },
}
