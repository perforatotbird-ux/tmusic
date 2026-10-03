const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')

function createOfflineServiceContext() {
  const localStore = new Map()
  const dbData = {
    tracks: new Map(),
    covers: new Map(),
    metadata: new Map(),
    settings: new Map(),
  }

  const customEvents = []

  const fakeIdb = {
    open: (dbName, version) => {
      const db = {
        name: dbName,
        version,
        objectStoreNames: {
          contains: (name) => ['tracks', 'covers', 'metadata', 'settings'].includes(name),
        },
        createObjectStore: () => ({ createIndex: () => {} }),
        transaction: (storeNames, mode) => {
          return {
            objectStore: (name) => {
              const map = dbData[name] || new Map()
              return {
                get: (key) => {
                  const r = { result: map.get(key) }
                  setTimeout(() => r.onsuccess && r.onsuccess({ target: r }), 0)
                  return r
                },
                put: (val) => {
                  const key = val.id ?? val.fileId ?? val.url ?? val.key
                  map.set(key, val)
                  const r = { result: key }
                  setTimeout(() => r.onsuccess && r.onsuccess({ target: r }), 0)
                  return r
                },
                delete: (key) => {
                  map.delete(key)
                  const r = {}
                  setTimeout(() => r.onsuccess && r.onsuccess({ target: r }), 0)
                  return r
                },
                clear: () => {
                  map.clear()
                  const r = {}
                  setTimeout(() => r.onsuccess && r.onsuccess({ target: r }), 0)
                  return r
                },
                getAll: () => {
                  const r = { result: Array.from(map.values()) }
                  setTimeout(() => r.onsuccess && r.onsuccess({ target: r }), 0)
                  return r
                },
                getAllKeys: () => {
                  const r = { result: Array.from(map.keys()) }
                  setTimeout(() => r.onsuccess && r.onsuccess({ target: r }), 0)
                  return r
                },
                openCursor: () => {
                  const items = Array.from(map.values())
                  let idx = 0
                  const reqCursor = {}
                  function step() {
                    if (idx < items.length) {
                      const item = items[idx]
                      reqCursor.result = {
                        value: item,
                        continue: () => {
                          idx++
                          setTimeout(step, 0)
                        },
                      }
                      reqCursor.onsuccess && reqCursor.onsuccess({ target: reqCursor })
                    } else {
                      reqCursor.result = null
                      reqCursor.onsuccess && reqCursor.onsuccess({ target: reqCursor })
                    }
                  }
                  setTimeout(step, 0)
                  return reqCursor
                },
                index: (idxName) => ({
                  getAll: () => {
                    const sorted = Array.from(map.values()).sort(
                      (a, b) => (a[idxName] || 0) - (b[idxName] || 0)
                    )
                    const r = { result: sorted }
                    setTimeout(() => r.onsuccess && r.onsuccess({ target: r }), 0)
                    return r
                  },
                  openCursor: (range) => {
                    const sorted = Array.from(map.values()).sort(
                      (a, b) => (a[idxName] || 0) - (b[idxName] || 0)
                    )
                    const filtered = range?.upper != null
                      ? sorted.filter((it) => (it[idxName] || 0) <= range.upper)
                      : sorted

                    let idx = 0
                    const reqCursor = {}
                    function step() {
                      if (idx < filtered.length) {
                        const item = filtered[idx]
                        reqCursor.result = {
                          value: item,
                          delete: () => {
                            const k = item.id ?? item.fileId ?? item.url ?? item.key
                            map.delete(k)
                          },
                          continue: () => {
                            idx++
                            setTimeout(step, 0)
                          },
                        }
                        reqCursor.onsuccess && reqCursor.onsuccess({ target: reqCursor })
                      } else {
                        reqCursor.result = null
                        reqCursor.onsuccess && reqCursor.onsuccess({ target: reqCursor })
                      }
                    }
                    setTimeout(step, 0)
                    return reqCursor
                  },
                }),
              }
            },
          }
        },
      }

      const req = { result: db }
      setTimeout(() => {
        if (req.onupgradeneeded) req.onupgradeneeded({ target: req })
        if (req.onsuccess) req.onsuccess({ target: req })
      }, 0)
      return req
    },
  }

  const windowMock = {
    indexedDB: fakeIdb,
    dispatchEvent: (e) => customEvents.push(e),
    addEventListener: () => {},
    removeEventListener: () => {},
  }

  const context = {
    window: windowMock,
    indexedDB: fakeIdb,
    IDBKeyRange: {
      upperBound: (upper) => ({ upper }),
    },
    CustomEvent: class CustomEvent {
      constructor(type, init) {
        this.type = type
        this.detail = init?.detail
      }
    },
    navigator: {
      storage: {
        estimate: async () => ({ usage: 50 * 1024 * 1024, quota: 10 * 1024 * 1024 * 1024 }),
      },
      onLine: true,
    },
    localStorage: {
      getItem: (k) => localStore.get(k) ?? null,
      setItem: (k, v) => localStore.set(k, v),
      removeItem: (k) => localStore.delete(k),
    },
    setTimeout,
    clearTimeout,
    Promise,
    Array,
    Date,
    Math,
    console,
    getNativeStorageInfo: () => ({ totalBytes: 64000000000, availableBytes: 32000000000 }),
  }

  const source = fs.readFileSync(path.join(__dirname, '../src/services/offlineStorageService.js'), 'utf8')
    .replace(/^import .*\r?\n/gm, '')
    .replace('export const DEFAULT_CACHE_SETTINGS =', 'globalThis.DEFAULT_CACHE_SETTINGS =')
    .replace(/export /g, '')

  vm.createContext(context)
  vm.runInContext(source, context)

  return { context, dbData, localStore, customEvents }
}

