import { create } from 'zustand'
import { playbackApi } from '../api/playback'

let epoch = 0
let revision = 0
const pending = new Map()
const changed = new Map()

export const useFavoritesStore = create((set, get) => ({
  ids: new Set(), loaded: false, loading: false, error: null,
  clearError: () => set({ error: null }),
  isFavorite: (fileId) => get().ids.has(fileId),
  load: async ({ force = false } = {}) => {
    if (get().loading || (get().loaded && !force)) return
    const generation = epoch
    const version = revision
    const alreadyPending = new Set(pending.keys())
    set({ loading: true })
    try {
      const res = await playbackApi.listFavorites(1000)
      if (generation !== epoch) return
      const ids = new Set((res?.items || []).map((x) => x.file_id))
      const protectedIds = new Set([...alreadyPending, ...pending.keys()])
      for (const [id, updatedAt] of changed) if (updatedAt > version) protectedIds.add(id)
      for (const id of protectedIds) {
        if (get().ids.has(id)) ids.add(id)
        else ids.delete(id)
      }
      set({ ids, loaded: true, error: null })
    } catch {
      if (generation === epoch) set({ error: 'Не удалось обновить избранное. Попробуйте ещё раз.' })
      // Keep the last known state; callers may retry.
    } finally {
      if (generation === epoch) set({ loading: false })
    }
  },
  reset: () => {
    epoch += 1
    revision += 1
    pending.clear()
    changed.clear()
    set({ ids: new Set(), loaded: false, loading: false, error: null })
  },
  toggle: (fileId) => {
    if (!fileId) return Promise.resolve({ ok: false, code: 'invalid_file_id' })
    const generation = epoch
    const previous = pending.get(fileId) || Promise.resolve()
    const operation = previous.then(async () => {
      if (generation !== epoch) return { ok: false, code: 'cancelled' }
      changed.set(fileId, ++revision)
      const wasFavorite = get().ids.has(fileId)
      const update = (value) => {
        const ids = new Set(get().ids)
        if (value) ids.add(fileId)
        else ids.delete(fileId)
        set({ ids })
      }
      set({ error: null })
      update(!wasFavorite)
      try {
        const res = await playbackApi.toggleFavorite(fileId)
        if (generation !== epoch) return { ok: false, code: 'cancelled' }
        if (typeof res?.is_favorite !== 'boolean') throw new Error('Invalid favorite response')
        changed.set(fileId, ++revision)
        update(res.is_favorite)
        return { ok: true, isFavorite: !!res?.is_favorite }
      } catch (error) {
        if (generation !== epoch) return { ok: false, code: 'cancelled' }
        changed.set(fileId, ++revision)
        update(wasFavorite) // Roll back this ID, not somebody else's mutation.
        const message = 'Не удалось подтвердить изменение избранного. Обновите список перед повтором.'
        set({ error: message })
        return { ok: false, code: error?.code || 'favorite_failed', message, isFavorite: wasFavorite }
      }
    }).finally(() => {
      if (pending.get(fileId) === operation) pending.delete(fileId)
    })
    pending.set(fileId, operation)
    return operation
  },
}))
