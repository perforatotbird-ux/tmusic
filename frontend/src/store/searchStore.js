/**
 * T-Music Unified Search Store (Zustand)
 * Manages search state, category filters, AbortController lifecycle, and search history.
 */
import { create } from 'zustand'
import { searchApi } from '../api/search'

const RECENT_KEY = 'tmusic:recent_searches'
const MAX_RECENT = 10

function loadRecent() {
  try {
    const raw = localStorage.getItem(RECENT_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed.filter((s) => typeof s === 'string' && s.trim()) : []
  } catch {
    return []
  }
}

function saveRecent(items) {
  try {
    localStorage.setItem(RECENT_KEY, JSON.stringify(items.slice(0, MAX_RECENT)))
  } catch {
    /* ignore storage errors */
  }
}

let activeAbortController = null

export const useSearchStore = create((set, get) => ({
  isOpen: false,
  query: '',
  activeCategory: 'all', // 'all' | 'tracks' | 'artists' | 'albums'
  isLoading: false,
  isLoadingMore: false,
  error: null,
  recentSearches: loadRecent(),

  // Unified results
  unifiedResults: {
    artists: [],
    albums: [],
    tracks: [],
    total_artists: 0,
    total_albums: 0,
    total_tracks: 0,
  },

  // Category specific paginated results
  categoryResults: {
    items: [],
    total: 0,
    offset: 0,
    limit: 30,
    hasMore: false,
  },

  openSearch: (initialQuery = '') => {
    set({
      isOpen: true,
      query: initialQuery,
      activeCategory: 'all',
      error: null,
    })
    if (initialQuery.trim()) {
      get().executeSearch(initialQuery, 'all')
    }
  },

  closeSearch: () => {
    if (activeAbortController) {
      try {
        activeAbortController.abort()
      } catch {
        /* ignore */
      }
      activeAbortController = null
    }
    set({ isOpen: false })
  },

  setQuery: (query) => {
    set({ query })
    const trimmed = query.trim()
    if (!trimmed) {
      if (activeAbortController) {
        try {
          activeAbortController.abort()
        } catch {
          /* ignore */
        }
        activeAbortController = null
      }
      set({
        isLoading: false,
        error: null,
        unifiedResults: {
          artists: [],
          albums: [],
          tracks: [],
          total_artists: 0,
          total_albums: 0,
          total_tracks: 0,
        },
        categoryResults: { items: [], total: 0, offset: 0, limit: 30, hasMore: false },
      })
      return
    }
    get().executeSearch(trimmed, get().activeCategory)
  },

  setActiveCategory: (category) => {
    set({ activeCategory: category })
    const q = get().query.trim()
    if (q) {
      get().executeSearch(q, category)
    }
  },

  executeSearch: async (q, category) => {
    if (activeAbortController) {
      try {
        activeAbortController.abort()
      } catch {
        /* ignore */
      }
    }
    const controller = new AbortController()
    activeAbortController = controller

    set({ isLoading: true, error: null })

    try {
      if (category === 'all') {
        const data = await searchApi.unified(q, {
          signal: controller.signal,
          limitArtists: 6,
          limitAlbums: 8,
          limitTracks: 15,
        })
        if (controller.signal.aborted) return
        set({
          unifiedResults: data || {
            artists: [],
            albums: [],
            tracks: [],
            total_artists: 0,
            total_albums: 0,
            total_tracks: 0,
          },
          isLoading: false,
        })
      } else {
        let resp
        if (category === 'tracks') {
          resp = await searchApi.tracks(q, { signal: controller.signal, limit: 30, offset: 0 })
        } else if (category === 'artists') {
          resp = await searchApi.artists(q, { signal: controller.signal, limit: 30, offset: 0 })
        } else if (category === 'albums') {
          resp = await searchApi.albums(q, { signal: controller.signal, limit: 30, offset: 0 })
        }
        if (controller.signal.aborted) return

        const items = resp?.items || []
        const total = resp?.total || 0
        set({
          categoryResults: {
            items,
            total,
            offset: 0,
            limit: 30,
            hasMore: items.length < total,
          },
          isLoading: false,
        })
      }
    } catch (err) {
      if (err?.name === 'AbortError') return
      console.error('Search failed:', err)
      set({
        isLoading: false,
        error: 'Не удалось выполнить поиск. Проверьте соединение.',
      })
    }
  },

  loadMoreCategory: async () => {
    const { query, activeCategory, categoryResults, isLoadingMore } = get()
    if (isLoadingMore || !categoryResults.hasMore || activeCategory === 'all') return

    const nextOffset = categoryResults.items.length
    set({ isLoadingMore: true })

    try {
      let resp
      if (activeCategory === 'tracks') {
        resp = await searchApi.tracks(query, { limit: 30, offset: nextOffset })
      } else if (activeCategory === 'artists') {
        resp = await searchApi.artists(query, { limit: 30, offset: nextOffset })
      } else if (activeCategory === 'albums') {
        resp = await searchApi.albums(query, { limit: 30, offset: nextOffset })
      }

      const newItems = resp?.items || []
      const merged = [...categoryResults.items, ...newItems]
      set({
        categoryResults: {
          items: merged,
          total: resp?.total || categoryResults.total,
          offset: nextOffset,
          limit: 30,
          hasMore: merged.length < (resp?.total || categoryResults.total),
        },
        isLoadingMore: false,
      })
    } catch (err) {
      console.error('Failed to load more search items:', err)
      set({ isLoadingMore: false })
    }
  },

  addRecentSearch: (query) => {
    const trimmed = (query || '').trim()
    if (!trimmed) return
    const current = get().recentSearches.filter((s) => s.toLowerCase() !== trimmed.toLowerCase())
    const updated = [trimmed, ...current].slice(0, MAX_RECENT)
    saveRecent(updated)
    set({ recentSearches: updated })
  },

  removeRecentSearch: (query) => {
    const updated = get().recentSearches.filter((s) => s !== query)
    saveRecent(updated)
    set({ recentSearches: updated })
  },

  clearRecentSearches: () => {
    saveRecent([])
    set({ recentSearches: [] })
  },
}))