test('DEFAULT_CACHE_SETTINGS has sensible mobile Telegram-style defaults', () => {
  const { context } = createOfflineServiceContext()
  const defaults = context.DEFAULT_CACHE_SETTINGS
  assert.equal(defaults.cacheTtlDays, 7) // 7 days default
  assert.equal(defaults.maxCacheSizeMb, 1024) // 1 GB default
  assert.equal(defaults.cacheAudio, true)
  assert.equal(defaults.cacheCovers, true)
  assert.equal(defaults.cacheLyrics, true)
  assert.equal(defaults.cacheCatalog, true)
  assert.equal(defaults.autoCacheOnPlay, true)
})

test('getCacheSettings returns defaults on fresh installation and saves custom settings', async () => {
  const { context } = createOfflineServiceContext()
  const initial = await context.getCacheSettings()
  assert.equal(initial.cacheTtlDays, 7)
  assert.equal(initial.cacheAudio, true)

  const updated = await context.saveCacheSettings({
    cacheTtlDays: 30, // 1 month
    maxCacheSizeMb: 500, // 500 MB
    autoCacheOnPlay: false,
  })

  assert.equal(updated.cacheTtlDays, 30)
  assert.equal(updated.maxCacheSizeMb, 500)
  assert.equal(updated.autoCacheOnPlay, false)
  // Preserved defaults for unset fields
  assert.equal(updated.cacheAudio, true)
  assert.equal(updated.cacheCovers, true)

  const reloaded = await context.getCacheSettings()
  assert.equal(reloaded.cacheTtlDays, 30)
  assert.equal(reloaded.maxCacheSizeMb, 500)
  assert.equal(reloaded.autoCacheOnPlay, false)
})

