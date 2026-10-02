/**
 * T-Music Offline Storage Service.
 * Telegram-style persistent client storage engine using IndexedDB:
 * - Stores audio tracks (Blobs), cover images, and catalog/lyrics metadata.
 * - Enforces configurable cache size limit (LRU eviction).
 * - Enforces configurable cache TTL (retention period).
 * - Enables full offline audio playback via Blob URLs.
 */

import { getNativeStorageInfo } from './nativeBridge'

const DB_NAME = 'tmusic_offline_cache'
const DB_VERSION = 1
const SETTINGS_KEY = 'tmusic_cache_settings_v1'

export const DEFAULT_CACHE_SETTINGS = {
  maxCacheSizeMb: 1024,   // 1 GB default. Options: 500, 1024, 2048, 5120, 10240, 0 (0 = unlimited)
  cacheTtlDays: 7,        // 7 days default. Options: 3, 7, 30, 0 (0 = keep forever)
  cacheAudio: true,       // Cache audio files
  cacheCovers: true,      // Cache album covers
  cacheLyrics: true,      // Cache lyrics and synced timings
  cacheCatalog: true,     // Cache playlists and catalog lists
  autoCacheOnPlay: true,  // Automatically cache played tracks (Telegram-like behavior)
}

let dbInstance = null
let dbPromise = null
const activeDownloads = new Map() // fileId -> Promise<boolean>

function openDatabase() {
  if (dbInstance) return Promise.resolve(dbInstance)
  if (dbPromise) return dbPromise

  dbPromise = new Promise((resolve, reject) => {
    if (typeof window === 'undefined' || !window.indexedDB) {
      reject(new Error('IndexedDB not supported in this environment'))
      return
    }

    const request = window.indexedDB.open(DB_NAME, DB_VERSION)

    request.onupgradeneeded = (event) => {
      const db = event.target.result

      // Store 1: Audio Tracks
      if (!db.objectStoreNames.contains('tracks')) {
        const trackStore = db.createObjectStore('tracks', { keyPath: 'fileId' })
        trackStore.createIndex('cachedAt', 'cachedAt', { unique: false })
        trackStore.createIndex('lastAccessedAt', 'lastAccessedAt', { unique: false })
        trackStore.createIndex('size', 'size', { unique: false })
      }

      // Store 2: Cover Art
      if (!db.objectStoreNames.contains('covers')) {
        const coverStore = db.createObjectStore('covers', { keyPath: 'url' })
        coverStore.createIndex('cachedAt', 'cachedAt', { unique: false })
        coverStore.createIndex('lastAccessedAt', 'lastAccessedAt', { unique: false })
      }

      // Store 3: Metadata (Lyrics, Playlists, Catalog JSON)
      if (!db.objectStoreNames.contains('metadata')) {
        const metaStore = db.createObjectStore('metadata', { keyPath: 'key' })
        metaStore.createIndex('type', 'type', { unique: false })
        metaStore.createIndex('lastAccessedAt', 'lastAccessedAt', { unique: false })
      }

      // Store 4: Configuration Settings
      if (!db.objectStoreNames.contains('settings')) {
        db.createObjectStore('settings', { keyPath: 'id' })
      }
    }

    request.onsuccess = (event) => {
      dbInstance = event.target.result
      dbInstance.onversionchange = () => {
        dbInstance.close()
        dbInstance = null
        dbPromise = null
      }
      resolve(dbInstance)
    }

    request.onerror = (event) => {
      dbPromise = null
      reject(event.target.error || new Error('Failed to open IndexedDB'))
    }
  })

  return dbPromise
}

function notifyCacheUpdated(detail = {}) {
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent('tmusic:cache-updated', { detail }))
  }
}

// ─── Settings Operations ──────────────────────────────────────────────────────

