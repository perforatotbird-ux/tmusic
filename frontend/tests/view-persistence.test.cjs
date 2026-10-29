/**
 * «Где я был»: снимок экрана переживает перезагрузку.
 *
 * Регрессия: навигация в приложении — состояние MusicLibrary, роутера нет,
 * поэтому F5/перезагрузка WebView всегда выбрасывала пользователя на главную
 * («Медиатеку»), а глубоко прокрученный каталог — в начало списка.
 */
const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')

const MODULE_PATH = path.join(__dirname, '..', 'src', 'utils', 'viewPersistence.js')
const EXPORTS = [
  'VIEW_STATE_KEY',
  'SCROLL_STATE_KEY',
  'DEFAULT_VIEW_STATE',
  'readViewState',
  'writeViewState',
  'clearViewState',
  'readScrollY',
  'saveScrollY',
  'flushScrollY',
  'configureScrollRestoration',
  'restoreScrollY',
]

function memoryStorage(initial = {}) {
  const data = new Map(Object.entries(initial))
  return {
    getItem: (key) => (data.has(key) ? data.get(key) : null),
    setItem: (key, value) => data.set(key, String(value)),
    removeItem: (key) => data.delete(key),
    dump: () => Object.fromEntries(data),
  }
}

/** Управляемые часы: модуль читает время только через Date.now(). */
function fakeClock(start = 1000) {
  let now = start
  return {
    Date: { now: () => now },
    advance: (ms) => {
      now += ms
    },
  }
}

function fakeFrames() {
  const queue = new Map()
  let next = 0
  return {
    request: (fn) => {
      queue.set(++next, fn)
      return next
    },
    cancel: (id) => queue.delete(id),
    pending: () => queue.size,
    run: () => {
      const first = queue.entries().next()
      if (first.done) return false
      const [id, fn] = first.value
      queue.delete(id)
      fn()
      return true
    },
  }
}

function loadModule({ storage = memoryStorage(), clock = fakeClock(), win = null, doc = null } = {}) {
  const code = fs
    .readFileSync(MODULE_PATH, 'utf8')
    .replace(/^export\s+/gm, '')
    .replace(/^import[\s\S]*?from\s+'[^']*'\s*$/gm, '')
  const context = {
    module: { exports: {} },
    sessionStorage: storage,
    Date: clock.Date,
    console,
    window: win,
    document: doc,
    history: { scrollRestoration: 'auto' },
  }
  vm.createContext(context)
  vm.runInContext(
    `${code}\n;module.exports = { ${EXPORTS.join(', ')} }`,
    context
  )
  return { api: context.module.exports, storage, clock, win, doc, context }
}

function fakeWindow({ frames = fakeFrames(), innerHeight = 800 } = {}) {
  const listeners = new Map()
  const win = {
    innerHeight,
    scrollY: 0,
    scrollCalls: [],
    requestAnimationFrame: frames.request,
    cancelAnimationFrame: frames.cancel,
    scrollTo: (x, y) => {
      win.scrollY = y
      win.scrollCalls.push(y)
    },
    addEventListener: (type, fn) => {
      if (!listeners.has(type)) listeners.set(type, new Set())
      listeners.get(type).add(fn)
    },
    removeEventListener: (type, fn) => {
      listeners.get(type)?.delete(fn)
    },
    emit: (type, event = {}) => {
      for (const fn of listeners.get(type) || []) fn(event)
    },
    listenerCount: (type) => listeners.get(type)?.size || 0,
  }
  return win
}

function fakeDocument(height = 0) {
  const html = { scrollHeight: height }
  return { documentElement: html, body: { style: {} }, html }
}

const KIND_WHITELIST = { kinds: ['artist', 'albums', 'compilation', 'favorites', 'cached'] }

// ─── Снимок экрана ──────────────────────────────────────────────────────────