test('TTL eviction removes expired records but retains fresh records', async () => {
  const { context, dbData } = createOfflineServiceContext()
  const now = Date.now()
  const day = 24 * 60 * 60 * 1000

  // Configure TTL to 7 days
  await context.saveCacheSettings({ cacheTtlDays: 7 })

  // Insert fresh track (accessed 2 days ago)
  dbData.tracks.set(101, {
    fileId: 101,
    name: 'Fresh Track.mp3',
    size: 5 * 1024 * 1024,
    lastAccessedAt: now - 2 * day,
  })

  // Insert expired track (accessed 10 days ago)
  dbData.tracks.set(102, {
    fileId: 102,
    name: 'Expired Track.mp3',
    size: 10 * 1024 * 1024,
    lastAccessedAt: now - 10 * day,
  })

  // Insert expired cover
  dbData.covers.set('cov_1', {
    url: 'cov_1',
    size: 200 * 1024,
    lastAccessedAt: now - 15 * day,
  })

  // Run eviction
  await context.runEviction()

  assert.equal(dbData.tracks.has(101), true)
  assert.equal(dbData.tracks.has(102), false)
  assert.equal(dbData.covers.has('cov_1'), false)
})

test('TTL eviction with cacheTtlDays=0 (Forever) never evicts records based on time', async () => {
  const { context, dbData } = createOfflineServiceContext()
  const now = Date.now()
  const year = 365 * 24 * 60 * 60 * 1000

  // 0 means keep forever
  await context.saveCacheSettings({ cacheTtlDays: 0, maxCacheSizeMb: 0 })

  dbData.tracks.set(201, {
    fileId: 201,
    name: 'Ancient Track.mp3',
    size: 8 * 1024 * 1024,
    lastAccessedAt: now - year,
  })

  await context.runEviction()
  assert.equal(dbData.tracks.has(201), true)
})

test('LRU limit eviction removes oldest accessed items when cache exceeds maxCacheSizeMb', async () => {
  const { context, dbData } = createOfflineServiceContext()
  const now = Date.now()

  // 15 MB limit
  await context.saveCacheSettings({ cacheTtlDays: 0, maxCacheSizeMb: 15 })

  // 3 tracks, 10 MB each = 30 MB total
  dbData.tracks.set(1, { fileId: 1, name: 'Oldest.mp3', size: 10 * 1024 * 1024, lastAccessedAt: now - 3000 })
  dbData.tracks.set(2, { fileId: 2, name: 'Middle.mp3', size: 10 * 1024 * 1024, lastAccessedAt: now - 2000 })
  dbData.tracks.set(3, { fileId: 3, name: 'Newest.mp3', size: 10 * 1024 * 1024, lastAccessedAt: now - 1000 })

  await context.runEviction()
  // Total was 30MB, limit is 15MB. Evicting track 1 (10MB) leaves 20MB. Evicting track 2 leaves 10MB <= 15MB.
  assert.equal(dbData.tracks.has(1), false)
  assert.equal(dbData.tracks.has(2), false)
  assert.equal(dbData.tracks.has(3), true)
})

test('clearCategory deletes only the specified category and clearCategory("all") clears everything', async () => {
  const { context, dbData } = createOfflineServiceContext()

  dbData.tracks.set(1, { fileId: 1, size: 1000 })
  dbData.covers.set('c1', { url: 'c1', size: 500 })
  dbData.metadata.set('m1', { key: 'm1', size: 200 })

  await context.clearCategory('covers')
  assert.equal(dbData.tracks.size, 1)
  assert.equal(dbData.covers.size, 0)
  assert.equal(dbData.metadata.size, 1)

  await context.clearCategory('all')
  assert.equal(dbData.tracks.size, 0)
  assert.equal(dbData.covers.size, 0)
  assert.equal(dbData.metadata.size, 0)
})