export async function getCacheSettings() {
  try {
    const db = await openDatabase()
    return new Promise((resolve) => {
      const tx = db.transaction('settings', 'readonly')
      const req = tx.objectStore('settings').get('config')
      req.onsuccess = () => {
        const val = req.result?.value
        let merged = { ...DEFAULT_CACHE_SETTINGS }
        if (val && typeof val === 'object') {
          merged = { ...merged, ...val }
        } else {
          // Fallback to localStorage if exists
          try {
            const raw = localStorage.getItem(SETTINGS_KEY)
            if (raw) merged = { ...merged, ...JSON.parse(raw) }
          } catch {
            /* ignore */
          }
        }
        resolve(merged)
      }
      req.onerror = () => {
        resolve({ ...DEFAULT_CACHE_SETTINGS })
      }
    })
  } catch {
    return { ...DEFAULT_CACHE_SETTINGS }
  }
}

export async function saveCacheSettings(partialSettings) {
  const current = await getCacheSettings()
  const updated = { ...current, ...partialSettings }

  try {
    const db = await openDatabase()
    await new Promise((resolve, reject) => {
      const tx = db.transaction('settings', 'readwrite')
      const req = tx.objectStore('settings').put({ id: 'config', value: updated })
      req.onsuccess = () => resolve()
      req.onerror = () => reject(req.error)
    })
  } catch (e) {
    console.warn('Failed to save settings to IDB, saving to localStorage:', e)
  }

  try {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(updated))
  } catch {
    /* ignore */
  }

  notifyCacheUpdated({ type: 'settings_updated', settings: updated })
  // Run eviction when limit or TTL changed
  setTimeout(() => runEviction().catch(() => {}), 100)
  return updated
}

// ─── Track Caching ────────────────────────────────────────────────────────────

export async function saveTrack(trackMeta, blob) {
  if (!trackMeta || trackMeta.id == null || !blob) {
    throw new Error('Invalid track metadata or audio blob')
  }

  const fileId = Number(trackMeta.id)
  const now = Date.now()
  const item = {
    fileId,
    name: trackMeta.name || trackMeta.title || `Track ${fileId}`,
    title: trackMeta.title || (trackMeta.name || '').replace(/\.[^/.]+$/, ''),
    artist: trackMeta.artist || trackMeta.artistName || '',
    album: trackMeta.album || trackMeta.albumTitle || '',
    coverUrl: trackMeta.coverUrl || null,
    duration: Number(trackMeta.duration) || 0,
    size: blob.size,
    mimeType: blob.type || trackMeta.mime_type || 'audio/mpeg',
    blob,
    cachedAt: now,
    lastAccessedAt: now,
  }

  const db = await openDatabase()
  await new Promise((resolve, reject) => {
    const tx = db.transaction('tracks', 'readwrite')
    const req = tx.objectStore('tracks').put(item)
    req.onsuccess = () => resolve()
    req.onerror = () => reject(req.error)
  })

  notifyCacheUpdated({ type: 'track_added', fileId, size: blob.size })

  // Trigger eviction check in background
  setTimeout(() => checkAndEvictSize().catch(() => {}), 200)
  return item
}

export async function getCachedTrack(fileId) {
  if (fileId == null) return null
  const id = Number(fileId)

  try {
    const db = await openDatabase()
    return new Promise((resolve) => {
      const tx = db.transaction('tracks', 'readwrite')
      const store = tx.objectStore('tracks')
      const req = store.get(id)

      req.onsuccess = () => {
        const item = req.result
        if (item) {
          // Touch lastAccessedAt for LRU eviction
          item.lastAccessedAt = Date.now()
          store.put(item)
          resolve(item)
        } else {
          resolve(null)
        }
      }
      req.onerror = () => resolve(null)
    })
  } catch {
    return null
  }
}

export async function hasTrack(fileId) {
  if (fileId == null) return false
  const id = Number(fileId)

  try {
    const db = await openDatabase()
    return new Promise((resolve) => {
      const tx = db.transaction('tracks', 'readonly')
      const req = tx.objectStore('tracks').count(id)
      req.onsuccess = () => resolve(req.result > 0)
      req.onerror = () => resolve(false)
    })
  } catch {
    return false
  }
}

export async function getCachedTrackIds() {
  try {
    const db = await openDatabase()
    return new Promise((resolve) => {
      const tx = db.transaction('tracks', 'readonly')
      const req = tx.objectStore('tracks').getAllKeys()
      req.onsuccess = () => resolve(new Set((req.result || []).map(Number)))
      req.onerror = () => resolve(new Set())
    })
  } catch {
    return new Set()
  }
}

