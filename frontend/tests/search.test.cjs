const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')

function createSearchContext() {
  const localStore = new Map()

  const fakeLocalStorage = {
    getItem: (key) => localStore.get(key) || null,
    setItem: (key, val) => localStore.set(key, String(val)),
    removeItem: (key) => localStore.delete(key),
    clear: () => localStore.clear(),
  }

  const networkRequests = []

  const fakeSearchApi = {
    unified: async (query, options = {}) => {
      networkRequests.push({ type: 'unified', query, options })
      if (options.signal?.aborted) {
        const err = new Error('The operation was aborted')
        err.name = 'AbortError'
        throw err
      }
      return {
        query,
        artists: [{ id: 1, display_name: 'Artist 1' }],
        albums: [{ id: 1, title: 'Album 1' }],
        tracks: [{ id: 1, title: 'Track 1', file_name: '01. Track 1.mp3' }],
        total_artists: 1,
        total_albums: 1,
        total_tracks: 1,
      }
    },
    tracks: async (query, options = {}) => {
      networkRequests.push({ type: 'tracks', query, options })
      if (options.signal?.aborted) {
        const err = new Error('The operation was aborted')
        err.name = 'AbortError'
        throw err
      }
      return {
        query,
        items: [{ id: 1, title: 'Track 1' }],
        total: 50,
        limit: options.limit || 30,
        offset: options.offset || 0,
      }
    },
    artists: async (query, options = {}) => {
      networkRequests.push({ type: 'artists', query, options })
      return {
        query,
        items: [{ id: 1, display_name: 'Artist 1' }],
        total: 10,
        limit: options.limit || 30,
        offset: options.offset || 0,
      }
    },
    albums: async (query, options = {}) => {
      networkRequests.push({ type: 'albums', query, options })
      return {
        query,
        items: [{ id: 1, title: 'Album 1' }],
        total: 15,
        limit: options.limit || 30,
        offset: options.offset || 0,
      }
    },
  }

  // Load and adapt searchStore code in sandbox
  const storeFilePath = path.join(__dirname, '..', 'src', 'store', 'searchStore.js')
  let code = fs.readFileSync(storeFilePath, 'utf8')

  // Transform ESM into CommonJS for node:test vm
  code = code
    .replace(/import\s*\{[^}]*\}\s*from\s*['"]zustand['"]/g, '')
    .replace(/import\s*\{[^}]*\}\s*from\s*['"]\.\.\/api\/search['"]/g, '')
    .replace(/export\s+const\s+useSearchStore\s*=\s*/g, 'const useSearchStore = ')

  function createZustandMock(fn) {
    let state = {}
    const get = () => state
    const set = (partial) => {
      const next = typeof partial === 'function' ? partial(state) : partial
      state = { ...state, ...next }
    }
    state = fn(set, get)
    return {
      getState: get,
      setState: set,
    }
  }

  const context = {
    console,
    localStorage: fakeLocalStorage,
    searchApi: fakeSearchApi,
    create: createZustandMock,
    AbortController: globalThis.AbortController,
    networkRequests,
    module: { exports: {} },
    exports: {},
  }

  vm.createContext(context)
  vm.runInContext(code + '\nmodule.exports = { useSearchStore };', context)

  return {
    store: context.module.exports.useSearchStore,
    localStorage: fakeLocalStorage,
    networkRequests,
  }
}

test('searchStore initializes with defaults and loads recent searches from localStorage', () => {
  const { store, localStorage } = createSearchContext()
  localStorage.setItem('tmusic:recent_searches', JSON.stringify(['queen', 'rock']))

  // Re-init context to load saved storage
  const ctx2 = createSearchContext()
  ctx2.localStorage.setItem('tmusic:recent_searches', JSON.stringify(['queen', 'rock']))
  const state = ctx2.store.getState()

  assert.equal(state.isOpen, false)
  assert.equal(state.query, '')
  assert.equal(state.activeCategory, 'all')
})

test('openSearch and closeSearch toggle modal state and reset query appropriately', () => {
  const { store } = createSearchContext()

  store.getState().openSearch('metallica')
  assert.equal(store.getState().isOpen, true)
  assert.equal(store.getState().query, 'metallica')

  store.getState().closeSearch()
  assert.equal(store.getState().isOpen, false)
})

test('recent searches management: add, deduplicate, limit to 10, remove, clear', () => {
  const { store } = createSearchContext()

  store.getState().addRecentSearch('track 1')
  store.getState().addRecentSearch('track 2')
  store.getState().addRecentSearch('track 1') // duplicate should move to front

  const recent = store.getState().recentSearches
  assert.equal(recent.length, 2)
  assert.equal(recent[0], 'track 1')
  assert.equal(recent[1], 'track 2')

  // Remove single
  store.getState().removeRecentSearch('track 2')
  assert.equal(JSON.stringify(store.getState().recentSearches), JSON.stringify(['track 1']))

  // Clear all
  store.getState().clearRecentSearches()
  assert.equal(JSON.stringify(store.getState().recentSearches), JSON.stringify([]))
})

test('executeSearch handles category routing and updates results', async () => {
  const { store, networkRequests } = createSearchContext()

  // 1. Search 'all' (unified)
  await store.getState().executeSearch('love', 'all')
  assert.equal(networkRequests.length, 1)
  assert.equal(networkRequests[0].type, 'unified')
  assert.equal(store.getState().unifiedResults.total_tracks, 1)

  // 2. Search category 'tracks'
  await store.getState().executeSearch('love', 'tracks')
  assert.equal(networkRequests.length, 2)
  assert.equal(networkRequests[1].type, 'tracks')
  assert.equal(store.getState().categoryResults.total, 50)
  assert.equal(store.getState().categoryResults.hasMore, true)
})