test('saveTrack stores release metadata from any queue-track shape', async () => {
  const { context } = createOfflineServiceContext()
  const blob = { size: 1234, type: 'audio/mpeg' }

  // Catalog/stream shape enriched by withReleaseMeta.
  await context.saveTrack(
    { id: 1, name: 'Load.mp3', artist: 'Metallica', album: 'Reload', coverUrl: '/covers/r.jpg' },
    blob,
  )
  // Search-API shape (backend key names).
  await context.saveTrack(
    { id: 2, name: 'Song2.mp3', artist_name: 'Nirvana', album_title: 'Nevermind', cover_url: '/covers/n.jpg' },
    blob,
  )

  const rows = await context.getAllCachedTracks()
  const byId = Object.fromEntries(rows.map((r) => [r.id, r]))
  assert.equal(byId[1].artist, 'Metallica')
  assert.equal(byId[1].album, 'Reload')
  assert.equal(byId[1].coverUrl, '/covers/r.jpg')
  assert.equal(byId[2].artist, 'Nirvana')
  assert.equal(byId[2].album, 'Nevermind')
  assert.equal(byId[2].coverUrl, '/covers/n.jpg')
})

test('refreshCachedTrackMeta heals blank rows without touching the blob', async () => {
  const { context, dbData } = createOfflineServiceContext()
  const blob = { size: 999, type: 'audio/mpeg' }

  // Row cached before the queue carried release context: blanks everywhere.
  await context.saveTrack({ id: 7, name: 'Load.mp3' }, blob)
  assert.equal((await context.getAllCachedTracks())[0].artist, '')

  const healed = await context.refreshCachedTrackMeta({
    id: 7, artist: 'Metallica', album: 'Reload', coverUrl: '/covers/r.jpg', title: 'Load',
  })
  assert.equal(healed, true)

  const [row] = await context.getAllCachedTracks()
  assert.equal(row.artist, 'Metallica')
  assert.equal(row.album, 'Reload')
  assert.equal(row.coverUrl, '/covers/r.jpg')
  // Audio blob entry untouched (same stored object, same size).
  assert.equal(dbData.tracks.get(7).size, 999)
})

test('refreshCachedTrackMeta never overwrites real values and no-ops cleanly', async () => {
  const { context } = createOfflineServiceContext()
  const blob = { size: 10, type: 'audio/mpeg' }

  await context.saveTrack(
    { id: 3, name: 'X.mp3', artist: 'Nirvana', album: 'Nevermind', coverUrl: '/covers/n.jpg', title: 'X' },
    blob,
  )
  const kept = await context.refreshCachedTrackMeta({
    id: 3, artist: 'Metallica', album: 'Reload', coverUrl: '/covers/r.jpg', title: 'Other',
  })
  assert.equal(kept, false)
  // The stored title is left alone even when the incoming one differs.
  assert.equal((await context.getAllCachedTracks())[0].title, 'X')
  const [row] = await context.getAllCachedTracks()
  assert.equal(row.artist, 'Nirvana')
  assert.equal(row.album, 'Nevermind')
  assert.equal(row.coverUrl, '/covers/n.jpg')

  // Unknown id and metadata-less track: both no-ops.
  assert.equal(await context.refreshCachedTrackMeta({ id: 404, artist: 'Ghost' }), false)
  assert.equal(await context.refreshCachedTrackMeta({ id: 3 }), false)
  assert.equal(await context.refreshCachedTrackMeta(null), false)
})

test('getStorageBreakdown computes audio, covers and metadata sizes accurately', async () => {
  const { context, dbData } = createOfflineServiceContext()

  dbData.tracks.set(1, { fileId: 1, size: 5000 })
  dbData.tracks.set(2, { fileId: 2, size: 3000 })
  dbData.covers.set('c1', { url: 'c1', size: 1200 })
  dbData.metadata.set('m1', { key: 'm1', size: 800 })

  const stats = await context.getStorageBreakdown()
  assert.equal(stats.audioBytes, 8000)
  assert.equal(stats.audioCount, 2)
  assert.equal(stats.coversBytes, 1200)
  assert.equal(stats.coversCount, 1)
  assert.equal(stats.metaBytes, 800)
  assert.equal(stats.totalBytes, 10000)
  assert.equal(stats.deviceTotalBytes, 64000000000)
  assert.equal(stats.deviceAvailableBytes, 32000000000)
})