export async function getAllCachedTracks() {
  try {
    const db = await openDatabase()
    return new Promise((resolve) => {
      const tx = db.transaction('tracks', 'readonly')
      const req = tx.objectStore('tracks').getAll()
      req.onsuccess = () => {
        const items = req.result || []
        // Return without keeping large blobs if not needed, sorted by cachedAt desc
        const mapped = items.map((it) => ({
          fileId: it.fileId,
          id: it.fileId,
          name: it.name,
          title: it.title,
          artist: it.artist,
          album: it.album,
          coverUrl: it.coverUrl,
          duration: it.duration,
          size: it.size,
          mimeType: it.mimeType,
          cachedAt: it.cachedAt,
          lastAccessedAt: it.lastAccessedAt,
        })).sort((a, b) => b.cachedAt - a.cachedAt)
        resolve(mapped)
      }
      req.onerror = () => resolve([])
    })
  } catch {
    return []
  }
}

export async function deleteTrack(fileId) {
  if (fileId == null) return
  const id = Number(fileId)

  try {
    const db = await openDatabase()
    await new Promise((resolve, reject) => {
      const tx = db.transaction('tracks', 'readwrite')
      const req = tx.objectStore('tracks').delete(id)
      req.onsuccess = () => resolve()
      req.onerror = () => reject(req.error)
    })
    notifyCacheUpdated({ type: 'track_removed', fileId: id })
  } catch (e) {
    console.error('Failed to delete track from cache:', e)
  }
}

// ─── Cover Image Caching ──────────────────────────────────────────────────────

export async function saveCover(url, blob) {
  if (!url || !blob) return
  const now = Date.now()
  const item = {
    url,
    blob,
    size: blob.size,
    cachedAt: now,
    lastAccessedAt: now,
  }

  try {
    const db = await openDatabase()
    await new Promise((resolve, reject) => {
      const tx = db.transaction('covers', 'readwrite')
      const req = tx.objectStore('covers').put(item)
      req.onsuccess = () => resolve()
      req.onerror = () => reject(req.error)
    })
  } catch {
    /* ignore */
  }
}

export async function getCoverBlob(url) {
  if (!url) return null
  try {
    const db = await openDatabase()
    return new Promise((resolve) => {
      const tx = db.transaction('covers', 'readwrite')
      const store = tx.objectStore('covers')
      const req = store.get(url)
      req.onsuccess = () => {
        const item = req.result
        if (item) {
          item.lastAccessedAt = Date.now()
          store.put(item)
          resolve(item.blob)
        } else {
          resolve(null)
        }
      }
      req.onerror = () => resolve(null)
    })
  } catch {
    return null
  }
}

// ─── Metadata & Lyrics Caching ────────────────────────────────────────────────

export async function saveMetadata(key, type, data) {
  if (!key || data == null) return
  const now = Date.now()
  let size = 0
  try {
    size = JSON.stringify(data).length
  } catch {
    size = 1024
  }

  const item = {
    key,
    type,
    data,
    size,
    cachedAt: now,
    lastAccessedAt: now,
  }

  try {
    const db = await openDatabase()
    await new Promise((resolve, reject) => {
      const tx = db.transaction('metadata', 'readwrite')
      const req = tx.objectStore('metadata').put(item)
      req.onsuccess = () => resolve()
      req.onerror = () => reject(req.error)
    })
  } catch {
    /* ignore */
  }
}

export async function getMetadata(key) {
  if (!key) return null
  try {
    const db = await openDatabase()
    return new Promise((resolve) => {
      const tx = db.transaction('metadata', 'readwrite')
      const store = tx.objectStore('metadata')
      const req = store.get(key)
      req.onsuccess = () => {
        const item = req.result
        if (item) {
          item.lastAccessedAt = Date.now()
          store.put(item)
          resolve(item.data)
        } else {
          resolve(null)
        }
      }
      req.onerror = () => resolve(null)
    })
  } catch {
    return null
  }
}

