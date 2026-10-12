const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')

// scanReport.js is a pure ESM module — strip the `export` keywords and evaluate
// it in a sandbox so the scan feedback text is asserted without a bundler.
function loadScanReport() {
  const source = fs.readFileSync(path.join(__dirname, '../src/utils/scanReport.js'), 'utf8')
  const code = source
    .replace(/^export const /gm, 'const ')
    .replace(/^export function /gm, 'function ')
  const context = {}
  vm.createContext(context)
  vm.runInContext(code + '\n;globalThis.__scan = { describeScan }', context)
  return context.__scan
}

const { describeScan } = loadScanReport()

test('scan: artist scan lists the changed fields in Russian', () => {
  const text = describeScan({
    updated: true,
    fields: ['bio', 'mbid', 'image_url'],
    image_source: 'lastfm',
    albums_updated: 0,
  })
  assert.equal(text, 'Обновлено: био · MBID · обложка')
})

test('scan: album covers are counted even without scalar field changes', () => {
  const text = describeScan({
    updated: true,
    fields: [],
    image_source: 'album',
    albums_updated: 3,
  })
  assert.equal(text, 'Обновлено: обложки альбомов: 3 (обложка взята из альбома)')
})

test('scan: an album-derived artist cover is called out explicitly', () => {
  // Last.fm has no artist artwork any more, so this is the common success path
  // and the user should know the picture came from an album.
  const text = describeScan({
    updated: true,
    fields: ['image_url'],
    image_source: 'album',
    albums_updated: 1,
  })
  assert.equal(text, 'Обновлено: обложка · обложки альбомов: 1 (обложка взята из альбома)')
})

test('scan: an untouched existing cover is reported as such', () => {
  const text = describeScan({
    updated: true,
    fields: ['bio'],
    image_source: 'existing',
  })
  assert.equal(text, 'Обновлено: био (обложка оставлена без изменений)')
})

test('scan: an empty report says nothing changed instead of staying silent', () => {
  // Regression: the button used to refresh with zero feedback, which is exactly
  // why it read as «она ничего не выполняет».
  const text = describeScan({ updated: false, fields: [], albums_updated: 0 })
  assert.equal(text, 'Last.fm не отдал новых данных — изменений нет.')
})

test('scan: a missing or malformed report never throws', () => {
  assert.equal(describeScan(null), 'Сканирование завершено.')
  assert.equal(describeScan(undefined), 'Сканирование завершено.')
  assert.equal(describeScan('nope'), 'Сканирование завершено.')
  assert.equal(describeScan(42), 'Сканирование завершено.')
  assert.equal(describeScan({ fields: 'bio' }), 'Last.fm не отдал новых данных — изменений нет.')
})

test('scan: unknown field names fall through untranslated', () => {
  const text = describeScan({ fields: ['weird_field'], albums_updated: 0 })
  assert.equal(text, 'Обновлено: weird_field')
})
