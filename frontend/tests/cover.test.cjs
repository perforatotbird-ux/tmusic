const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')

// cover.js is a pure ESM module — strip the `export` keywords and evaluate it
// in a sandbox so the fallback priority is asserted without a bundler.
function loadCover() {
  const source = fs.readFileSync(path.join(__dirname, '../src/utils/cover.js'), 'utf8')
  const code = source
    .replace(/^export const /gm, 'const ')
    .replace(/^export function /gm, 'function ')
  const context = {}
  vm.createContext(context)
  vm.runInContext(
    code + '\n;globalThis.__cover = { resolveCoverUrl, resolveReleaseCoverUrl, DEFAULT_COVER }',
    context,
  )
  return context.__cover
}

const { resolveCoverUrl, resolveReleaseCoverUrl, DEFAULT_COVER } = loadCover()

test('cover: an album resolves its own custom cover first', () => {
  assert.equal(
    resolveCoverUrl({ custom_image_url: '/api/library/cover?key=a&custom=1', cover_url: '/api/library/cover?key=a' }),
    '/api/library/cover?key=a&custom=1',
  )
})

test('cover: enriched cover_url is used when there is no custom override', () => {
  assert.equal(resolveCoverUrl({ cover_url: '/api/library/cover?key=b' }), '/api/library/cover?key=b')
})

test('cover: an album without its own cover NEVER inherits the artist cover', () => {
  // Regression: a manually assigned artist cover used to leak into every one of
  // its albums because resolveCoverUrl took the artist as a fallback source.
  const artist = { custom_image_url: '/api/library/cover?key=artist&custom=1', image_url: '/api/library/cover?key=artist' }
  const album = { id: 1, title: 'Untitled release' }
  assert.equal(resolveCoverUrl(album), DEFAULT_COVER)
  assert.equal(resolveCoverUrl(album, null), null)
})

test('cover: a non-string fallback is ignored, never rendered as an image src', () => {
  // The previous signature was resolveCoverUrl(entity, artist). A call site left
  // behind would now pass the artist object as `fallback` — it must not become
  // the <img src>.
  const artist = { custom_image_url: '/api/library/cover?key=artist&custom=1' }
  assert.equal(resolveCoverUrl({ id: 1 }, artist), DEFAULT_COVER)
  assert.equal(resolveCoverUrl({ id: 1 }, 42), DEFAULT_COVER)
  assert.equal(resolveCoverUrl({ id: 1 }, '/custom.svg'), '/custom.svg')
})

test('cover: an album with its own cover is unaffected by the artist argument', () => {
  const artist = { custom_image_url: '/api/library/cover?key=artist&custom=1' }
  const album = { cover_url: '/api/library/cover?key=album' }
  assert.equal(resolveCoverUrl(album, artist), '/api/library/cover?key=album')
})

test('cover: an artist card resolves its own custom cover', () => {
  assert.equal(
    resolveCoverUrl({ custom_image_url: '/api/library/cover?key=x&custom=1' }, null),
    '/api/library/cover?key=x&custom=1',
  )
})

test('cover: image_url is honoured for artist cards without a custom cover', () => {
  assert.equal(resolveCoverUrl({ image_url: '/api/library/cover?key=y' }), '/api/library/cover?key=y')
})

test('cover: a coverless entity falls back to the placeholder', () => {
  assert.equal(resolveCoverUrl({ id: 7 }), DEFAULT_COVER)
  assert.equal(resolveCoverUrl(null), DEFAULT_COVER)
})

test('cover: release objects expose only their own coverUrl', () => {
  assert.equal(resolveReleaseCoverUrl({ coverUrl: '/api/library/cover?key=z' }), '/api/library/cover?key=z')
  assert.equal(resolveReleaseCoverUrl({}), DEFAULT_COVER)
  assert.equal(resolveReleaseCoverUrl(null), DEFAULT_COVER)
})
