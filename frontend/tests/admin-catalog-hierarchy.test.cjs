// Admin catalog hierarchy (2026-10-21): «в админке альбомы отображаются как Артист».
//
// The catalog is a tree (artist → album group → release), and the nested sync
// stores album folders as catalog cards. The admin list used to request the
// whole catalog in one flat page, so album cards landed at the root level and
// read as «Артист» next to real artists. These are source-contract guards: the
// list must ask for one level at a time and expose a way to walk into a card.
const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const SRC = path.join(__dirname, '..', 'src')
const catalog = fs.readFileSync(path.join(SRC, 'components', 'AdminCatalog.jsx'), 'utf8')

test('catalog list requests the root level, not the whole tree', () => {
  assert.match(
    catalog,
    /currentParentId[\s\S]{0,120}\?\s*\{\s*parent_id:\s*currentParentId\s*\}\s*:\s*\{\s*root_only:\s*true\s*\}/,
    'root browsing must send root_only:true, drill-down must send parent_id',
  )
})

test('a search still spans every level', () => {
  // Search without a level filter — otherwise nested cards become unreachable.
  assert.match(catalog, /\.\.\.\(trimmed\s*\?\s*\{\}\s*:/)
})

test('breadcrumbs let the admin walk back up the tree', () => {
  assert.match(catalog, /data-testid="catalog-breadcrumbs"/)
  assert.match(catalog, /goToLevel\(-1\)/)
  assert.match(catalog, /goToLevel\(path\.length - 2\)/)
})

test('a card with children exposes a drill-in action', () => {
  assert.match(catalog, /const drillInto = \(artist\) => \{/)
  assert.match(catalog, /artist\.children_count > 0[\s\S]{0,200}drillInto\(artist\)/)
  assert.match(catalog, /Открыть вложенные \(\{artist\.children_count\}\)/)
})

test('cards report their nesting and the release kind exists', () => {
  // A row must say «N влож.» and, in search results, which card it sits under.
  assert.match(catalog, /влож\./)
  assert.match(catalog, /↳ \$\{artist\.parent_name\}/)
  assert.match(catalog, /id: 'standalone_album', label: 'Отдельный релиз'/)
})