test('пустое хранилище даёт главную, а не исключение', () => {
  const { api } = loadModule()
  const state = api.readViewState(KIND_WHITELIST)
  assert.equal(state.mobileTab, 'library')
  assert.equal(state.activeKind, 'artist')
  assert.equal(state.showAdmin, false)
  assert.equal(state.artistId, null)
  assert.equal(state.album, null)
  assert.equal(state.searchOpen, false)
})

test('валидный снимок возвращается как есть и переживает запись', () => {
  const { api, storage } = loadModule()
  api.writeViewState({
    mobileTab: 'artists',
    activeKind: 'compilation',
    libSection: 'recent',
    search: 'nirvana',
    showAdmin: true,
    artistId: 42,
    searchOpen: true,
    searchQuery: 'smells',
    playerExpanded: true,
  })
  const restored = api.readViewState(KIND_WHITELIST)
  assert.equal(restored.mobileTab, 'artists')
  assert.equal(restored.activeKind, 'compilation')
  assert.equal(restored.libSection, 'recent')
  assert.equal(restored.search, 'nirvana')
  assert.equal(restored.showAdmin, true)
  assert.equal(restored.artistId, 42)
  assert.equal(restored.searchOpen, true)
  assert.equal(restored.searchQuery, 'smells')
  assert.equal(restored.playerExpanded, true)
  assert.ok(storage.dump()[api.VIEW_STATE_KEY])
})

test('неизвестные таб, раздел и вкладка админки отбрасываются', () => {
  const storage = memoryStorage({
    tmusic_view_state_v1: JSON.stringify({
      mobileTab: 'podcasts',
      activeKind: 'audiobooks',
      libSection: 'downloads',
      adminTab: 'hacked',
    }),
  })
  const { api } = loadModule({ storage })
  const state = api.readViewState({ ...KIND_WHITELIST, adminTabs: ['overview', 'catalog'] })
  assert.equal(state.mobileTab, 'library')
  assert.equal(state.activeKind, 'artist')
  assert.equal(state.libSection, null)
  assert.equal(state.adminTab, null)
})

test('запись мержится: вкладка админки не затирает навигацию каталога', () => {
  const { api } = loadModule()
  api.writeViewState({ mobileTab: 'albums', activeKind: 'albums', artistId: 7 })
  api.writeViewState({ adminTab: 'analytics' })
  const state = api.readViewState({ ...KIND_WHITELIST, adminTabs: ['analytics'] })
  assert.equal(state.mobileTab, 'albums')
  assert.equal(state.activeKind, 'albums')
  assert.equal(state.artistId, 7)
  assert.equal(state.adminTab, 'analytics')
})

test('карточка альбома сводится к безопасным скалярам', () => {
  const { api } = loadModule()
  const state = api.writeViewState({
    album: {
      id: '12',
      folder_id: 34,
      title: 'Nevermind',
      artist_id: 5,
      kind: 'standalone_album',
      track_count: '12',
      year: 1991,
      cover_url: '/api/library/cover?key=x',
      custom_image_url: null,
      cached_tracks: [{ id: 1 }, { id: 2 }],
      onclick: 'alert(1)',
      title_long: 'x'.repeat(5000),
    },
  })
  assert.equal(state.album.id, 12)
  assert.equal(state.album.folder_id, 34)
  assert.equal(state.album.title, 'Nevermind')
  assert.equal(state.album.track_count, 12)
  assert.equal(state.album.cover_url, '/api/library/cover?key=x')
  assert.equal(state.album.cached_tracks, undefined)
  assert.equal(state.album.onclick, undefined)
  assert.equal(state.album.title_long, undefined)
})

test('виртуальные и битые карточки альбомов не восстанавливаются', () => {
  const { api } = loadModule()
  assert.equal(api.writeViewState({ album: { id: 1, folder_id: 2, is_cached_album: true } }).album, null)
  assert.equal(api.writeViewState({ album: { id: 1, folder_id: 2, is_favorites: true } }).album, null)
  assert.equal(api.writeViewState({ album: { id: 1, title: 'нет папки' } }).album, null)
  assert.equal(api.writeViewState({ album: { id: 'abc', folder_id: 2 } }).album, null)
})

