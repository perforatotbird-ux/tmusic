/**
 * «Где я был» — снимок текущего экрана приложения.
 *
 * В приложении нет роутера: навигация (нижние табы, раздел каталога, открытый
 * артист, админка, окно поиска) живёт в состоянии компонентов. Из-за этого F5,
 * перезагрузка WebView или возврат Android-задачи в приложение выбрасывали
 * пользователя на главную — «Медиатеку».
 *
 * Модуль хранит снимок экрана в `sessionStorage` (снимок принадлежит текущей
 * вкладке/сессии: новая вкладка по-прежнему стартует с главной) и запоминает
 * позицию скролла, чтобы страница вернулась туда же, где её оставили.
 *
 * Всё, что читается из хранилища, считается недоверенным (XSS, расширение,
 * оборванная запись): каждое поле проверяется по белому списку, который
 * передаёт вызывающий код, и сводится к простым скалярам.
 */

export const VIEW_STATE_KEY = 'tmusic_view_state_v1'
export const SCROLL_STATE_KEY = 'tmusic_scroll_v1'

export const MOBILE_TABS = ['library', 'artists', 'albums', 'profile']
export const LIB_SECTIONS = ['recent']
export const ADMIN_TABS = [
  'overview',
  'catalog',
  'db',
  'backups',
  'settings',
  'users',
  'audit',
  'vfs',
  'analytics',
]

export const DEFAULT_VIEW_STATE = Object.freeze({
  mobileTab: 'library',
  activeKind: 'artist',
  libSection: null,
  search: '',
  showAdmin: false,
  artistId: null,
  album: null,
  albumArtist: null,
  adminTab: null,
  searchOpen: false,
  searchQuery: '',
  playerExpanded: false,
})

const MAX_TEXT = 200
const MAX_URL = 600
const MAX_QUERY = 120

// Последний снимок и последние белые списки: запись мержится в них, поэтому
// `writeViewState({ adminTab })` не затирает навигацию, записанную другим
// компонентом.
let cache = null
let lastOptions = {}

let lastScrollY = 0
let lastScrollWrite = 0
let scrollKnown = false

// ─── storage ────────────────────────────────────────────────────────────────

function session() {
  try {
    return typeof sessionStorage !== 'undefined' ? sessionStorage : null
  } catch {
    // Safari в приватном режиме бросает уже на обращении к свойству.
    return null
  }
}

function readSession(key) {
  const store = session()
  if (!store) return null
  try {
    return store.getItem(key)
  } catch {
    return null
  }
}

function writeSession(key, value) {
  const store = session()
  if (!store) return false
  try {
    store.setItem(key, value)
    return true
  } catch {
    // Квота / приватный режим — восстановление экрана не критично.
    return false
  }
}

function removeSession(key) {
  const store = session()
  if (!store) return
  try {
    store.removeItem(key)
  } catch {
    /* ignore */
  }
}

// ─── валидация полей ────────────────────────────────────────────────────────

function asText(value, max = MAX_TEXT) {
  if (typeof value !== 'string') return ''
  // Управляющие символы в JSON-строке не нужны ни одному полю.
  return value.replace(/[\u0000-\u001f\u007f]/g, '').slice(0, max)
}

function asId(value) {
  const n = typeof value === 'number' ? value : Number(value)
  return Number.isInteger(n) && n > 0 ? n : null
}

function asOptionalNumber(value) {
  const n = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(n) ? n : null
}

/**
 * Карточка альбома для восстановления шторки треклиста. Хранится только то,
 * что нужно `AlbumDetail`, чтобы перезапросить треки по `folder_id`.
 * Виртуальные карточки («В кэше», «Любимые») не восстанавливаются: у них нет
 * каталожной папки, а их треклист живёт на устройстве.
 */
