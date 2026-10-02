const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')

const SERVICE_PATH = path.join(__dirname, '..', 'src', 'utils', 'cachedBrowse.js')

function loadModule() {
  const code = fs.readFileSync(SERVICE_PATH, 'utf8')
  // Strip the ESM export/import syntax so the module can run in the same
  // vm-based sandbox the other frontend tests use (no build step needed).
  const stripped = code
    .replace(/^export\s+/gm, '')
    .replace(/^import[\s\S]*?from\s+'[^']*'\s*$/gm, '')
  const context = { module: { exports: {} }, console }
  vm.createContext(context)
  vm.runInContext(`${stripped}\n;module.exports = { cachedToTrack, groupCachedTracks, cachedArtistCard, cachedAlbumCard, cachedArtistCardId, isCachedArtistCardId, UNKNOWN_ARTIST, UNKNOWN_ALBUM }`, context)
  return context.module.exports
}

const {
  cachedToTrack,
  groupCachedTracks,
  cachedArtistCard,
  cachedAlbumCard,
  isCachedArtistCardId,
  UNKNOWN_ARTIST,
  UNKNOWN_ALBUM,
} = loadModule()

// ─── Regression: the playback bug ───────────────────────────────────────────
// «В кэше» used to build queue entries with `id: c.file_id`, but IndexedDB
// rows expose `id`/`fileId`. `file_id` is undefined there, so startAudio()
// bailed out on a null file id and Play did nothing at all.
test('cachedToTrack resolves the numeric id from `id`, never `file_id`', () => {
  const track = cachedToTrack({ fileId: 42, id: 42, title: 'Song1', size: 10, duration: 5, mimeType: 'audio/mpeg' })
  assert.equal(track.id, 42)
  assert.ok(Number.isFinite(track.id))
  assert.equal(track.stream_url, '/api/stream/42')
})

test('cachedToTrack falls back to fileId when id is absent', () => {
  const track = cachedToTrack({ fileId: 7, name: 'raw.mp3' })
  assert.equal(track.id, 7)
  assert.equal(track.stream_url, '/api/stream/7')
})

test('cachedToTrack prefers the clean title over the raw filename', () => {
  assert.equal(cachedToTrack({ id: 1, title: 'Song1', name: 'Song1.mp3' }).name, 'Song1')
  assert.equal(cachedToTrack({ id: 1, name: 'Song1.mp3' }).name, 'Song1.mp3')
})

test('cachedToTrack yields a null id rather than NaN/undefined for junk rows', () => {
  const track = cachedToTrack({})
  assert.equal(track.id, null)
  assert.equal(track.stream_url, '/api/stream/null')
})

// ─── Artist → Album → Track tree ─────────────────────────────────────────────
test('groupCachedTracks builds Metallica → Reload → Song1', () => {
  const groups = groupCachedTracks([
    { id: 1, title: 'Song1', artist: 'Metallica', album: 'Reload' },
  ])

  assert.equal(groups.length, 1)
  assert.equal(groups[0].name, 'Metallica')
  assert.equal(groups[0].trackCount, 1)
  assert.equal(groups[0].albums.length, 1)

  const album = groups[0].albums[0]
  assert.equal(album.name, 'Reload')
  assert.equal(album.trackCount, 1)
  assert.equal(album.tracks[0].id, 1)
  assert.equal(album.tracks[0].name, 'Song1')
})

test('groupCachedTracks splits one artist into per-album buckets', () => {
  const groups = groupCachedTracks([
    { id: 1, title: 'Song1', artist: 'Metallica', album: 'Reload' },
    { id: 2, title: 'Song2', artist: 'Metallica', album: 'Reload' },
    { id: 3, title: 'Song3', artist: 'Metallica', album: 'Master of Puppets' },
    { id: 4, title: 'Song4', artist: 'Nirvana', album: 'Nevermind' },
  ])

  assert.equal(groups.length, 2)
  const metallica = groups.find((g) => g.name === 'Metallica')
  assert.equal(metallica.trackCount, 3)
  assert.equal(metallica.albums.length, 2)
  // Join instead of deepEqual: arrays built inside the vm realm have that
  // realm's Array.prototype, which deepStrictEqual rejects.
  assert.equal(metallica.albums.map((a) => a.name).join('|'), 'Master of Puppets|Reload')

  const reload = metallica.albums.find((a) => a.name === 'Reload')
  assert.equal(reload.tracks.length, 2)
  assert.equal(reload.tracks.map((t) => t.name).join('|'), 'Song1|Song2')
})

