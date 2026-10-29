/**
 * Regression-тесты логики окна «Плейлисты» (components/PlaylistsModal.jsx).
 *
 * Проект не тащит браузерный тест-раннер: как и в error-handling.test.cjs,
 * тело конкретного обработчика вырезается из исходника и исполняется в VM с
 * моками API/состояния. Так проверяется честность результата: неуспех не
 * выдаётся за успех, состояние не портится при ошибке.
 */
const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const vm = require('node:vm')
const path = require('node:path')

const source = (name) => fs.readFileSync(path.join(__dirname, '../src', name), 'utf8')

const FILE = 'components/PlaylistsModal.jsx'

function slice(text, from, to) {
  const a = text.indexOf(from)
  const b = text.indexOf(to, a)
  assert.ok(a >= 0 && b > a, `не найден фрагмент ${from}`)
  return text.slice(a, b)
}

/** Исполняет тело обработчика как globalThis.action(ctx-зависимый). */
function runHandler(text, from, to, ctx, name) {
  const body = slice(text, from, to).replace(`const ${name} =`, 'globalThis.action =')
  vm.createContext(ctx)
  vm.runInContext(body, ctx)
}

function makeCtx(overrides = {}) {
  const state = {
    detail: { id: 7, name: 'Старое', track_count: 2, tracks: [{ id: 1, file_id: 11 }, { id: 2, file_id: 12 }] },
    nameDraft: 'Старое',
    renameBusy: false,
    rowBusy: false,
    addingId: null,
    error: null,
    playlists: [{ id: 7, name: 'Старое', track_count: 2 }],
    notices: [],
  }
  const ctx = {
    state,
    playlistsApi: {},
    usePlayerStore: { getState: () => ({ pushNotice: (n) => state.notices.push(n) }) },
    setError: (v) => { state.error = v },
    setRenameBusy: (v) => { state.renameBusy = v },
    setNameDraft: (v) => { state.nameDraft = v },
    setRowBusy: (v) => { state.rowBusy = v },
    setAddingId: (v) => { state.addingId = v },
    setDetail: (v) => { state.detail = typeof v === 'function' ? v(state.detail) : v },
    setPlaylists: (v) => { state.playlists = typeof v === 'function' ? v(state.playlists) : v },
    get detail() { return state.detail },
    get nameDraft() { return state.nameDraft },
    get renameBusy() { return state.renameBusy },
    get rowBusy() { return state.rowBusy },
    get addingId() { return state.addingId },
    get tracks() { return state.detail?.tracks || [] },
    ...overrides,
  }
  return ctx
}

test('успешное переименование применяет ответ сервера и подтверждает пользователю', async () => {
  const text = source(FILE)
  const ctx = makeCtx()
  ctx.state.nameDraft = 'Новое'
  ctx.playlistsApi = {
    rename: async (id, name) => {
      ctx.renameArgs = [id, name]
      return { id, name: 'Новое' }
    },
  }
  runHandler(text, '  const handleRename = async () => {', '  const handleDelete = async (pl) => {', ctx, 'handleRename')

  await ctx.action()

  assert.deepEqual(ctx.renameArgs, [7, 'Новое'])
  assert.equal(ctx.state.detail.name, 'Новое')
  assert.equal(ctx.state.nameDraft, 'Новое')
  assert.equal(ctx.state.playlists[0].name, 'Новое')
  assert.equal(ctx.state.error, null)
  assert.equal(ctx.state.renameBusy, false)
  assert.equal(ctx.state.notices.length, 1)
})

test('переименование не вызывается, если имя не изменилось', async () => {
  const text = source(FILE)
  const ctx = makeCtx()
  let calls = 0
  ctx.playlistsApi = { rename: async () => { calls += 1; return { id: 7, name: 'x' } } }
  runHandler(text, '  const handleRename = async () => {', '  const handleDelete = async (pl) => {', ctx, 'handleRename')

  await ctx.action()

  assert.equal(calls, 0)
  assert.equal(ctx.state.detail.name, 'Старое')
})

test('ошибка переименования не выдаётся за успех и не меняет название', async () => {
  const text = source(FILE)
  const ctx = makeCtx()
  ctx.state.nameDraft = 'Новое'
  ctx.playlistsApi = { rename: async () => { throw new Error('Synthetic rename failure') } }
  runHandler(text, '  const handleRename = async () => {', '  const handleDelete = async (pl) => {', ctx, 'handleRename')

  await ctx.action()

  assert.equal(ctx.state.detail.name, 'Старое')
  assert.equal(ctx.state.nameDraft, 'Новое')
  assert.equal(ctx.state.playlists[0].name, 'Старое')
  assert.equal(ctx.state.notices.length, 0)
  assert.equal(ctx.state.error, 'Synthetic rename failure')
  assert.equal(ctx.state.renameBusy, false)
})

