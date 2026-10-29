// Bulk delete of checked catalog cards (2026-10-21): «удалить несколько
// отмеченных чекбоксами карточек, состояние сразу обновляется».
//
// The owner then asked for «удалять целиком, даже если есть альбомы или файлы»:
// nothing blocks a card any more, its album rows go with it, audio files stay in
// the VFS. These are source-contract guards: the list has to drop the rows on the
// response (not one refetch later) and tell the library grid behind the panel.
const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const SRC = path.join(__dirname, '..', 'src')
const catalog = fs.readFileSync(path.join(SRC, 'components', 'AdminCatalog.jsx'), 'utf8')
const adminApi = fs.readFileSync(path.join(SRC, 'api', 'admin.js'), 'utf8')

test('deleting a card takes its albums with it', () => {
  assert.match(adminApi, /deleteArtist: \(id\) =>[\s\S]{0,120}with_albums=true/)
  assert.match(adminApi, /batchDeleteArtists:\s*\(body\)\s*=>/)
  assert.match(adminApi, /\/api\/admin\/catalog\/artists\/batch-delete/)
  // Albums are no longer a reason to refuse a deletion.
  assert.doesNotMatch(catalog, /has_albums/)
  assert.doesNotMatch(catalog, /Возможно только если внутри нет альбомов/)
})

test('the bulk action is wired to the checkbox selection', () => {
  assert.match(catalog, /const deleteSelectedArtists = async \(\) => \{/)
  assert.match(catalog, /const ids = \[\.\.\.selectedArtists\]/)
  assert.match(catalog, /adminApi\.batchDeleteArtists\(\{ artist_ids: ids \}\)/)
  assert.match(catalog, /onClick=\{deleteSelectedArtists\}/)
  assert.match(catalog, /Удалить выбранные\{selectedArtists\.length \? ` \(\$\{selectedArtists\.length\}\)` : ''\}/)
})

test('deleting asks for confirmation and warns that albums go too', () => {
  assert.match(catalog, /title: 'Удалить выбранные карточки'/)
  assert.match(catalog, /Вместе с карточками удалятся их альбомы/)
  assert.match(catalog, /Аудиофайлы остаются в файловом дереве/)
  assert.match(catalog, /danger: true/)
})

test('rows disappear on the response, not after a refetch', () => {
  assert.match(catalog, /setArtists\(\(prev\) => prev\.filter\(\(card\) => !deletedIds\.has\(card\.id\)\)\)/)
  // The refetch that follows must not flash the «Загрузка…» placeholder.
  assert.match(catalog, /await loadArtists\(\{ silent: true \}\)/)
  assert.match(catalog, /if \(mySeq === loadSeq\.current && !silent\) setLoading\(false\)/)
  // Expanded albums/tracks of a deleted card must not linger either.
  assert.match(catalog, /setAlbumsByArtist\(\(prev\) => \{[\s\S]{0,160}delete next\[id\]/)
  assert.match(catalog, /setExpanded\(\(prev\) => \(prev && deletedIds\.has\(prev\) \? null : prev\)\)/)
})

test('the notice reports deleted cards and albums', () => {
  assert.match(catalog, /const albums = result\?\.deleted_albums \|\| 0/)
  assert.match(catalog, /альбомов: \$\{albums\}/)
  // Only a card that vanished or failed can be skipped now.
  assert.match(catalog, /not_found: 'карточка уже удалена'/)
})

test('other views are told to refresh, without double-loading this list', () => {
  assert.match(catalog, /emitCatalogChanged\(\{ action: 'cards-deleted', ids: \[\.\.\.deletedIds\] \}\)/)
  assert.match(catalog, /if \(event\?\.detail\?\.action === 'cards-deleted'\) return/)
})