function asAlbum(value) {
  if (!value || typeof value !== 'object') return null
  if (value.is_cached_album || value.is_favorites) return null
  const id = asId(value.id)
  const folderId = asId(value.folder_id)
  if (id == null || folderId == null) return null
  const imageUrl = asText(value.image_url, MAX_URL)
  const coverUrl = asText(value.cover_url, MAX_URL)
  const customImageUrl = asText(value.custom_image_url, MAX_URL)
  return {
    id,
    folder_id: folderId,
    title: asText(value.title, MAX_TEXT),
    artist_id: asId(value.artist_id),
    kind: asText(value.kind, 40) || null,
    track_count: asOptionalNumber(value.track_count),
    year: asOptionalNumber(value.year),
    image_url: imageUrl || null,
    cover_url: coverUrl || null,
    custom_image_url: customImageUrl || null,
  }
}

function asArtistRef(value) {
  if (!value || typeof value !== 'object') return null
  const displayName = asText(value.display_name, MAX_TEXT)
  const id = asId(value.id)
  if (!displayName && id == null) return null
  return { id, display_name: displayName || null }
}

/**
 * Приводит произвольный объект к валидному снимку экрана.
 * `options` — белые списки от вызывающего кода, чтобы список разделов
 * каталога не дублировался здесь и не разъезжался с `SECTIONS`.
 */
function normalize(raw, options = {}) {
  const tabs = Array.isArray(options.tabs) ? options.tabs : MOBILE_TABS
  const kinds = Array.isArray(options.kinds) ? options.kinds : null
  const libSections = Array.isArray(options.libSections) ? options.libSections : LIB_SECTIONS
  const adminTabs = Array.isArray(options.adminTabs) ? options.adminTabs : ADMIN_TABS
  const source = raw && typeof raw === 'object' ? raw : {}
  const state = { ...DEFAULT_VIEW_STATE }

  if (tabs.includes(source.mobileTab)) state.mobileTab = source.mobileTab
  const kind = asText(source.activeKind, 40)
  if (kind && (!kinds || kinds.includes(kind))) state.activeKind = kind
  if (libSections.includes(source.libSection)) state.libSection = source.libSection

  state.search = asText(source.search, MAX_QUERY)
  state.showAdmin = source.showAdmin === true
  state.artistId = asId(source.artistId)
  state.album = asAlbum(source.album)
  state.albumArtist = state.album ? asArtistRef(source.albumArtist) : null
  state.adminTab = adminTabs.includes(source.adminTab) ? source.adminTab : null
  state.searchOpen = source.searchOpen === true
  state.searchQuery = asText(source.searchQuery, MAX_QUERY)
  state.playerExpanded = source.playerExpanded === true
  return state
}

// ─── публичный API ──────────────────────────────────────────────────────────

/** Снимок экрана (всегда валидный объект, даже если хранилище пустое). */
export function readViewState(options = {}) {
  lastOptions = { ...lastOptions, ...(options || {}) }
  let raw = null
  const text = readSession(VIEW_STATE_KEY)
  if (text) {
    try {
      raw = JSON.parse(text)
    } catch {
      raw = null
    }
  }
  const state = normalize(raw, lastOptions)
  cache = state
  return { ...state }
}

/**
 * Мержит изменения в снимок и сохраняет его. Остальные поля не затираются:
 * MusicLibrary пишет навигацию, AdminPanel — свою вкладку.
 */
export function writeViewState(patch = {}) {
  if (!cache) cache = readViewState()
  const merged = { ...cache, ...(patch && typeof patch === 'object' ? patch : {}) }
  const state = normalize(merged, lastOptions)
  cache = state
  writeSession(VIEW_STATE_KEY, JSON.stringify(state))
  return { ...state }
}

/** Полный сброс (выход из аккаунта): следующий вход начинается с главной. */
export function clearViewState() {
  cache = null
  lastScrollY = 0
  lastScrollWrite = 0
  scrollKnown = false
  removeSession(VIEW_STATE_KEY)
  removeSession(SCROLL_STATE_KEY)
}