test('ошибка удаления трека оставляет трек в плейлисте и показывает ошибку', async () => {
  const text = source(FILE)
  const ctx = makeCtx()
  ctx.playlistsApi = { removeItem: async () => { throw new Error('Synthetic request failed') } }
  runHandler(
    text,
    '  const handleRemoveTrack = async (item) => {',
    '  // Поиск треков для добавления',
    ctx,
    'handleRemoveTrack'
  )

  await ctx.action({ id: 1, file_id: 11 })

  assert.equal(ctx.state.detail.tracks.length, 2)
  assert.equal(ctx.state.error, 'Synthetic request failed')
  assert.equal(ctx.state.rowBusy, false)
})

test('успешное удаление трека убирает его из детали и счётчика списка', async () => {
  const text = source(FILE)
  const ctx = makeCtx()
  ctx.playlistsApi = { removeItem: async () => ({ status: 'ok' }) }
  runHandler(
    text,
    '  const handleRemoveTrack = async (item) => {',
    '  // Поиск треков для добавления',
    ctx,
    'handleRemoveTrack'
  )

  await ctx.action({ id: 1, file_id: 11 })

  assert.deepEqual(ctx.state.detail.tracks.map((t) => t.id), [2])
  assert.equal(ctx.state.detail.track_count, 1)
  assert.equal(ctx.state.playlists[0].track_count, 1)
})

test('повторное добавление уже имеющегося трека сообщает об этом, а не о добавлении', async () => {
  const text = source(FILE)
  const ctx = makeCtx()
  ctx.playlistsApi = {
    addTrack: async () => ({
      id: 7,
      name: 'Старое',
      track_count: 2,
      tracks: [{ id: 1, file_id: 11 }, { id: 2, file_id: 12 }],
    }),
  }
  runHandler(text, '  const handleAddTrack = async (track) => {', '  const renameDirty =', ctx, 'handleAddTrack')

  await ctx.action({ id: 11, title: 'Трек' })

  assert.equal(ctx.state.notices.length, 1)
  assert.match(ctx.state.notices[0].text, /уже в плейлисте/)
  assert.equal(ctx.state.addingId, null)
})

test('добавление нового трека подтверждается и обновляет счётчик', async () => {
  const text = source(FILE)
  const ctx = makeCtx()
  ctx.playlistsApi = {
    addTrack: async () => ({
      id: 7,
      name: 'Старое',
      track_count: 3,
      tracks: [{ id: 1, file_id: 11 }, { id: 2, file_id: 12 }, { id: 3, file_id: 13 }],
    }),
  }
  runHandler(text, '  const handleAddTrack = async (track) => {', '  const renameDirty =', ctx, 'handleAddTrack')

  await ctx.action({ id: 13, title: 'Новый трек' })

  assert.equal(ctx.state.detail.tracks.length, 3)
  assert.equal(ctx.state.playlists[0].track_count, 3)
  assert.match(ctx.state.notices[0].text, /добавлен в плейлист/)
  assert.equal(ctx.state.error, null)
})

test('ошибка добавления трека не выдаётся за успех', async () => {
  const text = source(FILE)
  const ctx = makeCtx()
  ctx.playlistsApi = { addTrack: async () => { throw new Error('Synthetic add failure') } }
  runHandler(text, '  const handleAddTrack = async (track) => {', '  const renameDirty =', ctx, 'handleAddTrack')

  await ctx.action({ id: 13, title: 'Новый трек' })

  assert.equal(ctx.state.notices.length, 0)
  assert.equal(ctx.state.error, 'Synthetic add failure')
  assert.equal(ctx.state.addingId, null)
  assert.equal(ctx.state.detail.tracks.length, 2)
})

test('источник содержит вызов PATCH-переименования через api-клиент', () => {
  const api = source('api/playlists.js')
  assert.match(api, /rename:\s*\(id, name\)/)
  assert.match(api, /method:\s*'PATCH'/)
  const router = fs.readFileSync(
    path.join(__dirname, '../../backend/app/api/playlist_router.py'),
    'utf8'
  )
  assert.match(router, /@router\.patch\("\/\{playlist_id\}"/)
})

test('пункт меню «Плейлисты» открывает отдельное окно, а не встроенную панель', () => {
  const lib = source('components/MusicLibrary.jsx')
  assert.match(lib, /id === 'playlists'/)
  assert.match(lib, /setPlaylistsOpen\(true\)/)
  assert.match(lib, /<PlaylistsModal open=\{playlistsOpen\}/)
})