test('битый JSON и мусор вместо объекта не ломают старт', () => {
  const broken = memoryStorage({ tmusic_view_state_v1: '{not json' })
  assert.equal(loadModule({ storage: broken }).api.readViewState().mobileTab, 'library')
  const array = memoryStorage({ tmusic_view_state_v1: '[1,2,3]' })
  assert.equal(loadModule({ storage: array }).api.readViewState().showAdmin, false)
})

test('недоступное хранилище (приватный режим) не бросает исключений', () => {
  const hostile = {
    getItem: () => {
      throw new Error('denied')
    },
    setItem: () => {
      throw new Error('denied')
    },
    removeItem: () => {
      throw new Error('denied')
    },
  }
  const { api } = loadModule({ storage: hostile })
  assert.equal(api.readViewState().mobileTab, 'library')
  assert.doesNotThrow(() => api.writeViewState({ mobileTab: 'profile' }))
  assert.doesNotThrow(() => api.clearViewState())
  assert.doesNotThrow(() => api.saveScrollY(120))
  assert.equal(api.readScrollY(), 0)
})

test('выход из аккаунта стирает и экран, и скролл', () => {
  const { api, storage } = loadModule()
  api.writeViewState({ mobileTab: 'profile', showAdmin: true })
  api.saveScrollY(900)
  api.clearViewState()
  assert.deepEqual(storage.dump(), {})
  assert.equal(api.readViewState().mobileTab, 'library')
  assert.equal(api.readScrollY(), 0)
})

// ─── Скролл ─────────────────────────────────────────────────────────────────

test('скролл пишется с троттлингом, flush сохраняет последнюю позицию', () => {
  const clock = fakeClock()
  const { api, storage } = loadModule({ clock })
  api.saveScrollY(100)
  api.saveScrollY(250) // в пределах окна — только в памяти
  assert.equal(storage.dump()[api.SCROLL_STATE_KEY], '100')
  clock.advance(300)
  api.saveScrollY(250)
  assert.equal(storage.dump()[api.SCROLL_STATE_KEY], '250')
  clock.advance(10)
  api.saveScrollY(777)
  api.flushScrollY()
  assert.equal(api.readScrollY(), 777)
})

test('flush без единого скролл-события не пишет ложный ноль', () => {
  const { api, storage } = loadModule()
  api.flushScrollY()
  assert.equal(storage.dump()[api.SCROLL_STATE_KEY], undefined)
})

test('readScrollY игнорирует мусор и отрицательные значения', () => {
  for (const raw of ['abc', '-40', '', 'NaN']) {
    const storage = memoryStorage({ tmusic_scroll_v1: raw })
    assert.equal(loadModule({ storage }).api.readScrollY(), 0)
  }
})

test('восстановление скролла ждёт, пока документ дорастёт до нужной высоты', () => {
  const frames = fakeFrames()
  const win = fakeWindow({ frames, innerHeight: 800 })
  const doc = fakeDocument(1000) // максимум 200px — цель недостижима
  const { api } = loadModule({ win, doc })
  api.restoreScrollY(600)
  frames.run()
  assert.deepEqual(win.scrollCalls, [], 'рано скроллить нельзя — контент ещё не загрузился')
  doc.html.scrollHeight = 5000 // каталог догрузился
  frames.run()
  assert.deepEqual(win.scrollCalls, [600])
  assert.equal(win.listenerCount('wheel'), 0, 'слушатели снимаются после успеха')
})

test('если контент так и не дорос — упираемся в низ, но не висим вечно', () => {
  const frames = fakeFrames()
  const clock = fakeClock()
  const win = fakeWindow({ frames, innerHeight: 800 })
  const doc = fakeDocument(1200)
  const { api } = loadModule({ win, doc, clock })
  api.restoreScrollY(5000, { timeoutMs: 1000 })
  frames.run()
  assert.deepEqual(win.scrollCalls, [])
  clock.advance(1000)
  frames.run()
  assert.deepEqual(win.scrollCalls, [400])
})

