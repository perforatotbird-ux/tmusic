import { create } from 'zustand'
import { playbackApi } from '../api/playback'

/**
 * Shared favorite-track state: one fetch of ids, optimistic-ish toggle,
 * so AlbumDetail / player / home panel stay in sync.
 */
export const useFavoritesStore = create((set, get) => ({
  ids: new Set(),
  loaded: false,
  loading: false,

  isFavorite: (fileId) => get().ids.has(fileId),

  load: async ({ force = false } = {}) => {
    if (get().loading) return
    if (get().loaded && !force) return
    set({ loading: true })
    try {
      const res = await playbackApi.listFavorites(1000)
      const ids = new Set((res?.items || []).map((x) => x.file_id))
      set({ ids, loaded: true, loading: false })
    } catch {
      set({ loading: false })
    }
  },

  toggle: async (fileId) => {
    if (!fileId) return false
    const prev = get().ids
    const next = new Set(prev)
    const optimistic = !next.has(fileId)
    if (optimistic) next.add(fileId)
    else next.delete(fileId)
    set({ ids: next })
    try {
      const res = await playbackApi.toggleFavorite(fileId)
      const settled = new Set(get().ids)
      if (res?.is_favorite) settled.add(fileId)
      else settled.delete(fileId)
      set({ ids: settled, loaded: true })
      return !!res?.is_favorite
    } catch {
      set({ ids: prev })
      return prev.has(fileId)
    }
  },
}))
