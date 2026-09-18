import { request } from './client'

const cache = new Map()
const CACHE_TTL = 5 * 60 * 1000

function memoize(key, fetcher) {
  const cached = cache.get(key)
  if (cached && Date.now() - cached.ts < CACHE_TTL) {
    return Promise.resolve(cached.data)
  }
  return fetcher().then(data => {
    cache.set(key, { data, ts: Date.now() })
    return data
  })
}

function buildKey(path, params) {
  return `${path}?${new URLSearchParams(params).toString()}`
}

export const libraryApi = {
  getArtists: (params = {}) => {
    const key = buildKey('/api/library/artists', params)
    return memoize(key, () => request(`/api/library/artists?${new URLSearchParams(params).toString()}`))
  },

  getAlbums: (params = {}) => {
    const key = buildKey('/api/library/albums', params)
    return memoize(key, () => request(`/api/library/albums?${new URLSearchParams(params).toString()}`))
  },

  getArtistDetail: (artistId) => {
    const key = `/api/library/artists/${artistId}`
    return memoize(key, () => request(key))
  },

  getStats: () => {
    const key = '/api/library/stats'
    return memoize(key, () => request(key))
  },

  setCoverUrl: (targetType, targetId, imageUrl) => {
    cache.delete('/api/library/artists')
    cache.delete('/api/library/albums')
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
    cache.delete('/api/library/artists')
    cache.delete('/api/library/albums')
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

  coversManifest: (keys, custom = 0) => {
    if (!keys || keys.length === 0) return Promise.resolve({ found: {}, missing: [] })
    const qs = new URLSearchParams({
      keys: keys.slice(0, 500).join(','),
      custom: String(custom ? 1 : 0),
    })
    return request(`/api/library/covers/manifest?${qs.toString()}`)
  },
}