// ─── Storage Breakdown & Stats ────────────────────────────────────────────────

export async function getStorageBreakdown() {
  const result = {
    audioBytes: 0,
    audioCount: 0,
    coversBytes: 0,
    coversCount: 0,
    metaBytes: 0,
    metaCount: 0,
    totalBytes: 0,
    quotaBytes: 0,
    deviceTotalBytes: 0,
    deviceAvailableBytes: 0,
  }

  try {
    const db = await openDatabase()

    // 1. Audio tracks stats
    await new Promise((resolve) => {
      const tx = db.transaction('tracks', 'readonly')
      const store = tx.objectStore('tracks')
      const req = store.openCursor()
      req.onsuccess = (e) => {
        const cursor = e.target.result
        if (cursor) {
          result.audioCount += 1
          result.audioBytes += (cursor.value.size || 0)
          cursor.continue()
        } else {
          resolve()
        }
      }
      req.onerror = () => resolve()
    })

    // 2. Covers stats
    await new Promise((resolve) => {
      const tx = db.transaction('covers', 'readonly')
      const store = tx.objectStore('covers')
      const req = store.openCursor()
      req.onsuccess = (e) => {
        const cursor = e.target.result
        if (cursor) {
          result.coversCount += 1
          result.coversBytes += (cursor.value.size || 0)
          cursor.continue()
        } else {
          resolve()
        }
      }
      req.onerror = () => resolve()
    })

    // 3. Metadata stats
    await new Promise((resolve) => {
      const tx = db.transaction('metadata', 'readonly')
      const store = tx.objectStore('metadata')
      const req = store.openCursor()
      req.onsuccess = (e) => {
        const cursor = e.target.result
        if (cursor) {
          result.metaCount += 1
          result.metaBytes += (cursor.value.size || 0)
          cursor.continue()
        } else {
          resolve()
        }
      }
      req.onerror = () => resolve()
    })

    result.totalBytes = result.audioBytes + result.coversBytes + result.metaBytes

    // 4. Browser Quota estimate
    if (navigator.storage && navigator.storage.estimate) {
      try {
        const est = await navigator.storage.estimate()
        result.quotaBytes = est.quota || 0
      } catch {
        /* ignore */
      }
    }

    // 5. Native Android storage info
    const nativeStorage = getNativeStorageInfo()
    if (nativeStorage) {
      result.deviceTotalBytes = nativeStorage.totalBytes || 0
      result.deviceAvailableBytes = nativeStorage.availableBytes || 0
    }
  } catch (e) {
    console.warn('Failed to calculate storage breakdown:', e)
  }

  return result
}

// ─── Cache Purging ────────────────────────────────────────────────────────────

export async function clearCategory(category) {
  try {
    const db = await openDatabase()
    const storesToClear = []
    if (category === 'audio' || category === 'all') storesToClear.push('tracks')
    if (category === 'covers' || category === 'all') storesToClear.push('covers')
    if (category === 'metadata' || category === 'all') storesToClear.push('metadata')

    for (const storeName of storesToClear) {
      await new Promise((resolve, reject) => {
        const tx = db.transaction(storeName, 'readwrite')
        const req = tx.objectStore(storeName).clear()
        req.onsuccess = () => resolve()
        req.onerror = () => reject(req.error)
      })
    }

    notifyCacheUpdated({ type: 'cache_cleared', category })
    return true
  } catch (e) {
    console.error(`Failed to clear cache category ${category}:`, e)
    return false
  }
}

// ─── Automatic Eviction (TTL + LRU Size Limit) ────────────────────────────────

