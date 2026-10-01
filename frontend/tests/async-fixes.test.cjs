const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const { create } = require('zustand')
const noop = () => {}
const logger = { info: noop, debug: noop, warn: noop, error: noop }
const deferred = () => {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
const tick = () => new Promise(setImmediate)
const source = (name) => fs.readFileSync(path.join(__dirname, '../src', name), 'utf8')
function load(name, context = {}) {
  vm.createContext(context)
  const text = source(name).replace(/^import .*\n/gm, '').replace(/export /g, '')
    .replace('const useAuthStore =', 'globalThis.store =')
    .replace('const useFavoritesStore =', 'globalThis.store =')
  vm.runInContext(text, context)
  return context
}
function favorites(api) { return load('store/favoritesStore.js', { create, playbackApi: api }).store }
function auth(api) {
  return load('store/authStore.js', { create, authApi: api, bustCache: noop,
    usePlayerStore: { getState: () => ({ reset: noop, hydrate: noop }) },
    useFavoritesStore: { getState: () => ({ reset: noop }) }, createLogger: () => logger }).store
}

test('resume ignores a late previous-track reply (including A -> B -> A)', async () => {
  const d = deferred()
  const ctx = load('services/playbackProgress.js', { playbackApi: { getPosition: () => d.promise } })
  const audio = { currentTime: 0, duration: 300 }
  ctx.onTrackChanged(1)
  const pending = ctx.tryRestorePosition(audio, 1)
  ctx.onTrackChanged(2); ctx.onTrackChanged(1)
  d.resolve({ position_sec: 42 }); await pending
  assert.equal(audio.currentTime, 0)
})
test('resume applies only finite offset to matching audio binding', async () => {
  for (const [position, binding, expected] of [[42, '1', 42], [42, '2', 0], ['NaN', '1', 0]]) {
    const ctx = load('services/playbackProgress.js', { playbackApi: { getPosition: async () => ({ position_sec: position }) } })
    const audio = { currentTime: 0, duration: 300, getAttribute: () => binding }
    ctx.onTrackChanged(1); await ctx.tryRestorePosition(audio, 1)
    assert.equal(audio.currentTime, expected)
  }
})
test('cache invalidation detaches old pending request and preserves new single flight', async () => {
  const ctx = load('api/cache.js'), oldReply = deferred(), newReply = deferred()
  const old = ctx.memoize('library/key', 300000, () => oldReply.promise)
  ctx.bustCache('library/')
  const fresh = ctx.memoize('library/key', 300000, () => newReply.promise)
  assert.notEqual(old, fresh)
  oldReply.resolve('old'); assert.equal(await old, 'old')
  assert.equal(ctx.memoize('library/key', 300000, () => 'bad'), fresh)
  newReply.resolve('new'); assert.equal(await fresh, 'new')
  assert.equal(await ctx.memoize('library/key', 300000, () => 'bad'), 'new')
})
test('cache fetcher errors reject normally and allow a retry', async () => {
  const ctx = load('api/cache.js')
  await assert.rejects(ctx.memoize('x', 500, () => { throw Error('synthetic') }))
  assert.equal(await ctx.memoize('x', 500, () => 1), 1)
})
test('late me success and failure never alter a newer session/logout', async () => {
  const d = deferred(), store = auth({ getMe: () => d.promise, logout: async () => {} })
  const checking = store.getState().checkAuth()
  await tick()
  await store.getState().logout()
  d.resolve({ id: 1 }); await checking
  assert.equal(store.getState().isAuthenticated, false)
  const d2 = deferred(); let calls = 0
  const s2 = auth({ getMe: () => ++calls === 1 ? d2.promise : Promise.resolve({ id: 2 }) })
  const stale = s2.getState().checkAuth(); await s2.getState().checkAuth()
  d2.reject(Error('old')); await stale
  assert.equal(s2.getState().user.id, 2)
})
test('logout cookie mutation completes before a fresh login', async () => {
  const d = deferred(), events = []
  const store = auth({ logout: () => { events.push('logout'); return d.promise },
    login: async () => { events.push('login') }, getMe: async () => ({ id: 2 }) })
  const loggingOut = store.getState().logout(); await tick()
  const loggingIn = store.getState().loginWithPassword('synthetic', 'placeholder'); await tick()
  assert.deepEqual(events, ['logout'])
  d.resolve(); await loggingOut; assert.equal(await loggingIn, true)
  assert.deepEqual(events, ['logout', 'login']); assert.equal(store.getState().user.id, 2)
})
test('favorites reset invalidates pending load and pending toggle', async () => {
  const d = deferred(), mutation = deferred()
  const store = favorites({ listFavorites: () => d.promise, toggleFavorite: () => mutation.promise })
  const loading = store.getState().load(); const toggle = store.getState().toggle(1); await tick()
  store.getState().reset()
  d.resolve({ items: [{ file_id: 1 }] }); mutation.resolve({ is_favorite: true })
  await Promise.all([loading, toggle])
  assert.equal(store.getState().ids.size, 0); assert.equal(store.getState().loaded, false)
})
test('failed favorite rollback preserves another successful favorite', async () => {
  const one = deferred(), two = deferred()
  const store = favorites({ toggleFavorite: (id) => id === 1 ? one.promise : two.promise })
  const p1 = store.getState().toggle(1), p2 = store.getState().toggle(2); await tick()
  two.resolve({ is_favorite: true }); await p2
  one.reject(Error('synthetic')); await p1
  assert.equal(store.getState().ids.has(1), false); assert.equal(store.getState().ids.has(2), true)
})
test('same-file toggles are serialized', async () => {
  const first = deferred(); let calls = 0
  const store = favorites({ toggleFavorite: () => ++calls === 1 ? first.promise : Promise.resolve({ is_favorite: false }) })
  const p1 = store.getState().toggle(1), p2 = store.getState().toggle(1); await tick()
  assert.equal(calls, 1); first.resolve({ is_favorite: true }); await Promise.all([p1, p2])
  assert.equal(calls, 2); assert.equal(store.getState().ids.has(1), false)
})
test('favorites refresh cannot erase a concurrent optimistic update', async () => {
  const loadReply = deferred(), mutation = deferred()
  const store = favorites({ listFavorites: () => loadReply.promise, toggleFavorite: () => mutation.promise })
  const toggle = store.getState().toggle(1); await tick()
  const loading = store.getState().load({ force: true })
  loadReply.resolve({ items: [{ file_id: 99 }] }); await loading
  assert.equal(store.getState().ids.has(1), true)
  mutation.resolve({ is_favorite: true }); await toggle
  assert.equal(store.getState().ids.has(99), true)
  assert.equal(store.getState().loaded, true)
})
test('pagination ignores old catalog and deduplicates double click', async () => {
  let text = source('components/MusicLibrary.jsx')
  text = text.slice(text.indexOf('  const loadMore = async () => {'), text.indexOf('  // Панель под сеткой:'))
    .replace('const loadMore = async () =>', 'async function loadMore()')
  let items = [{ id: 'old' }], calls = 0
  const reply = deferred(), ctx = { loadingMore: false, hasMore: true, moreRequestRef: { current: null },
    loadSeq: { current: 1 }, catalogKeyRef: { current: 'old' }, debouncedSearch: '', search: '', viewKind: 'albums',
    items, LIB_PAGE_SIZE: 200, libraryApi: { getAlbums: () => { calls++; return reply.promise } },
    setLoadingMore: noop, setItems: (fn) => { items = fn(items) }, setHasMore: noop, console }
  vm.createContext(ctx); vm.runInContext(text, ctx)
  const pending = ctx.loadMore(); await ctx.loadMore(); assert.equal(calls, 1)
  ctx.catalogKeyRef.current = 'new'; ctx.loadSeq.current++; items = [{ id: 'new' }]
  reply.resolve([{ id: 'old-page2' }]); await pending
  assert.deepEqual(items, [{ id: 'new' }])
})
test('folder mirror uses one batch request and displays write failure', async () => {
  const text = source('components/FolderBrowser.jsx')
  const start = text.indexOf('  const onPlayFolder = async (folder) => {')
  const body = text.slice(start, text.indexOf('\n  return (', start)).replace('const onPlayFolder = async (folder) =>', 'async function onPlayFolder(folder)')
  let calls = 0, notice
  const ctx = { playingId: null, setPlayingId: noop, isAuthenticated: true, playQueue: noop,
    vfsApi: { tracks: async () => [{ id: 3 }, { id: 4 }] },
    playlistsApi: { mirror: async (name, ids) => { calls++; assert.equal(name, 'Folder'); assert.deepEqual(Array.from(ids), [3, 4]); throw Error('synthetic') } },
    setNotice: (value) => { notice = value }, console: { error: noop } }
  vm.createContext(ctx); vm.runInContext(body, ctx); await ctx.onPlayFolder({ id: 1, name: 'Folder' })
  assert.equal(calls, 1); assert.equal(notice.kind, 'error')
})
test('serial polling prevents overlap; stopped late reply cannot touch a new poll', async () => {
  const timers = new Map(); let id = 0, calls = 0, statuses = []
  const d = deferred()
  const ctx = load('utils/serialPolling.js', { setTimeout: (fn) => { timers.set(++id, fn); return id }, clearTimeout: (key) => timers.delete(key) })
  const first = ctx.startSerialPolling(() => { calls++; return d.promise }, (st) => statuses.push(st))
  const callback = timers.values().next().value; timers.clear(); const pending = callback()
  assert.equal(calls, 1); assert.equal(timers.size, 0)
  first.stop()
  const next = ctx.startSerialPolling(async () => ({ status: 'running' }), (st) => statuses.push(st))
  d.resolve({ status: 'completed' }); await pending
  assert.equal(statuses.length, 0); assert.equal(timers.size, 1)
  next.stop(); assert.equal(timers.size, 0)
})
test('poll errors are handled and retry is scheduled, terminal status stops', async () => {
  const timers = new Map(); let id = 0, errors = 0
  const ctx = load('utils/serialPolling.js', { setTimeout: (fn) => { timers.set(++id, fn); return id }, clearTimeout: (key) => timers.delete(key) })
  let first = true
  ctx.startSerialPolling(async () => { if (first) { first = false; throw Error('synthetic') }; return { status: 'completed' } }, noop, { onError: () => { errors++ } })
  let cb = timers.values().next().value; timers.clear(); await cb(); assert.equal(errors, 1); assert.equal(timers.size, 1)
  cb = timers.values().next().value; timers.clear(); await cb(); assert.equal(timers.size, 0)
})


test('me lookup waits for a pending cookie mutation', async () => {
  const d = deferred(); let meCalls = 0
  const store = auth({ logout: () => d.promise, getMe: async () => { meCalls++; return { id: 2 } } })
  const logout = store.getState().logout(); await tick()
  const lookup = store.getState().checkAuth(); await tick(); assert.equal(meCalls, 0)
  d.resolve(); await logout; await lookup; assert.equal(meCalls, 1)
})

test('old import run reply cannot overwrite new result or stop its polling', async () => {
  const src = source('components/AdminVfsImport.jsx')
  const start = src.indexOf('  const handleRunImport = async () => {')
  const handler = src.slice(start, src.indexOf('  // Recursive Tree Node', start))
    .replace('const handleRunImport = async () =>', 'async function handleRunImport()')
  const old = deferred(), fresh = deferred(), controllers = []
  const results = [], errors = []
  let calls = 0
  const ctx = { staged: { import_id: 'old' }, pollRef: { current: null }, importEpoch: { current: 0 },
    importBusy: false, preview: null, selectedFolders: new Set([1]), selectedFiles: new Set(),
    targetRootId: '', preserveParentPath: true, catalogKind: 'compilation', formatNumber: String,
    setImportBusy: noop, setError: (v) => { if (v) errors.push(v) }, setProgress: noop, setResult: (r) => results.push(r), setPreview: noop,
    window: { confirm: () => true, dispatchEvent: noop }, CustomEvent: function () {},
    startSerialPolling: () => { const c = { stopped: false, stop() { this.stopped = true } }; controllers.push(c); return c },
    adminApi: { getVfsImportStatus: noop, runTreeImport: (id) => { calls++; return id === 'old' ? old.promise : fresh.promise } },
    stopPolling: () => { ctx.importEpoch.current++; ctx.pollRef.current?.stop(); ctx.pollRef.current = null }, onImported: noop }
  vm.createContext(ctx); vm.runInContext(handler, ctx)
  const oldRun = ctx.handleRunImport()
  await ctx.handleRunImport(); assert.equal(calls, 1) // immediate guard, without a React rerender
  ctx.stopPolling(); ctx.staged = { import_id: 'new' }
  const newRun = ctx.handleRunImport()
  old.resolve({ import_id: 'old' }); await oldRun
  assert.equal(results.length, 0); assert.equal(controllers[1].stopped, false)
  fresh.resolve({ import_id: 'new' }); await newRun
  assert.equal(results.length, 1); assert.equal(results[0].import_id, 'new'); assert.equal(errors.length, 0)
})