/** Позиция скролла, сохранённая перед перезагрузкой (0 — нечего восстанавливать). */
export function readScrollY() {
  const raw = readSession(SCROLL_STATE_KEY)
  const y = Number(raw)
  return Number.isFinite(y) && y > 0 ? Math.round(y) : 0
}

/**
 * Запоминает позицию скролла. Скролл-события летят десятками в секунду, а
 * `sessionStorage.setItem` синхронный, поэтому запись троттлится.
 */
export function saveScrollY(y, { throttleMs = 250 } = {}) {
  const value = Number(y)
  if (!Number.isFinite(value) || value < 0) return
  lastScrollY = Math.round(value)
  scrollKnown = true
  const now = Date.now()
  if (now - lastScrollWrite < throttleMs) return
  lastScrollWrite = now
  writeSession(SCROLL_STATE_KEY, String(lastScrollY))
}

/**
 * Принудительная запись (уход со страницы: `pagehide`). Без аргумента пишет
 * последнюю известную позицию, но только если скролл вообще отслеживался —
 * иначе фоновый вызов затёр бы сохранённое значение нулём.
 */
export function flushScrollY(y) {
  const value = Number(y)
  if (Number.isFinite(value) && value >= 0) {
    lastScrollY = Math.round(value)
    scrollKnown = true
  }
  if (!scrollKnown) return
  lastScrollWrite = Date.now()
  writeSession(SCROLL_STATE_KEY, String(lastScrollY))
}

/**
 * Браузер восстанавливает скролл сам, но слишком рано: каталог грузится
 * асинхронно, документ ещё короткий, и позиция сбрасывается в 0. Забираем
 * управление на себя и восстанавливаем позицию после того, как контент дорос.
 */
export function configureScrollRestoration() {
  try {
    if (typeof history !== 'undefined' && 'scrollRestoration' in history) {
      history.scrollRestoration = 'manual'
    }
  } catch {
    /* ignore */
  }
}

/**
 * Прокручивает страницу к сохранённой позиции, дожидаясь нужной высоты
 * документа (не дольше `timeoutMs`). Если контент так и не дорос — упираемся
 * в текущий низ, но остаёмся в том же списке.
 *
 * Возвращает функцию отмены. Ручной скролл/нажатие клавиши отменяет
 * восстановление, чтобы не выдёргивать страницу из-под пользователя.
 */
export function restoreScrollY(target, { timeoutMs = 4000, tolerance = 2 } = {}) {
  const y = Number(target)
  const win = typeof window !== 'undefined' ? window : null
  const doc = typeof document !== 'undefined' ? document : null
  if (!win || !doc || !Number.isFinite(y) || y <= 0) return () => {}

  let cancelled = false
  let frame = 0
  const startedAt = Date.now()

  const maxScroll = () =>
    Math.max(0, (doc.documentElement?.scrollHeight || 0) - (win.innerHeight || 0))

  const stop = () => {
    if (cancelled) return
    cancelled = true
    if (frame && typeof win.cancelAnimationFrame === 'function') win.cancelAnimationFrame(frame)
    win.removeEventListener?.('wheel', stop)
    win.removeEventListener?.('touchstart', stop)
    win.removeEventListener?.('keydown', stop)
  }

  const attempt = () => {
    if (cancelled) return
    const max = maxScroll()
    if (max >= y - tolerance) {
      win.scrollTo(0, y)
      stop()
      return
    }
    if (Date.now() - startedAt >= timeoutMs) {
      if (max > 0) win.scrollTo(0, max)
      stop()
      return
    }
    frame = win.requestAnimationFrame(attempt)
  }

  if (typeof win.requestAnimationFrame !== 'function') {
    if (maxScroll() >= y - tolerance) win.scrollTo(0, y)
    return () => {}
  }

  win.addEventListener?.('wheel', stop, { passive: true })
  win.addEventListener?.('touchstart', stop, { passive: true })
  win.addEventListener?.('keydown', stop)
  frame = win.requestAnimationFrame(attempt)
  return stop
}
