import { request } from './client'
import { memoize, bustMediaCaches, cacheTtl } from './cache'
import { resetCoverProbes } from '../services/coverMissing'

/** Drop null/undefined/'' so URLSearchParams never emits the string "null". */
function toQuery(params = {}) {
  const q = new URLSearchParams()
  for (const [k, v] of Object.entries(params || {})) {
    if (v === null || v === undefined || v === '') continue
    q.set(k, String(v))
  }
  return q.toString()
}

/**
 * Tell every open view that one catalog row changed.
 *
 * `detail` names the row so a listener can patch it in place; without it the
 * listeners still refetch (the old behaviour). Covers/renames used to fire an
 * empty event, and views that did not listen — the admin catalog — silently
 * kept showing the previous artwork, which is indistinguishable from the
 * feature being broken.
 */
export function emitCatalogChanged(detail) {
  window.dispatchEvent(
    new CustomEvent('tmusic:catalog-changed', { detail: detail || null })
  )
}

export const libraryApi = {
  getArtists: (params = {}) => {
    const qs = toQuery(params)
    const key = `/api/library/artists?${qs}`
    return memoize(key, cacheTtl.catalog, () =>
      request(qs ? `/api/library/artists?${qs}` : '/api/library/artists')
    )
  },

  getAlbums: (params = {}) => {
    const qs = toQuery(params)
    const key = `/api/library/albums?${qs}`
    return memoize(key, cacheTtl.catalog, () =>
      request(qs ? `/api/library/albums?${qs}` : '/api/library/albums')
    )
  },

  getArtistDetail: (artistId) => {
    const key = `/api/library/artists/${artistId}`
    return memoize(key, cacheTtl.detail, () => request(key))
  },

  getStats: () => memoize('/api/library/stats', cacheTtl.stats, () => request('/api/library/stats')),

  setCoverUrl: (targetType, targetId, imageUrl) => {
    bustCatalogCache()
    const formData = new FormData()
    formData.append('target_type', targetType)
    formData.append('target_id', targetId)
    formData.append('url', imageUrl)
    return request('/api/library/cover', {
      method: 'POST',
      body: formData,
    }).then((res) => {
      emitCatalogChanged({ type: targetType, id: targetId, custom_key: res?.custom_key })
      return res
    })
  },

  uploadCoverFile: (targetType, targetId, file) => {
    bustCatalogCache()
    const formData = new FormData()
    formData.append('target_type', targetType)
    formData.append('target_id', targetId)
    formData.append('file', file)
    return request('/api/library/cover', {
      method: 'POST',
      body: formData,
    }).then((res) => {
      emitCatalogChanged({ type: targetType, id: targetId, custom_key: res?.custom_key })
      return res
    })
  },

  resetCover: (targetType, targetId) => {
    bustCatalogCache()
    return request(`/api/library/cover?target_type=${targetType}&target_id=${targetId}`, {
      method: 'DELETE',
    }).then((res) => {
      emitCatalogChanged({ type: targetType, id: targetId, custom_key: null })
      return res
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

/**
 * Kept as a named export: MusicLibrary and AdminCatalog call it after
 * mutations. One contract for everything derived from the data model:
 * catalog lists, tracklists, the VFS tree and cover-existence probes.
 */
export function bustCatalogCache() {
  bustMediaCaches()
  resetCoverProbes()
}
