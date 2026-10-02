const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const { create } = require('zustand')
const noop = () => {}
const src = (name) => fs.readFileSync(path.join(__dirname, '../src', name), 'utf8')
function player({ native = true, storedVolume = '0.9' } = {}) {
  const timers = [], logs = [], storage = new Map([['tmusic_volume', storedVolume]]), events = {}
  const context = { create, IS_NATIVE: native, streamApi: {
    getStreamUrl: (id) => '/stream/' + id, triggerTranscode: async () => {}, prefetchTracks: async () => {},
    createTranscodeEventSource: () => events }, setMediaPlaybackState: noop,
    createLogger: () => ({ info: noop, warn: noop, error: noop, debug: (...args) => logs.push(args) }),
    localStorage: { getItem: (k) => storage.get(k) ?? null, setItem: (k,v) => storage.set(k,v), removeItem: (k) => storage.delete(k) },
    setTimeout: (cb) => { timers.push(cb); return timers.length }, setInterval: () => 0, clearTimeout: noop, clearInterval: noop,
    AbortController, fetch: async () => ({ ok: true, status: 206, arrayBuffer: async () => new ArrayBuffer(0) }),
    Math: Object.assign(Object.create(Math), { random: () => 0.9999 }) }
  vm.createContext(context)
  vm.runInContext(src('store/playerStore.js').replace(/^import .*\r?\n/gm, '').replace('export const usePlayerStore =', 'globalThis.usePlayerStore ='), context)
  return { store: context.usePlayerStore, events, timers, logs, storage }
}
const tracks = [1,2,3,4].map((id) => ({ id, name: `Synthetic${id}.mp3` }))
const upcoming = (store) => Array.from(store.getState().upcomingIndices(), (i) => store.getState().queue[i].id)

test('shuffle removing played history preserves the actual successor', () => {
  const { store } = player()
  store.getState().playQueue(tracks); store.getState().toggleShuffle(); store.getState().nextTrack()
  assert.equal(store.getState().currentTrack.id, 2); assert.deepEqual(upcoming(store), [3,4])
  store.getState().removeFromQueue(0)
  assert.deepEqual(upcoming(store), [3,4]); store.getState().nextTrack()
  assert.equal(store.getState().currentTrack.id, 3)
})
test('shuffle removing upcoming item and older history never skips remaining next', () => {
  const { store } = player()
  store.getState().playQueue(tracks); store.getState().toggleShuffle(); store.getState().nextTrack()
  store.getState().removeFromQueue(2); assert.deepEqual(upcoming(store), [4])
  store.getState().removeFromQueue(0); assert.deepEqual(upcoming(store), [4])
  store.getState().nextTrack(); assert.equal(store.getState().currentTrack.id, 4)
})
test('non-shuffle removal also preserves current index and next', () => {
  const { store } = player()
  store.getState().playQueue(tracks,1); store.getState().removeFromQueue(0)
  assert.equal(store.getState().queueIndex, 0); store.getState().nextTrack()
  assert.equal(store.getState().currentTrack.id, 3)
})
test('mute is independent from volume and survives binding a new audio element', () => {
  const { store } = player({ storedVolume: '0.25' })
  const audio = {}
  store.getState().setAudioElement(audio); store.getState().toggleMute()
  assert.equal(store.getState().volume, 0.25); assert.equal(audio.muted, true)
  const replacement = {}; store.getState().setAudioElement(replacement)
  assert.equal(replacement.muted, true); assert.equal(replacement.volume, 0.25)
  store.getState().toggleMute()
  assert.equal(store.getState().volume, 0.25); assert.equal(replacement.muted, false)
})
test('slider zero then unmute restores last audible volume, not a hardcoded jump', () => {
  const { store, storage } = player()
  store.getState().setVolume(0.3); store.getState().setVolume(0)
  assert.equal(store.getState().isMuted, true)
  store.getState().toggleMute()
  assert.equal(store.getState().volume, 0.3); assert.equal(storage.get('tmusic_volume'), '0.3')
  store.getState().toggleMute(); store.getState().setVolume(0.6)
  assert.equal(store.getState().isMuted, false)
})
for (const [stored, expected, muted] of [['0',0,true],['NaN',0.9,false],['9',1,false],['-2',0,true]]) {
  test('initial volume is valid and muted state matches: ' + stored, () => {
    const { store } = player({ storedVolume: stored })
    assert.equal(store.getState().volume, expected); assert.equal(store.getState().isMuted, muted)
  })
}
for (const event of ['transcode_started','transcode_complete','transcode_failed']) {
  test('browser ESM handles SSE without require: ' + event, () => {
    const { store, events, timers, logs } = player({ native: false })
    store.setState({ queue: tracks, currentTrack: tracks[0] }); store.getState().startTranscodeEvents()
    events.onmessage({ data: JSON.stringify({ event, file_id: 1 }) })
    assert.equal(store.getState().notice?.kind, event === 'transcode_failed' ? 'error' : 'info')
    assert(!logs.some((args) => args[0] === 'SSE parse error'))
  })
}
test('SSE ping/unrelated tracks do not change the player', () => {
  const { store, events, timers } = player({ native: false })
  store.setState({ queue: tracks }); store.getState().startTranscodeEvents()
  events.onmessage({ data: JSON.stringify({ event: 'ping' }) })
  events.onmessage({ data: JSON.stringify({ event: 'transcode_complete', file_id: 999 }) })
  assert.equal(store.getState().notice, null)
})

function media(state = 'paused') {
  const nativeStates = [], metadata = []
  const context = { DEFAULT_COVER: '/icon.svg', URL,
    window: { location: { href: 'https://synthetic.example/music' } },
    getNativeBridge: () => ({ setPlaybackState: (s) => nativeStates.push(s), updateMetadata: (...args) => metadata.push(args) }),
    navigator: { mediaSession: { playbackState: state } }, MediaMetadata: function (data) { return data }, console }
  vm.createContext(context); vm.runInContext(src('services/mediaSession.js').replace(/^import .*\r?\n/gm,'').replace(/export /g,''),context)
  return { context, nativeStates, metadata }
}
for (const state of ['paused','none','playing']) {
  test('metadata update preserves playback state: ' + state, () => {
    const { context, nativeStates, metadata } = media(state)
    context.updateMediaSessionMetadata(tracks[0], { coverUrl: '/api/library/cover?key=synthetic&custom=1' })
    assert.equal(context.navigator.mediaSession.playbackState, state); assert.deepEqual(nativeStates, [])
    assert.equal(metadata[0][3], 'https://synthetic.example/api/library/cover?key=synthetic&custom=1')
  })
}
for (const [input, expected] of [
  ['/icon.svg','https://synthetic.example/icon.svg'],
  ['/api/library/cover?key=synthetic','https://synthetic.example/api/library/cover?key=synthetic'],
  ['https://covers.example/img.png','https://covers.example/img.png'],
  ['', 'https://synthetic.example/icon.svg'], ['javascript:alert(1)',''], ['file:///private/file',''],
]) {
  test('native cover URL resolution: ' + input, () => {
    assert.equal(media().context.resolveMediaCoverUrl(input), expected)
  })
}