export async function runEviction() {
  const settings = await getCacheSettings()
  const db = await openDatabase()
  const now = Date.now()

  // 1. TTL Eviction: remove items not accessed for more than cacheTtlDays
  if (settings.cacheTtlDays > 0) {
    const cutoff = now - (settings.cacheTtlDays * 24 * 60 * 60 * 1000)

    for (const storeName of ['tracks', 'covers', 'metadata']) {
      await new Promise((resolve) => {
        const tx = db.transaction(storeName, 'readwrite')
        const store = tx.objectStore(storeName)
        const index = store.index('lastAccessedAt')
        const range = IDBKeyRange.upperBound(cutoff)
        const req = index.openCursor(range)

        req.onsuccess = (e) => {
          const cursor = e.target.result
          if (cursor) {
            cursor.delete()
            cursor.continue()
          } else {
            resolve()
          }
        }
        req.onerror = () => resolve()
      })
    }
  }

  // 2. Max Size Limit LRU Eviction
  await checkAndEvictSize(settings)
  notifyCacheUpdated({ type: 'eviction_completed' })
}

async function checkAndEvictSize(preloadedSettings) {
  const settings = preloadedSettings || await getCacheSettings()
  if (!settings.maxCacheSizeMb || settings.maxCacheSizeMb <= 0) return

  const maxBytes = settings.maxCacheSizeMb * 1024 * 1024
  const breakdown = await getStorageBreakdown()

  if (breakdown.totalBytes <= maxBytes) return

  const db = await openDatabase()
  let currentBytes = breakdown.totalBytes

  // Sort tracks by lastAccessedAt ascending (oldest accessed first)
  const tracksToPrune = await new Promise((resolve) => {
    const tx = db.transaction('tracks', 'readonly')
    const store = tx.objectStore('tracks')
    const index = store.index('lastAccessedAt')
    const req = index.getAll()
    req.onsuccess = () => resolve(req.result || [])
    req.onerror = () => resolve([])
  })

  // Delete tracks until under limit
  for (const track of tracksToPrune) {
    if (currentBytes <= maxBytes) break
    await deleteTrack(track.fileId)
    currentBytes -= (track.size || 0)
  }
}

// ─── Downloading Tracks for Offline ───────────────────────────────────────────

export async function downloadTrackForOffline(track, { onProgress } = {}) {
  if (!track || track.id == null) return false
  const fileId = Number(track.id)

  if (activeDownloads.has(fileId)) {
    return activeDownloads.get(fileId)
  }

  const isAlreadyCached = await hasTrack(fileId)
  if (isAlreadyCached) return true

  const downloadPromise = (async () => {
    try {
      const streamUrl = track.stream_url || `/api/stream/${fileId}`
      const res = await fetch(streamUrl, {
        credentials: 'include',
        headers: { Accept: 'audio/*,*/*' },
      })

      if (!res.ok) {
        throw new Error(`Stream download failed with HTTP ${res.status}`)
      }

      const blob = await res.blob()
      await saveTrack(track, blob)

      // Background cache cover and lyrics if configured
      const settings = await getCacheSettings()
      if (settings.cacheCovers && track.coverUrl) {
        fetchCoverForOffline(track.coverUrl).catch(() => {})
      }
      if (settings.cacheLyrics) {
        fetchLyricsForOffline(fileId).catch(() => {})
      }

      return true
    } catch (e) {
      console.warn(`Failed to cache track ${fileId} for offline:`, e)
      return false
    } finally {
      activeDownloads.delete(fileId)
      notifyCacheUpdated({ type: 'download_finished', fileId })
    }
  })()

  activeDownloads.set(fileId, downloadPromise)
  notifyCacheUpdated({ type: 'download_started', fileId })
  return downloadPromise
}

export function isTrackDownloading(fileId) {
  return activeDownloads.has(Number(fileId))
}

export function getActiveDownloads() {
  return new Set(activeDownloads.keys())
}

async function fetchCoverForOffline(coverUrl) {
  if (!coverUrl) return
  try {
    const res = await fetch(coverUrl, { credentials: 'include' })
    if (res.ok) {
      const blob = await res.blob()
      await saveCover(coverUrl, blob)
    }
  } catch {
    /* ignore */
  }
}

async function fetchLyricsForOffline(fileId) {
  if (!fileId) return
  try {
    const res = await fetch(`/api/metadata/lyrics?track_id=${fileId}`, { credentials: 'include' })
    if (res.ok) {
      const json = await res.json()
      await saveMetadata(`lyrics:${fileId}`, 'lyrics', json)
    }
  } catch {
    /* ignore */
  }
}