test('groupCachedTracks merges case/whitespace variants of the same name', () => {
  const groups = groupCachedTracks([
    { id: 1, title: 'Song1', artist: 'Metallica', album: 'Reload' },
    { id: 2, title: 'Song2', artist: ' metallica ', album: ' reload ' },
  ])

  assert.equal(groups.length, 1)
  assert.equal(groups[0].trackCount, 2)
  assert.equal(groups[0].albums.length, 1)
  // First spelling seen wins the display name.
  assert.equal(groups[0].name, 'Metallica')
  assert.equal(groups[0].albums[0].name, 'Reload')
})

test('groupCachedTracks buckets rows without artist/album metadata', () => {
  const groups = groupCachedTracks([{ id: 1, title: 'Loose' }])

  assert.equal(groups.length, 1)
  assert.equal(groups[0].name, UNKNOWN_ARTIST)
  assert.equal(groups[0].albums.length, 1)
  assert.equal(groups[0].albums[0].name, UNKNOWN_ALBUM)
  assert.equal(groups[0].albums[0].tracks[0].id, 1)
})

test('groupCachedTracks skips rows without a usable id', () => {
  const groups = groupCachedTracks([
    { title: 'No id', artist: 'X' },
    null,
    undefined,
    { id: 3, title: 'Good', artist: 'X' },
  ])

  assert.equal(groups.length, 1)
  assert.equal(groups[0].trackCount, 1)
})

test('groupCachedTracks never lets a catch-all bucket show an unrelated cover', () => {
  const [mixed] = groupCachedTracks([
    { id: 1, title: 'A', coverUrl: '/covers/a.jpg' },
    { id: 2, title: 'B', coverUrl: '/covers/b.jpg' },
  ])

  assert.equal(mixed.name, UNKNOWN_ARTIST)
  assert.equal(mixed.coverUrl, null)
  // …while a real album still keeps its own cover.
  assert.equal(mixed.albums[0].name, UNKNOWN_ALBUM)
  assert.equal(mixed.albums[0].coverUrl, null)

  const [real] = groupCachedTracks([
    { id: 1, title: 'A', artist: 'Metallica', album: 'Reload', coverUrl: '/covers/cover.jpg' },
  ])
  assert.equal(real.coverUrl, '/covers/cover.jpg')
  assert.equal(real.albums[0].coverUrl, '/covers/cover.jpg')
})

// ─── Card shapes handed to the existing catalog renderers ────────────────────
test('cachedArtistCard matches the catalog artist-card fields renderArtistCard reads', () => {
  const group = groupCachedTracks([
    { id: 1, title: 'Song1', artist: 'Metallica', album: 'Reload', coverUrl: '/c.jpg' },
  ])[0]
  const card = cachedArtistCard(group)

  assert.equal(card.display_name, 'Metallica')
  assert.equal(card.kind, 'artist')
  assert.equal(card.album_count, 1)
  assert.equal(card.children_count, 0)
  assert.equal(card.is_cached_group, true)
  assert.equal(card.cover_url, '/c.jpg')
  assert.ok(isCachedArtistCardId(card.id))
})

test('cachedAlbumCard carries the local tracklist and a stable id', () => {
  const album = groupCachedTracks([
    { id: 1, title: 'Song1', artist: 'Metallica', album: 'Reload' },
    { id: 2, title: 'Song2', artist: 'Metallica', album: 'Reload' },
  ])[0].albums[0]

  const card = cachedAlbumCard(album)
  assert.equal(card.title, 'Reload')
  assert.equal(card.track_count, 2)
  assert.equal(card.is_cached_album, true)
  assert.equal(card.id, album.key)
  assert.equal(card.cached_tracks.length, 2)
  assert.ok(card.cached_tracks.every((t) => Number.isFinite(t.id)))
})

test('cached album ids are namespaced so they cannot collide with catalog ids', () => {
  assert.equal(isCachedArtistCardId('tmusic:cached:metallica'), true)
  assert.equal(isCachedArtistCardId(42), false)
  assert.equal(isCachedArtistCardId('42'), false)
  assert.equal(isCachedArtistCardId('tmusic:favorites'), false)
})