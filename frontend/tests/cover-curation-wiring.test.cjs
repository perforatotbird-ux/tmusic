const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')

const SRC = path.join(__dirname, '..', 'src')
const read = (rel) => fs.readFileSync(path.join(SRC, rel), 'utf8')

// ── COVER_TARGET_LABEL (pure, exported from CoverPicker) ────────────────────

function loadTargetLabel() {
  const source = read('components/CoverPicker.jsx')
  const code = source
    .replace(/^export const /gm, 'const ')
    .replace(/^export function /gm, 'function ')
    // Keep only the constant block: the component body needs React.
    .split('\n')
    .filter((line) => !/^import /.test(line))
    .join('\n')
  const start = code.indexOf('COVER_TARGET_LABEL')
  const snippet = code.slice(code.indexOf('const COVER_TARGET_LABEL'), code.indexOf('}', code.indexOf('track:')) + 1)
  assert.ok(snippet.includes('track'), 'could not extract COVER_TARGET_LABEL')
  const context = {}
  vm.createContext(context)
  vm.runInContext(snippet + '\n;globalThis.__t = COVER_TARGET_LABEL', context)
  return context.__t
}

test('cover picker labels every catalog level', () => {
  const labels = loadTargetLabel()
  assert.equal(labels.artist, 'Артист / сборник')
  assert.equal(labels.album, 'Альбом')
  assert.equal(labels.track, 'Трек')
})

// ── Wiring: a cover change must reach every open view ───────────────────────
// Regression for «в админке меняю картинку — другое изображение из кеша, а
// изменить обложку вообще негде». The API dispatches
// `tmusic:catalog-changed` naming the touched row; every list must react.
// These are source-contract guards: the wiring is what broke, not a pure
// function, so a static check is the honest regression net.

test('libraryApi emits a catalog-change event naming the touched row', () => {
  const src = read('api/library.js')
  // One emitter, used by all three mutations (file, url, reset).
  assert.match(src, /new CustomEvent\('tmusic:catalog-changed'/)
  const emitted = src.match(/emitCatalogChanged\(\{[^}]*\}\)/g) || []
  assert.equal(emitted.length, 3, 'setCoverUrl / uploadCoverFile / resetCover must all emit')
  for (const call of emitted) {
    assert.match(call, /type: targetType/)
    assert.match(call, /id: targetId/)
  }
  // The reset path must clear the key, not keep the previous one.
  assert.match(src, /emitCatalogChanged\(\{[^}]*custom_key: null[^}]*\}\)/)
})

test('AdminCatalog reacts to a cover change instead of keeping the stale row', () => {
  const src = read('components/AdminCatalog.jsx')
  assert.match(
    src,
    /addEventListener\('tmusic:catalog-changed'/,
    'the admin catalog must refresh — it silently kept the old artwork',
  )
  // The reload must not collapse an expanded artist mid-edit.
  assert.match(src, /const onChanged = \(\) => \{/)
  assert.match(src, /loadAlbums\(Number\(artistId\)\)/)
})

test('MusicLibrary and AlbumDetail already react to the same event', () => {
  assert.match(read('components/MusicLibrary.jsx'), /addEventListener\('tmusic:catalog-changed'/)
  assert.match(read('components/AlbumDetail.jsx'), /addEventListener\('tmusic:catalog-changed'/)
})

// ── Discoverability: the cover action exists at every level ─────────────────

test('cover curation is reachable from the admin rows, not only a hidden menu', () => {
  const src = read('components/AdminCatalog.jsx')
  // artist row, album row, track row — three visible triggers next to ⋮.
  assert.equal(
    (src.match(/title=\"Изменить обложку\"/g) || []).length,
    2,
    'artist and album rows need a visible cover button',
  )
  assert.match(src, /title=\"Обложка трека\"/)
})

test('cover curation is reachable from the catalog cards', () => {
  const src = read('components/MusicLibrary.jsx')
  assert.match(src, /type: 'album',[\s\S]{0,200}hasCustom: !!album\.custom_image_url/)
  assert.match(src, /type: 'artist',[\s\S]{0,200}hasCustom: !!artist\.custom_image_url/)
  // Both card renderers gate on isAdmin, so non-admins never see the button.
  assert.match(src, /isAdmin && onOpenCoverPicker && !album\.is_cached_album && \(/)
  assert.match(src, /isAdmin && onOpenCoverPicker && !artist\.is_cached_group && \(/)
})

test('the album cover button never opens a cached (non-catalog) album', () => {
  const src = read('components/MusicLibrary.jsx')
  const cardSrc = src.slice(src.indexOf('const renderAlbumCard'), src.indexOf('const renderAlbumCard') + 4000)
  assert.match(cardSrc, /isAdmin && onOpenCoverPicker && !album\.is_cached_album/)
})

test('the card cover button is visible on hover, focus and touch (WCAG 2.4.7)', () => {
  const css = read('index.css')
  const block = css.slice(css.indexOf('.tmusic-card-cover-btn'))
  assert.match(block, /@media \(hover: none\)/)
  assert.match(block, /\.tmusic-card:hover \.tmusic-card-cover-btn/)
  assert.match(block, /\.tmusic-card:focus-within \.tmusic-card-cover-btn/)
  assert.match(block, /\.tmusic-card-cover-btn:focus-visible/)
})

// ── The picker must not carry state between targets ────────────────────────

test('the cover picker is remounted per target', () => {
  const app = read('App.jsx')
  assert.match(app, /key=\{`\$\{coverTarget\.type\}:\$\{coverTarget\.id\}`\}/)
  // The event is emitted once, by the API layer — not a second time here.
  const picker = app.slice(app.indexOf('Cover Art Picker Modal'))
  assert.doesNotMatch(picker, /dispatchEvent/)
})
