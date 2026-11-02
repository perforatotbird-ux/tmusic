/**
 * Zustand store for Offline Storage & Telegram-like Cache Management.
 */

import { create } from 'zustand'
import {
  getCacheSettings,
  saveCacheSettings,
  getCachedTrackIds,
  getAllCachedTracks,
  getStorageBreakdown,
  deleteTrack,
  clearCategory,
  downloadTrackForOffline,
  runEviction,
  DEFAULT_CACHE_SETTINGS,
} from '../services/offlineStorageService'
import { createLogger } from '../utils/logger'

const log = createLogger('offline-store')

export const useOfflineStore = create((set, get) => {
  let isInitialized = false

  const handleOnline = () => set({ isOffline: false })
  const handleOffline = () => set({ isOffline: true })

  const handleCacheUpdated = (e) => {
    const detail = e?.detail || {}
    // Refresh track list and stats
    get().refreshStats()
    if (detail.type === 'download_started' && detail.fileId) {
      set((state) => {
        const next = new Set(state.cachingTrackIds)
        next.add(detail.fileId)
        return { cachingTrackIds: next }
      })
    } else if (detail.type === 'download_finished' && detail.fileId) {
      set((state) => {
        const next = new Set(state.cachingTrackIds)
        next.delete(detail.fileId)
        return { cachingTrackIds: next }
      })
    }
  }

  return {
    cachedTrackIds: new Set(),
    cachingTrackIds: new Set(),
    cachedTracksList: [],
    storageStats: {
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
    },
    settings: { ...DEFAULT_CACHE_SETTINGS },
    isOffline: typeof navigator !== 'undefined' ? !navigator.onLine : false,
    storageModalOpen: false,
    offlineFilterActive: false,

    init: async () => {
      if (isInitialized) return
      isInitialized = true

      if (typeof window !== 'undefined') {
        window.addEventListener('online', handleOnline)
        window.addEventListener('offline', handleOffline)
        window.addEventListener('tmusic:cache-updated', handleCacheUpdated)
      }

      const [settings, ids, tracks, stats] = await Promise.all([
        getCacheSettings(),
        getCachedTrackIds(),
        getAllCachedTracks(),
        getStorageBreakdown(),
      ])

      set({
        settings,
        cachedTrackIds: ids,
        cachedTracksList: tracks,
        storageStats: stats,
        isOffline: typeof navigator !== 'undefined' ? !navigator.onLine : false,
      })

      // Run eviction on launch (cleans up any old files based on TTL / limit)
      runEviction().catch((e) => log.debug('launch eviction failed', { error: e?.message }))
    },

    dispose: () => {
      if (typeof window !== 'undefined') {
        window.removeEventListener('online', handleOnline)
        window.removeEventListener('offline', handleOffline)
        window.removeEventListener('tmusic:cache-updated', handleCacheUpdated)
      }
      isInitialized = false
    },

    setStorageModalOpen: (open) => set({ storageModalOpen: Boolean(open) }),

    setOfflineFilterActive: (active) => set({ offlineFilterActive: Boolean(active) }),

    updateSettings: async (partial) => {
      const updated = await saveCacheSettings(partial)
      set({ settings: updated })
      await get().refreshStats()
    },

    refreshStats: async () => {
      const [ids, tracks, stats] = await Promise.all([
        getCachedTrackIds(),
        getAllCachedTracks(),
        getStorageBreakdown(),
      ])
      set({
        cachedTrackIds: ids,
        cachedTracksList: tracks,
        storageStats: stats,
      })
    },

    cacheTrack: async (track) => {
      if (!track || track.id == null) return false
      const fileId = Number(track.id)

      set((state) => {
        const next = new Set(state.cachingTrackIds)
        next.add(fileId)
        return { cachingTrackIds: next }
      })

      try {
        const ok = await downloadTrackForOffline(track)
        await get().refreshStats()
        return ok
      } finally {
        set((state) => {
          const next = new Set(state.cachingTrackIds)
          next.delete(fileId)
          return { cachingTrackIds: next }
        })
      }
    },

    removeTrack: async (fileId) => {
      if (fileId == null) return
      const id = Number(fileId)
      await deleteTrack(id)
      await get().refreshStats()
    },

    clearCategory: async (category) => {
      const ok = await clearCategory(category)
      await get().refreshStats()
      return ok
    },

    clearAll: async () => {
      const ok = await clearCategory('all')
      await get().refreshStats()
      return ok
    },

    isCached: (fileId) => {
      if (fileId == null) return false
      return get().cachedTrackIds.has(Number(fileId))
    },
  }
})
