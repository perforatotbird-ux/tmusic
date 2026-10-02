const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const { create } = require('zustand')
const noop = () => {}
const logger = { info: noop, debug: noop, warn: noop, error: noop }

// Exercise the original Zustand actions; isolate browser transport/timers.
function player() {
  const storage = new Map()
  const context = {
    create, streamApi: { getStreamUrl: (id) => '/stream/' + id, prefetchTracks: async () => {} },
    setMediaPlaybackState: noop, IS_NATIVE: true, createLogger: () => logger,
    localStorage: { getItem: (k) => storage.get(k) ?? null, setItem: (k, v) => storage.set(k, v), removeItem: (k) => storage.delete(k) },
    setTimeout: () => 0, setInterval: () => 0, clearTimeout: noop, clearInterval: noop,
    AbortController, fetch: async () => ({ ok: true, status: 206, arrayBuffer: async () => new ArrayBuffer(0) }),
  }
  const source = fs.readFileSync(path.join(__dirname, '../src/store/playerStore.js'), 'utf8')
    .replace(/^import .*\r?\n/gm, '').replace('export const usePlayerStore =', 'globalThis.usePlayerStore =')
  vm.createContext(context)
  vm.runInContext(source, context)
  return context.usePlayerStore
}
const tracks = [{ id: 1, name: 'One' }, { id: 2, name: 'Two' }, { id: 3, name: 'Three' }]
const ids = (queue) => Array.from(queue, (t) => t.id)

for (const [name, current, scheduled, expected] of [
  ['new track', 0, { id: 4, name: 'Four' }, [1, 4, 2, 3]],
  ['move history item', 1, tracks[0], [2, 1, 3]],
  ['move upcoming item', 0, tracks[2], [1, 3, 2]],
  ['append after last', 2, { id: 4, name: 'Four' }, [1, 2, 3, 4]],
]) {
  test('playNext preserves playing index: ' + name, () => {
    const store = player()
    store.setState({ queue: tracks, queueIndex: current, currentTrack: tracks[current] })
    store.getState().playNext(scheduled)
    const state = store.getState()
    assert.deepEqual(ids(state.queue), expected)
    assert.equal(state.currentTrack.id, tracks[current].id)
    assert.equal(state.queue[state.queueIndex].id, state.currentTrack.id)
    store.getState().nextTrack()
    assert.equal(store.getState().currentTrack.id, scheduled.id)
  })
}

test('playNext current last track is a no-op, never out of bounds', () => {
  const store = player()
  store.setState({ queue: tracks, queueIndex: 2, currentTrack: tracks[2] })
  store.getState().playNext(tracks[2])
  assert.equal(store.getState().queueIndex, 2)
  assert.equal(store.getState().queue.length, 3)
})

test('playNext scheduled track is also next when shuffle is enabled', () => {
  const store = player()
  store.setState({ queue: tracks, queueIndex: 1, currentTrack: tracks[1], shuffle: true })
  store.getState().playNext({ id: 4 })
  assert.equal(store.getState().queue[store.getState().upcomingIndices()[0]].id, 4)
  store.getState().nextTrack()
  assert.equal(store.getState().currentTrack.id, 4)
})

test('playNext empty queue starts the selected track', () => {
  const store = player()
  store.getState().playNext(tracks[0])
  assert.equal(store.getState().currentTrack.id, 1)
  assert.equal(store.getState().queueIndex, 0)
})

test('Retry-After seconds, dates and malformed values are finite and bounded', async () => {
  const { retryAfterSeconds } = await import('../src/utils/retryAfter.js')
  const now = Date.parse('2026-10-01T00:00:00Z')
  for (const [value, expected] of [
    [null, 60], ['', 60], ['  ', 60], ['20', 20], [' 15 ', 15], ['0', 0], ['99999999', 300],
    ['1'.repeat(400), 60], ['junk', 60], ['NaN', 60], ['Infinity', 60], ['-1', 60], ['1.5', 60],
    ['Thu, 01 Oct 2026 00:01:00 GMT', 60], ['Wed, 30 Sep 2026 23:59:00 GMT', 0],
    ['Thu, 01 Oct 2026 01:00:00 GMT', 300],
  ]) assert.equal(retryAfterSeconds(value, now), expected, String(value))
})

test('API client 429 waits using parsed HTTP-date, never NaN', async () => {
  const { retryAfterSeconds } = await import('../src/utils/retryAfter.js')
  let delay
  const context = {
    retryAfterSeconds, createLogger: () => logger, document: { cookie: '' }, performance: { now: () => 0 },
    fetch: async () => ({ status: 429, headers: { get: () => new Date(Date.now() + 60000).toUTCString() } }),
    setTimeout: (callback, ms) => { delay = ms; callback() }, window: { dispatchEvent: noop }, CustomEvent: function () {},
  }
  const source = fs.readFileSync(path.join(__dirname, '../src/api/client.js'), 'utf8')
    .replace(/^import .*\r?\n/gm, '').replace(/export /g, '')
  vm.createContext(context); vm.runInContext(source, context)
  await assert.rejects(context.request('/api/test'), (error) => error.code === 'rate_limited' && error.status === 429)
  assert(Number.isFinite(delay) && delay >= 59000 && delay <= 60000)
})
