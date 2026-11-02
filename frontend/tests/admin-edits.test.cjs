const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')

// Pure module — strip the `export` keyword and evaluate it in a sandbox.
function loadAdminEdits() {
  const source = fs.readFileSync(path.join(__dirname, '../src/utils/adminEdits.js'), 'utf8')
  const code = source.replace(/^export function /gm, 'function ')
  const context = {}
  vm.createContext(context)
  vm.runInContext(code + '\n;globalThis.__edits = { trackRenamePatch }', context)
  return context.__edits
}

const { trackRenamePatch } = loadAdminEdits()

// The body is built inside the VM realm, so its prototype differs from this
// realm's Object.prototype and deepStrictEqual would reject it. Compare fields.
const bodyName = (patch) => (patch === null ? null : patch.name)

test('rename: a trimmed new name produces a PATCH body', () => {
  assert.equal(bodyName(trackRenamePatch('  Новое имя  ', 'Старое')), 'Новое имя')
})

test('rename: an unchanged draft is a no-op, not a pointless round-trip', () => {
  assert.equal(trackRenamePatch('Старое', 'Старое'), null)
})

test('rename: a blank draft is rejected so the name can never be emptied', () => {
  assert.equal(trackRenamePatch('', 'Старое'), null)
  assert.equal(trackRenamePatch('    ', 'Старое'), null)
  assert.equal(trackRenamePatch(null, 'Старое'), null)
  assert.equal(trackRenamePatch(undefined, 'Старое'), null)
})

test('rename: renaming to a name that trims to the current one is a no-op', () => {
  assert.equal(trackRenamePatch('  Старое  ', 'Старое'), null)
})

test('rename: a track whose current name is unknown still accepts a new one', () => {
  assert.equal(bodyName(trackRenamePatch('Новое', null)), 'Новое')
})