test('ручной скролл отменяет восстановление', () => {
  const frames = fakeFrames()
  const win = fakeWindow({ frames })
  const doc = fakeDocument(1000)
  const { api } = loadModule({ win, doc })
  api.restoreScrollY(600)
  win.emit('wheel', {})
  frames.run()
  assert.deepEqual(win.scrollCalls, [])
})

test('нулевая цель и отсутствие window не запускают цикл', () => {
  const frames = fakeFrames()
  const win = fakeWindow({ frames })
  const { api } = loadModule({ win, doc: fakeDocument(5000) })
  api.restoreScrollY(0)
  assert.equal(frames.pending(), 0)
  const headless = loadModule()
  assert.doesNotThrow(() => headless.api.restoreScrollY(400)())
})

test('браузерное восстановление скролла отключается (иначе оно сработает слишком рано)', () => {
  const { api, context } = loadModule()
  api.configureScrollRestoration()
  assert.equal(context.history.scrollRestoration, 'manual')
})

// ─── Контракты исходников ───────────────────────────────────────────────────

const source = (name) => fs.readFileSync(path.join(__dirname, '..', 'src', name), 'utf8')

test('MusicLibrary читает снимок при монтировании, а не после загрузки каталога', () => {
  const src = source('components/MusicLibrary.jsx')
  assert.match(src, /useState\(\(\) => readViewState\(\{ kinds: SECTIONS\.map\(\(s\) => s\.id\) \}\)\)/)
  assert.match(src, /useState\(bootView\.mobileTab\)/)
  assert.match(src, /useState\(bootView\.activeKind\)/)
  assert.match(src, /useState\(bootView\.libSection\)/)
  assert.match(src, /useState\(bootView\.showAdmin\)/)
})

test('снимок не перезаписывается до восстановления экрана', () => {
  const src = source('components/MusicLibrary.jsx')
  const persist = src.slice(src.indexOf('  // Снимок текущего экрана.'), src.indexOf('  // Окно поиска живёт'))
  assert.match(persist, /if \(!viewRestoredRef\.current\) return/)
  assert.match(persist, /writeViewState\(\{/)
})

test('админка открывается только админу, даже если снимок говорит обратное', () => {
  const src = source('components/MusicLibrary.jsx')
  assert.match(src, /\{showAdmin && isAdmin \? \(/)
  assert.match(src, /if \(bootView\.showAdmin && !isAdmin\) setShowAdmin\(false\)/)
})

test('выход из аккаунта сбрасывает снимок, а не возвращает чужой экран', () => {
  const src = source('components/MusicLibrary.jsx')
  const block = src.slice(src.indexOf('  // Выход из аккаунта не должен'), src.indexOf('  // ─── Позиция скролла'))
  assert.match(block, /clearViewState\(\)/)
  assert.match(block, /setMobileTab\('library'\)/)
  assert.match(block, /setActiveKind\('artist'\)/)
})

test('позиция скролла пишется на скролл и на pagehide, но не под зафиксированным body', () => {
  const src = source('components/MusicLibrary.jsx')
  assert.match(src, /addEventListener\('scroll', onScroll, \{ passive: true \}\)/)
  assert.match(src, /addEventListener\('pagehide', onHide\)/)
  assert.match(src, /document\.body\.style\.position === 'fixed'/)
  assert.match(src, /configureScrollRestoration\(\)/)
})

test('вкладка админки тоже переживает перезагрузку', () => {
  const src = source('components/AdminPanel.jsx')
  assert.match(src, /readViewState\(\{ adminTabs: TABS\.map\(\(t\) => t\.id\) \}\)\.adminTab \|\| 'overview'/)
  assert.match(src, /writeViewState\(\{ adminTab: tab \}\)/)
})
