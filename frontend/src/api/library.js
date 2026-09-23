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

/** Drop null/undefined/'' so URLSearchParams never emits the string "null". */
function toQuery(params = {}) {
  const q = new URLSearchParams()
  for (const [k, v] of Object.entries(params || {})) {
    if (v === null || v === undefined || v === '') continue
    q.set(k, String(v))
  }
  return q.toString()
}

function invalidateCatalogCache() {
  for (const key of [...cache.keys()]) {
    if (key.startsWith('/api/library/artists') || key.startsWith('/api/library/albums')) {
      cache.delete(key)
    }
  }
}

export function bustCatalogCache() {
  invalidateCatalogCache()
}

export const libraryApi = {
  getArtists: (params = {}) => {
    const qs = toQuery(params)
    const key = `/api/library/artists?${qs}`
    return memoize(key, () => request(qs ? `/api/library/artists?${qs}` : '/api/library/artists'))
  },

  getAlbums: (params = {}) => {
    const qs = toQuery(params)
    const key = `/api/library/albums?${qs}`
    return memoize(key, () => request(qs ? `/api/library/albums?${qs}` : '/api/library/albums'))
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
    invalidateCatalogCache()
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
    invalidateCatalogCache()
    const formData = new FormData()
    formData.append('target_type', targetType)
    formData.append('target_id', targetId)
    formData.append('file', file)
    return request('/api/library/cover', {
      method: 'POST',
      body: formData,
    })
  },

  resetCover: (targetType, targetId) => {
    invalidateCatalogCache()
    return request(`/api/library/cover?target_type=${targetType}&target_id=${targetId}`, {
      method: 'DELETE',
    })
  },

  coversManifest: (keys, custom = 0) => {
    if (!keys || keys.length === 0) return Promise.resolve({ found: {}, missing: [] })
    const qs = new URLSearchParams({
      keys: keys.slice(0, 500).join(','),
      custom: String(custom ? 1 : 0),
    })
    return request(`/api/library/covers/manifest?${qs.toString()}`)
  },
}
