import { request } from './client'

export const libraryApi = {
  getArtists: (params = {}) => {
    const query = new URLSearchParams()
    if (params.kind) query.set('kind', params.kind)
    if (params.search) query.set('search', params.search)
    if (params.limit) query.set('limit', params.limit)
    if (params.offset) query.set('offset', params.offset)
    const qs = query.toString()
    return request(`/api/library/artists${qs ? `?${qs}` : ''}`)
  },

  getAlbums: (params = {}) => {
    const query = new URLSearchParams()
    if (params.search) query.set('search', params.search)
    if (params.limit) query.set('limit', params.limit)
    if (params.offset) query.set('offset', params.offset)
    const qs = query.toString()
    return request(`/api/library/albums${qs ? `?${qs}` : ''}`)
  },

  getArtistDetail: (artistId) => request(`/api/library/artists/${artistId}`),

  getStats: () => request('/api/library/stats'),

  setCoverUrl: (targetType, targetId, imageUrl) => {
    const formData = new FormData()
    formData.append('target_type', targetType)
    formData.append('target_id', targetId)
    formData.append('url', imageUrl)
    return request('/api/library/cover', {
      method: 'POST',
      body: formData,
    })
  },

  uploadCoverFile: (targetType, targetId, file) => {
    const formData = new FormData()
    formData.append('target_type', targetType)
    formData.append('target_id', targetId)
    formData.append('file', file)
    return request('/api/library/cover', {
      method: 'POST',
      body: formData,
    })
  },

  resetCover: (targetType, targetId) =>
    request(`/api/library/cover?target_type=${targetType}&target_id=${targetId}`, {
      method: 'DELETE',
    }),
}
