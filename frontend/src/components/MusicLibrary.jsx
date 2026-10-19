import React, { useState, useEffect, useMemo, useRef } from 'react'
import { libraryApi, bustCatalogCache } from '../api/library'
import { streamApi } from '../api/stream'
import { adminApi } from '../api/admin'
import { metadataApi } from '../api/metadata'
import { playbackApi } from '../api/playback'
import { missingCoverKeys, probedCoverKeys, markCoverProbed, parseCoverKey } from '../services/coverMissing'
import { resolveCoverUrl } from '../utils/cover'
import { trackRenamePatch } from '../utils/adminEdits'
import {
  groupCachedTracks,
  cachedArtistCard,
  cachedAlbumCard,
  isCachedArtistCardId,
  withReleaseMeta,
} from '../utils/cachedBrowse'
import { AlbumDetail } from './AlbumDetail'
import { LibraryPanel } from './HomeExtraPanels'
import { FolderBrowser } from './FolderBrowser'
import { AdminPanel } from './AdminPanel'
import { ProfileMenu, roleLabel } from './ProfileMenu'
import { PlaylistsModal } from './PlaylistsModal'
import { usePlayerStore } from '../store/playerStore'
import { useAuthStore } from '../store/authStore'
import { useFavoritesStore } from '../store/favoritesStore'
import {
  Search,
  Disc,
  Users,
  Layers,
  FolderHeart,
  Play,
  ArrowLeft,
  Image as ImageIcon,
  ShieldCheck,
  LogOut,
  Loader2,
  Music,
  X,
  Home,
  User,
  FolderTree,
  LayoutGrid,
  Unlink,
  Plus,
  AlertTriangle,
  Star,
  HardDrive,
  Pencil,
  Check,
  ChevronRight,
} from 'lucide-react'
import { useOfflineStore } from '../store/offlineStore'
import { useSearchStore } from '../store/searchStore'
import { UnifiedSearchModal } from './search/UnifiedSearchModal'
import { ClickableRow } from './ui/ClickableRow'

// Small square control for the admin-only inline editors in the artist header.
const artistHeaderBtn = {
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
  width: '34px',
  height: '34px',
  flexShrink: 0,
  borderRadius: '8px',
  border: '1px solid var(--line)',
  background: 'transparent',
  color: 'var(--dim)',
  cursor: 'pointer',
}

// Deterministic hue generator based on string hash for generative badges
function hashHue(str) {
  let h = 0
  const s = str || '?'
  for (let i = 0; i < s.length; i++) {
    h = (h * 31 + s.charCodeAt(i)) % 360
  }
  return h
}

// Fallback letter badge with deterministic gradient
function Fallback({ name, kind = 'artist', size = '100%', className = '' }) {
  const hue = hashHue(name || '?')
  const letter = (name || '?').trim()[0] || '?'
  const isCircle = kind === 'artist'

  return (
    <div
      className={className}
      style={{
        width: typeof size === 'number' ? `${size}px` : size,
        height: typeof size === 'number' ? `${size}px` : size,
        aspectRatio: '1 / 1',
        borderRadius: isCircle ? '50%' : '10px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: `linear-gradient(135deg, hsl(${hue}, 40%, 25%), hsl(${(hue + 45) % 360}, 45%, 15%))`,
        border: '1px solid rgba(255, 255, 255, 0.1)',
        userSelect: 'none',
        flexShrink: 0,
      }}
    >
      <span
        style={{
          fontSize: isCircle ? 'clamp(26px, 7vw, 40px)' : 'clamp(22px, 6vw, 36px)',
          fontWeight: '900',
          color: 'var(--accent, #D4FF3F)',
          textTransform: 'uppercase',
          letterSpacing: '-0.02em',
          opacity: 0.9,
        }}
      >
        {letter}
      </span>
    </div>
  )
}

// Image with automatic fallback on error (server caches covers with ETag)
function ImgWithFallback({ name, kind = 'artist', src, alt = '', style = {}, size = '100%', className = '' }) {
  const [hasError, setHasError] = useState(false)
  const timerRef = useRef(null)

  useEffect(() => {
    setHasError(false)
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current)
    }
  }, [src])

  // Skip requests the manifest already proved missing (no GET + no retries).
  const coverKey = parseCoverKey(src)
  const manifestMissing =
    !!coverKey && missingCoverKeys.has(`${coverKey.custom ? 1 : 0}:${coverKey.key}`)

  if (!src || hasError || manifestMissing) {
    return <Fallback name={name} kind={kind} size={size} className={className} />
  }

  const handleError = () => {
    setHasError(true)
  }

  return (
    <img
      key={src}
      src={src}
      alt={alt || name}
      onError={handleError}
      loading="lazy"
      className={className}
      style={{
        objectFit: 'cover',
        ...style,
      }}
    />
  )
}

const FAVORITES_KIND = 'favorites'
const FAVORITES_CARD_ID = 'tmusic:favorites'

const CACHED_KIND = 'cached'

const SECTIONS = [
  { id: 'artist', label: 'Исполнители', icon: Users, isAlbum: false },
  { id: 'albums', label: 'Альбомы', icon: Disc, isAlbum: true },
  { id: 'compilation', label: 'Сборники', icon: Layers, isAlbum: false },
  { id: FAVORITES_KIND, label: 'Любимые', icon: Star, isAlbum: true },
  // The cache is browsed as Artist → Album → Track, so this view shows artist
  // cards even though every real card in it is a release.
  { id: CACHED_KIND, label: 'В кэше', icon: HardDrive, isAlbum: false },
  { id: 'standalone_album', label: 'Отдельные релизы', icon: Disc, isAlbum: true },
  { id: 'genre_collection', label: 'Коллекции', icon: FolderHeart, isAlbum: false },
  { id: 'all', label: 'Все категории', icon: Layers, isAlbum: false },
]

const SECTIONS_BY_ID = Object.fromEntries(SECTIONS.map((s) => [s.id, s]))

// «Любимые» — виртуальный сборник: карточка стоит в каталоге рядом с настоящими
// сборниками, но треки берёт из избранного пользователя, а не из folder_id.
const FAVORITES_LIMIT = 500

function favoritesCard(trackCount) {
  return {
    id: FAVORITES_CARD_ID,
    title: 'Любимые',
    kind: 'compilation',
    track_count: trackCount,
    is_favorites: true,
  }
}

const FAVORITES_RELEASE = { title: 'Любимые', artistName: 'Сборник', coverUrl: null }

function favoriteToTrack(row) {
  return {
    id: row.file_id,
    name: row.file_name || `Трек ${row.file_id}`,
    stream_url: row.stream_url || `/api/stream/${row.file_id}`,
    folder_id: row.folder_id ?? null,
    mime_type: row.mime_type || 'audio/mpeg',
    size: row.size ?? null,
  }
}

function tracksWord(n) {
  const mod10 = n % 10
  const mod100 = n % 100
  if (mod10 === 1 && mod100 !== 11) return 'трек'
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return 'трека'
  return 'треков'
}

// Card kinds for the admin "create object" form (same catalog kinds as AdminCatalog).
const CARD_KINDS = [
  { id: 'artist', label: 'Исполнитель' },
  { id: 'compilation', label: 'Сборник' },
  { id: 'standalone_album', label: 'Отдельный релиз' },
  { id: 'genre_collection', label: 'Коллекция' },
]
const CARD_KIND_LABEL = Object.fromEntries(CARD_KINDS.map((k) => [k.id, k.label]))

// The "Артисты" / "Альбомы" tabs are fixed views catalogued under these kinds.
const MOBILE_TAB_KIND = { artists: 'artist', albums: 'albums' }

// Одна страница каталога главной. Бэкенд отдаёт максимум 500 за раз;
// хвост каталога (после буквы Г) подгружается кнопкой «Показать ещё».
const LIB_PAGE_SIZE = 200

export function MusicLibrary({ onOpenCoverPicker }) {
  const [items, setItems] = useState([])
  // UI-05: a network/server failure must NOT be disguised as "nothing found".
  // This state holds the failure message; the grid renders it as an alert with a
  // retry button (never as an empty grid).
  const [loadError, setLoadError] = useState(null)
  // UI-21: guard the album play button so a double-tap can't enqueue twice.
  const [playBusyId, setPlayBusyId] = useState(null)
  const [selectedArtist, setSelectedArtist] = useState(null)
  // UI-05: when an artist detail fetch fails, keep the user informed instead of
  // silently dropping them back into an empty catalog.
  const [detailError, setDetailError] = useState(null) // { id, message } | null
  const [similarArtists, setSimilarArtists] = useState([])
  const [similarLoading, setSimilarLoading] = useState(false)
  const [selectedAlbum, setSelectedAlbum] = useState(null) // { album, artist } — AlbumDetail sheet
  const [activeKind, setActiveKind] = useState('artist')
  const [mobileTab, setMobileTab] = useState('library') // 'library' | 'artists' | 'albums' | 'search' | 'profile'
  const [search, setSearch] = useState('')
  const [isLoading, setIsLoading] = useState(true)
  const [hasMore, setHasMore] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const [stats, setStats] = useState(null)
  // 'covers' | 'folders' — Windows-style VFS hierarchy vs cover catalog
  const [viewMode, setViewMode] = useState(() => {
    try {
      return localStorage.getItem('tmusic_view_mode') === 'folders' ? 'folders' : 'covers'
    } catch {
      return 'covers'
    }
  })
  // Top bar section: catalog is default; 'recent' opens an inline panel
  const [libSection, setLibSection] = useState(null) // null | 'recent'
  // «Плейлисты» из меню профиля открываются отдельным окном-редактором,
  // а не встроенной панелью: нужно править и название, и содержимое.
  const [playlistsOpen, setPlaylistsOpen] = useState(false)
  const [showAdmin, setShowAdmin] = useState(false)
  // Треки раздела «Любимые»: виртуальный сборник строится на клиенте,
  // поэтому сырые строки избранного живут отдельно от каталожных items.
  const [favoriteRows, setFavoriteRows] = useState([])
  // Bumped when the cover manifest arrives so the grid re-renders with fresh
  // missing-cover knowledge (the registry itself is not React state).
  const [, setManifestTick] = useState(0)
  // Monotonic request id: a slow earlier fetch must not overwrite a newer one
  // (fast typing fires parallel loadData calls that resolve out of order).
  const loadSeq = useRef(0)

  const { playQueue, prepareQueue, setPlayerExpanded, currentTrack, currentRelease, isPlaying } = usePlayerStore()
  const { user, isAuthenticated, logout, setAuthModalOpen, checkAuth } = useAuthStore()
  // Избранное переключают из плеера и альбома; подписка на ids держит счётчик
  // на карточке-сборнике живым без повторного открытия раздела.
  const favoriteIds = useFavoritesStore((s) => s.ids)

  // Catalog curation (rename / covers / collections) is admin-only.
  const isAdmin = user?.role === 'admin' || user?.username === 'admin'

  // Inline rename of the open artist card. The name is written back into
  // `selectedArtist` so the header updates without refetching the detail.
  const [renamingArtist, setRenamingArtist] = useState(false)
  const [artistNameDraft, setArtistNameDraft] = useState('')
  const [artistRenameBusy, setArtistRenameBusy] = useState(false)

  const saveArtistName = async () => {
    const body = trackRenamePatch(artistNameDraft, selectedArtist?.display_name)
    if (!body) {
      setRenamingArtist(false)
      return
    }
    setArtistRenameBusy(true)
    try {
      await adminApi.patchArtist(selectedArtist.id, { display_name: body.name })
      setSelectedArtist((prev) => (prev ? { ...prev, display_name: body.name } : prev))
      bustCatalogCache()
      setRenamingArtist(false)
    } catch (e) {
      console.error('Artist rename failed:', e)
      usePlayerStore.getState().pushNotice({ kind: 'warn', text: 'Не удалось переименовать артиста.' })
    } finally {
      setArtistRenameBusy(false)
    }
  }

  // Which catalog section is on screen: the Артисты/Альбомы tabs are fixed views,
  // everything else follows the category strip picked under Медиатека.
  const viewKind = MOBILE_TAB_KIND[mobileTab] || activeKind
  const catalogKey = JSON.stringify([viewKind, search, isAuthenticated, user?.id])
  const catalogKeyRef = useRef(catalogKey)
  catalogKeyRef.current = catalogKey
  const moreRequestRef = useRef(null)
  const currentSection = SECTIONS_BY_ID[viewKind] || SECTIONS[0]
  const isAlbumSection = currentSection.isAlbum
  const folderMode = viewMode === 'folders'
  // «Любимые» — виртуальный сборник: на бэкенде такого kind нет, поэтому любой
  // каталожный запрос уходит как 'all', а карточка показывается только в каталоге.
  const isFavoritesView = viewKind === FAVORITES_KIND && mobileTab !== 'search'
  const requestKind = viewKind === FAVORITES_KIND ? 'all' : viewKind

  const setLibraryViewMode = (mode) => {
    setViewMode(mode)
    try {
      localStorage.setItem('tmusic_view_mode', mode)
    } catch {
      /* ignore */
    }
  }

  // Debounce search - only trigger after 300ms of no typing
  const [debouncedSearch, setDebouncedSearch] = useState(search)
  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(search), 300)
    return () => clearTimeout(t)
  }, [search])

  // «Любимые» — виртуальный сборник: вместо каталожного запроса тянем избранное
  // и кладём в items одну синтетическую карточку с числом треков.
  const favoritesQuery = (debouncedSearch || search || '').trim().toLowerCase()
  const favoriteTracks =
    isFavoritesView && favoritesQuery
      ? favoriteRows.filter((r) => (r.file_name || '').toLowerCase().includes(favoritesQuery))
      : favoriteRows
  const favoritesCount = favoriteTracks.length

  const cachedTracksList = useOfflineStore((s) => s.cachedTracksList)
  const isCachedView = viewKind === CACHED_KIND && mobileTab !== 'search'

  const cachedQuery = (debouncedSearch || search || '').trim().toLowerCase()
  const cachedTracks = cachedQuery
    ? cachedTracksList.filter((r) =>
        `${r.title || ''} ${r.artist || ''} ${r.album || ''}`.toLowerCase().includes(cachedQuery)
      )
    : cachedTracksList
  const cachedCount = cachedTracks.length

  // Artist → Album → Track tree rebuilt from the flat IndexedDB rows. The
  // groups are looked up by card id when a cached artist is opened.
  const cachedArtistGroups = useMemo(() => groupCachedTracks(cachedTracks), [cachedTracks])
  const cachedGroupById = useMemo(() => {
    const map = new Map()
    for (const group of cachedArtistGroups) map.set(cachedArtistCard(group).id, group)
    return map
  }, [cachedArtistGroups])

  useEffect(() => {
    if (!isFavoritesView) return undefined
    if (!isAuthenticated) {
      setFavoriteRows([])
      setIsLoading(false)
      return undefined
    }
    let cancelled = false
    setIsLoading(true)
    ;(async () => {
      try {
        const res = await playbackApi.listFavorites(FAVORITES_LIMIT)
        if (!cancelled) setFavoriteRows(res?.items || [])
      } catch (e) {
        console.error('Failed to load favorites:', e)
        if (!cancelled) setFavoriteRows([])
      } finally {
        if (!cancelled) setIsLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [isFavoritesView, isAuthenticated, user?.id])

  // Зависимость — число, а не массив: свежий массив на каждый рендер зациклил бы setItems.
  useEffect(() => {
    if (!isFavoritesView) return
    setItems([favoritesCard(favoritesCount)])
    setHasMore(false)
  }, [isFavoritesView, favoritesCount])

  useEffect(() => {
    if (!isCachedView) return
    setItems(cachedArtistGroups.map(cachedArtistCard))
    setHasMore(false)
    setIsLoading(false)
  }, [isCachedView, cachedArtistGroups])

  useEffect(() => {
    if (isAuthenticated) {
      loadData()
    } else {
      setIsLoading(false)
    }
  }, [viewKind, debouncedSearch, isAuthenticated, user?.id])

  useEffect(() => {
    if (isAuthenticated) loadStats()
  }, [isAuthenticated])

  useEffect(() => {
    const handleVfsImported = () => {
      bustCatalogCache()
      if (isAuthenticated) {
        loadStats()
        loadData()
      }
    }
    window.addEventListener('tmusic:vfs-imported', handleVfsImported)
    return () => window.removeEventListener('tmusic:vfs-imported', handleVfsImported)
  }, [isAuthenticated, viewKind, debouncedSearch])

  // UI-08 / UI-25: after an admin edits a cover/title (or any catalog mutation),
  // refresh the grid without a full page reload — keeps playback alive.
  useEffect(() => {
    const handleCatalogChanged = () => {
      bustCatalogCache()
      if (isAuthenticated) {
        loadStats()
        loadData()
      }
    }
    window.addEventListener('tmusic:catalog-changed', handleCatalogChanged)
    return () => window.removeEventListener('tmusic:catalog-changed', handleCatalogChanged)
  }, [isAuthenticated, viewKind, debouncedSearch])

  // Prefetch cover existence for the current grid: images proven missing render
  // the fallback badge without firing a GET (+retries). Fire-and-forget — any
  // failure leaves the registry empty, i.e. previous behavior.
  useEffect(() => {
    const byCustom = { 0: [], 1: [] }
    for (const it of items) {
      for (const u of [it?.custom_image_url, it?.image_url]) {
        const p = parseCoverKey(u)
        if (!p) continue
        const id = `${p.custom ? 1 : 0}:${p.key}`
        // Already answered by an earlier probe (found or missing): skip it.
        if (probedCoverKeys.has(id)) continue
        byCustom[p.custom ? 1 : 0].push(p.key)
      }
    }
    let cancelled = false
    ;(async () => {
      try {
        const batches = await Promise.all([0, 1].map(async (c) => {
          if (byCustom[c].length === 0) return []
          const res = await libraryApi.coversManifest(byCustom[c], c)
          return Array.isArray(res?.missing) ? res.missing.map((k) => `${c}:${k}`) : []
        }))
        const missing = batches.flat()
        if (!cancelled) {
          for (const c of [0, 1]) for (const k of byCustom[c]) markCoverProbed(c, k)
          for (const k of missing) missingCoverKeys.add(k)
          if (missing.length > 0) setManifestTick((t) => t + 1)
        }
      } catch {
        // Ignore — images fall back to individual fetching.
      }
    })()
    return () => {
      cancelled = true
    }
  }, [items])

  const loadStats = async () => {
    try {
      const s = await libraryApi.getStats()
      setStats(s)
    } catch {
      // Ignore
    }
  }

  const loadData = async () => {
    // Виртуальные сборники «Любимые» и «В кэше» не имеют kind на бэкенде — их items
    // строят отдельные эффекты на клиенте, каталожный запрос здесь не нужен.
    if (viewKind === FAVORITES_KIND || viewKind === CACHED_KIND) return
    const mySeq = ++loadSeq.current
    const key = catalogKeyRef.current
    moreRequestRef.current = null
    setLoadingMore(false)
    setIsLoading(true)
    setHasMore(false)
    setLoadError(null)
    try {
      // Never send search=null — backend treats it as a literal filter.
      const searchVal = (debouncedSearch || search || '').trim()
      if (viewKind === 'albums') {
        const data = await libraryApi.getAlbums({
          ...(searchVal ? { search: searchVal } : {}),
          limit: LIB_PAGE_SIZE,
          offset: 0,
        })
        if (mySeq === loadSeq.current && key === catalogKeyRef.current) {
          setItems(data || [])
          setHasMore((data || []).length >= LIB_PAGE_SIZE)
        }
      } else {
        const data = await libraryApi.getArtists({
          ...(viewKind === 'all' ? {} : { kind: viewKind }),
          ...(searchVal ? { search: searchVal } : {}),
          limit: LIB_PAGE_SIZE,
          offset: 0,
        })
        if (mySeq === loadSeq.current && key === catalogKeyRef.current) {
          setItems(data || [])
          setHasMore((data || []).length >= LIB_PAGE_SIZE)
        }
      }
    } catch (e) {
      console.error('Failed to load library catalog:', e)
      if (mySeq === loadSeq.current && key === catalogKeyRef.current) {
        // UI-05: surface the failure instead of silently emptying the grid.
        setLoadError(e?.message || 'Не удалось загрузить каталог. Проверьте подключение.')
        setItems([])
        setHasMore(false)
      }
    } finally {
      if (mySeq === loadSeq.current && key === catalogKeyRef.current) setIsLoading(false)
    }
  }

  // Догрузка следующей страницы каталога (кнопка «Показать ещё»).
  const loadMore = async () => {
    if (loadingMore || !hasMore || moreRequestRef.current) return
    const requestId = Symbol()
    moreRequestRef.current = requestId
    const sequence = loadSeq.current
    const key = catalogKeyRef.current
    setLoadingMore(true)
    try {
      const searchVal = (debouncedSearch || search || '').trim()
      const offset = items.length
      let data
      if (viewKind === 'albums') {
        data = await libraryApi.getAlbums({
          ...(searchVal ? { search: searchVal } : {}),
          limit: LIB_PAGE_SIZE,
          offset,
        })
      } else {
        data = await libraryApi.getArtists({
          ...(viewKind === 'all' ? {} : { kind: viewKind }),
          ...(searchVal ? { search: searchVal } : {}),
          limit: LIB_PAGE_SIZE,
          offset,
        })
      }
      if (sequence !== loadSeq.current || key !== catalogKeyRef.current) return
      const batch = data || []
      setItems((prev) => [...prev, ...batch])
      setHasMore(batch.length >= LIB_PAGE_SIZE)
    } catch (e) {
      console.error('Failed to load more catalog items:', e)
    } finally {
      if (moreRequestRef.current === requestId) {
        moreRequestRef.current = null
        setLoadingMore(false)
      }
    }
  }

  // Панель под сеткой: счётчик + кнопка «Показать ещё».
  const renderLoadMore = () => {
    // У виртуальных сборников одна карточка — счётчик «Показано» только мешал бы.
    if (items.length === 0 || viewKind === FAVORITES_KIND || viewKind === CACHED_KIND) return null
    return (
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6, padding: '18px 0 8px' }}>
        <div style={{ fontSize: 12, color: 'var(--dim)' }}>Показано: {items.length}{hasMore ? ' — есть ещё' : ''}</div>
        {hasMore && (
          <button
            type="button"
            onClick={loadMore}
            disabled={loadingMore}
            style={{
              background: 'var(--accent-muted)', color: 'var(--accent)',
              border: '1px solid var(--line)', borderRadius: 8,
              minHeight: 40, padding: '8px 18px', fontWeight: 800, fontSize: 13,
              cursor: loadingMore ? 'default' : 'pointer', opacity: loadingMore ? 0.6 : 1,
              display: 'inline-flex', alignItems: 'center', gap: 8,
            }}
          >
            {loadingMore && <Loader2 size={14} className="animate-spin" />}
            {loadingMore ? 'Загрузка…' : 'Показать ещё'}
          </button>
        )}
      </div>
    )
  }

  const handleSearchChange = (e) => {
    setSearch(e.target.value)
  }

  const clearSearch = () => {
    setSearch('')
  }

  // A bottom-nav tap is always visible: the section tabs drop a stale search
  // filter (they show the whole kind), and the scroll returns to the grid head
  // so re-tapping the tab you are already on still gives feedback.
  const goToTab = (tab) => {
    if (tab === 'search') {
      useSearchStore.getState().openSearch()
      return
    }
    setMobileTab(tab)
    setSelectedArtist(null)
    if (MOBILE_TAB_KIND[tab]) setSearch('')
    window.scrollTo(0, 0)
  }

  const openArtist = async (artistId) => {
    // «В кэше» artists have no catalog row to fetch — the group is already in
    // memory, so open it directly instead of hitting a 404.
    if (isCachedArtistCardId(artistId)) {
      const group = cachedGroupById.get(artistId)
      if (!group) return
      setSimilarArtists([])
      setDetailError(null)
      setSelectedArtist({
        id: artistId,
        display_name: group.name,
        kind: 'artist',
        bio: null,
        album_count: group.albums.length,
        albums: group.albums.map(cachedAlbumCard),
        children: [],
        image_url: group.coverUrl || undefined,
        is_cached_group: true,
      })
      window.scrollTo(0, 0)
      return
    }
    setIsLoading(true)
    setDetailError(null)
    try {
      const detail = await libraryApi.getArtistDetail(artistId)
      setSelectedArtist(detail)
    } catch (e) {
      console.error('Failed to load artist detail:', e)
      setDetailError({ id: artistId, message: e?.message || 'Не удалось загрузить артиста' })
    } finally {
      setIsLoading(false)
    }
  }

  // Last.fm similar artists for the open artist card (best-effort).
  useEffect(() => {
    let cancelled = false
    setSimilarArtists([])
    if (!selectedArtist?.display_name || !isAuthenticated) return undefined
    setSimilarLoading(true)
    ;(async () => {
      try {
        const info = await metadataApi.artist(selectedArtist.display_name)
        if (!cancelled) setSimilarArtists(info?.similar || [])
      } catch {
        if (!cancelled) setSimilarArtists([])
      } finally {
        if (!cancelled) setSimilarLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [selectedArtist?.id, selectedArtist?.display_name, isAuthenticated])

  const openSimilarArtist = async (name) => {
    setSelectedArtist(null)
    setSearch(name)
    setMobileTab('artists')
    window.scrollTo(0, 0)
    try {
      const list = await libraryApi.getArtists({ search: name, limit: 20 })
      const exact = (list || []).find(
        (a) => (a.display_name || '').toLowerCase() === name.toLowerCase()
      )
      if (exact) await openArtist(exact.id)
    } catch (e) {
      console.error('Similar artist lookup failed:', e)
    }
  }

  const handlePlayAlbum = async (album, artist = null) => {
    // Safety guard: if this card is a compilation or has child folders, open it as an artist/container
    if (album.children_count > 0 || album.kind === 'compilation' || album.kind === 'genre_collection') {
      await openArtist(album.id)
      return
    }
    if (album.is_favorites) {
      const tracks = favoriteTracks.map(favoriteToTrack)
      if (tracks.length > 0) playQueue(tracks, 0, FAVORITES_RELEASE)
      return
    }
    // Album inside a «В кэше» artist — the tracklist is already local.
    if (album.is_cached_album) {
      const tracks = album.cached_tracks || []
      if (tracks.length === 0) return
      playQueue(tracks, 0, {
        title: album.title,
        artistName: (artist && artist.display_name) || album.title || 'Кэш устройства',
        coverUrl: resolveCoverUrl(album, null),
      })
      return
    }
    // UI-21: a double-tap must not enqueue twice while the tracklist is fetching.
    if (playBusyId) return
    setPlayBusyId(album.id)
    try {
      const recursive = album.is_tracks === false && !album.children_count && album.kind !== 'compilation'
      const tracks = await streamApi.getFolderTracks(album.folder_id, recursive)
      if (tracks && tracks.length > 0) {
        const artistName = (artist && artist.display_name) || album.title || 'Альбом'
        const coverUrl = resolveCoverUrl(album)

        // Stream rows carry no artist/album/cover — attach the release context
        // to each queue track so the offline cache stores it and «В кэше»
        // groups the track under its real Artist → Album (null fallback: a
        // stored '/icon.svg' would mask the fallback badge).
        const queueTracks = withReleaseMeta(tracks, {
          artist: artistName,
          album: album.title,
          coverUrl: resolveCoverUrl(album, null),
        })
        playQueue(queueTracks, 0, {
          title: album.title,
          artistName,
          coverUrl,
        })
      }
    } catch (e) {
      console.error('Failed to load tracks for album:', e)
      // UI-05: a silent no-op looked like a dead button.
      usePlayerStore.getState().pushNotice({ kind: 'warn', text: 'Не удалось запустить альбом. Попробуйте ещё раз.' })
    } finally {
      setPlayBusyId(null)
    }
  }

  // Cover tap → open the fullscreen player with the album loaded but NOT
  // playing (Spotify-style: the bar's play or the queue's item starts audio).
  const openAlbum = async (album, artist = null) => {
    // Safety guard: if this card is a compilation or has child folders, open it as an artist/container
    if (album.children_count > 0 || album.kind === 'compilation' || album.kind === 'genre_collection') {
      await openArtist(album.id)
      return
    }
    if (album.is_favorites) {
      const tracks = favoriteTracks.map(favoriteToTrack)
      if (tracks.length === 0) return
      prepareQueue(tracks, 0, FAVORITES_RELEASE)
      setPlayerExpanded(true)
      return
    }
    // Cached album: open the tracklist sheet with the local tracks instead of
    // preparing a queue — there is no folder_id to stream from.
    if (album.is_cached_album) {
      setSelectedAlbum({ album, artist })
      return
    }
    try {
      const recursive = album.is_tracks === false && !album.children_count && album.kind !== 'compilation'
      const tracks = await streamApi.getFolderTracks(album.folder_id, recursive)
      if (!tracks || tracks.length === 0) return
      const artistName = (artist && artist.display_name) || album.title || 'Альбом'
      const coverUrl = resolveCoverUrl(album)
      const queueTracks = withReleaseMeta(tracks, {
        artist: artistName,
        album: album.title,
        coverUrl: resolveCoverUrl(album, null),
      })
      prepareQueue(queueTracks, 0, { title: album.title, artistName, coverUrl })
      setPlayerExpanded(true)
    } catch (e) {
      console.error('Failed to load tracks for album:', e)
      setSelectedAlbum({ album, artist })
    }
  }

  // ─── Admin card curation: drag-and-drop + create (admin-only) ────────────
  // dragItem: {type:'artist'|'album', id, parent_id?|artist_id?}; dropKey is a
  // `kind:id` string so artist and album grids never fight over the highlight.
  const [dragItem, setDragItem] = useState(null)
  const [dropKey, setDropKey] = useState(null)
  const [actionError, setActionError] = useState(null)
  const [mutationBusy, setMutationBusy] = useState(false)
  const [showCreate, setShowCreate] = useState(false)
  const [newCard, setNewCard] = useState({ name: '', kind: 'artist', bio: '' })
  const [creating, setCreating] = useState(false)

  const reportError = (e, fallback) => {
    console.error(fallback, e)
    setActionError(e?.detail || e?.message || fallback)
  }

  const refreshCatalog = async () => {
    bustCatalogCache()
    await Promise.all([loadData(), loadStats()])
    if (selectedArtist) {
      try {
        setSelectedArtist(await libraryApi.getArtistDetail(selectedArtist.id))
      } catch {
        // Detail row may have been deleted upstream — fall back to the grid.
        setSelectedArtist(null)
      }
    }
  }

  // targetId = null detaches the card to the catalog root.
  const moveArtistInto = async (artistId, targetId) => {
    setMutationBusy(true)
    setActionError(null)
    try {
      await adminApi.patchArtist(artistId, { parent_id: targetId })
      await refreshCatalog()
    } catch (e) {
      reportError(e, 'Не удалось переместить карточку')
    } finally {
      setMutationBusy(false)
    }
  }

  const moveAlbumTo = async (albumId, targetArtistId) => {
    setMutationBusy(true)
    setActionError(null)
    try {
      await adminApi.patchAlbum(albumId, { artist_id: targetArtistId })
      await refreshCatalog()
    } catch (e) {
      reportError(e, 'Не удалось переместить релиз')
    } finally {
      setMutationBusy(false)
    }
  }

  const startArtistDrag = (e, artist) => {
    e.dataTransfer.effectAllowed = 'move'
    e.dataTransfer.setData('text/plain', `artist:${artist.id}`)
    setDragItem({ type: 'artist', id: artist.id, parent_id: artist.parent_id ?? null })
  }

  const startAlbumDrag = (e, album) => {
    e.dataTransfer.effectAllowed = 'move'
    e.dataTransfer.setData('text/plain', `album:${album.id}`)
    setDragItem({ type: 'album', id: album.id, artist_id: album.artist_id })
  }

  const endDrag = () => {
    setDragItem(null)
    setDropKey(null)
  }

  // Artist-level cards accept: artist cards (nest) and albums (re-parent).
  const artistDropAllowed = (artist) => {
    if (!dragItem) return false
    if (dragItem.type === 'artist') {
      return dragItem.id !== artist.id && dragItem.parent_id !== artist.id
    }
    return dragItem.artist_id !== artist.id
  }

  const overArtistCard = (e, artist) => {
    if (!artistDropAllowed(artist)) return
    e.preventDefault()
    e.stopPropagation()
    e.dataTransfer.dropEffect = 'move'
    setDropKey(`artist:${artist.id}`)
  }

  const dropOnArtistCard = (e, artist) => {
    e.preventDefault()
    e.stopPropagation()
    const item = dragItem
    endDrag()
    if (!item || !artistDropAllowed(artist)) return
    if (item.type === 'artist') moveArtistInto(item.id, artist.id)
    else moveAlbumTo(item.id, artist.id)
  }

  // Album cards accept: other albums (move to the target's owner).
  const albumDropAllowed = (album) =>
    !!dragItem &&
    dragItem.type === 'album' &&
    dragItem.id !== album.id &&
    dragItem.artist_id !== album.artist_id

  const overAlbumCard = (e, album) => {
    if (!albumDropAllowed(album)) return
    e.preventDefault()
    e.stopPropagation()
    e.dataTransfer.dropEffect = 'move'
    setDropKey(`album:${album.id}`)
  }

  const dropOnAlbumCard = (e, album) => {
    e.preventDefault()
    e.stopPropagation()
    const item = dragItem
    endDrag()
    if (!item || !albumDropAllowed(album)) return
    moveAlbumTo(item.id, album.artist_id)
  }

  const createCard = async () => {
    const name = newCard.name.trim()
    if (!name || creating) return
    setCreating(true)
    setActionError(null)
    try {
      const created = await adminApi.createCollection({
        display_name: name,
        kind: newCard.kind,
        ...(newCard.bio.trim() ? { bio: newCard.bio.trim() } : {}),
      })
      setNewCard({ name: '', kind: newCard.kind, bio: '' })
      setShowCreate(false)
      bustCatalogCache()
      setActiveKind(created.kind)
      setSearch('')
      await loadData()
    } catch (e) {
      reportError(e, 'Не удалось создать карточку')
    } finally {
      setCreating(false)
    }
  }
  // ─── End admin card curation ─────────────────────────────────────────────

  const handlePlayAllArtist = async (artist) => {
    // If the card has children or is a compilation, do not fetch thousands of tracks recursively
    if (
      artist?.children_count > 0 ||
      (artist?.children && artist.children.length > 0) ||
      artist?.kind === 'compilation' ||
      artist?.kind === 'genre_collection'
    ) {
      usePlayerStore.getState().pushNotice({ kind: 'info', text: 'Для сборников откройте конкретную папку для воспроизведения.' })
      return
    }
    // «В кэше» artist: every cached track of the group, no server round-trip.
    if (artist?.is_cached_group) {
      const group = cachedGroupById.get(artist.id)
      const tracks = group ? group.albums.flatMap((a) => a.tracks) : []
      if (tracks.length === 0) {
        useOfflineStore.getState().setStorageModalOpen(true)
        return
      }
      playQueue(tracks, 0, {
        title: artist.display_name,
        artistName: artist.display_name,
        coverUrl: resolveCoverUrl(artist, null),
      })
      return
    }
    try {
      const tracks = await streamApi.getFolderTracks(artist.folder_id, true)
      if (tracks && tracks.length > 0) {
        // Recursive tracklist spans several albums — per-track album is
        // unknown here, but the artist is: attach it so «В кэше» buckets the
        // tracks under the real artist instead of «Без исполнителя».
        const queueTracks = withReleaseMeta(tracks, {
          artist: artist.display_name,
          coverUrl: resolveCoverUrl(artist, null),
        })
        playQueue(queueTracks, 0, {
          title: 'Все треки',
          artistName: artist.display_name,
          coverUrl: resolveCoverUrl(artist),
        })
      }
    } catch (e) {
      console.error('Failed to play all artist tracks:', e)
    }
  }

  // ─── Card renderers (shared by catalog / search / detail grids) ──────────
  // DnD attributes exist only for admins; everyone else gets plain cards.
  const artistCardDnd = (artist) =>
    isAdmin
      ? {
          draggable: true,
          onDragStart: (e) => startArtistDrag(e, artist),
          onDragEnd: endDrag,
          onDragOver: (e) => overArtistCard(e, artist),
          onDragLeave: () => setDropKey((k) => (k === `artist:${artist.id}` ? null : k)),
          onDrop: (e) => dropOnArtistCard(e, artist),
        }
      : {}

  // Синтетическая карточка «Любимые» не участвует в курировании: у неё нет
  // ни folder_id, ни artist_id, поэтому ни тащить, ни ронять на неё нельзя.
  const albumCardDnd = (album) =>
    isAdmin && !album.is_favorites
      ? {
          draggable: true,
          onDragStart: (e) => startAlbumDrag(e, album),
          onDragEnd: endDrag,
          onDragOver: (e) => overAlbumCard(e, album),
          onDragLeave: () => setDropKey((k) => (k === `album:${album.id}` ? null : k)),
          onDrop: (e) => dropOnAlbumCard(e, album),
        }
      : {}

  const isLeafCard = (artist) =>
    (artist?.children_count === 0 || !artist?.children_count) &&
    artist?.kind !== 'compilation' &&
    artist?.kind !== 'genre_collection' &&
    Boolean(artist?.parent_id)

  const handleArtistCardClick = async (artist) => {
    if (
      artist?.children_count > 0 ||
      artist?.kind === 'compilation' ||
      artist?.kind === 'genre_collection'
    ) {
      await openArtist(artist.id)
      return
    }

    if (artist?.folder_id && isLeafCard(artist)) {
      await openLeafCard(artist, selectedArtist)
      return
    }

    await openArtist(artist.id)
  }

  const openLeafCard = async (artist, parentArtist = null) => {
    if (playBusyId) return
    setPlayBusyId(artist.id)
    try {
      const tracks = await streamApi.getFolderTracks(artist.folder_id, false)
      if (!tracks || tracks.length === 0) {
        usePlayerStore.getState().pushNotice({ kind: 'info', text: 'В этой папке нет аудиофайлов.' })
        return
      }
      const parentName = (parentArtist && parentArtist.display_name) || artist.parent_name
      const artistName = parentName || artist.display_name
      const coverUrl = resolveCoverUrl(artist)
      const queueTracks = withReleaseMeta(tracks, {
        artist: artist.display_name || artistName,
        album: artist.display_name,
        coverUrl: resolveCoverUrl(artist, null),
      })
      prepareQueue(queueTracks, 0, {
        title: artist.display_name,
        artistName,
        coverUrl,
      })
      setPlayerExpanded(true)
    } catch (e) {
      console.error('Failed to load tracks for leaf card:', e)
      usePlayerStore.getState().pushNotice({ kind: 'warn', text: 'Не удалось загрузить файлы папки в плейлист.' })
    } finally {
      setPlayBusyId(null)
    }
  }

  const handlePlayLeafCard = async (artist, parentArtist = null) => {
    if (playBusyId) return
    setPlayBusyId(artist.id)
    try {
      const tracks = await streamApi.getFolderTracks(artist.folder_id, false)
      if (!tracks || tracks.length === 0) {
        usePlayerStore.getState().pushNotice({ kind: 'info', text: 'В этой папке нет аудиофайлов.' })
        return
      }
      const parentName = (parentArtist && parentArtist.display_name) || artist.parent_name
      const artistName = parentName || artist.display_name
      const coverUrl = resolveCoverUrl(artist)
      const queueTracks = withReleaseMeta(tracks, {
        artist: artist.display_name || artistName,
        album: artist.display_name,
        coverUrl: resolveCoverUrl(artist, null),
      })
      playQueue(queueTracks, 0, {
        title: artist.display_name,
        artistName,
        coverUrl,
      })
    } catch (e) {
      console.error('Failed to play tracks for leaf card:', e)
      usePlayerStore.getState().pushNotice({ kind: 'warn', text: 'Не удалось воспроизвести файлы папки.' })
    } finally {
      setPlayBusyId(null)
    }
  }

  const renderArtistCard = (artist) => {
    const cover = resolveCoverUrl(artist, null)
    const isDragging = dragItem?.type === 'artist' && dragItem.id === artist.id
    const isDropTarget = dropKey === `artist:${artist.id}`
    const isLeaf = isLeafCard(artist)
    const isNowPlaying =
      isLeaf &&
      !!currentTrack &&
      !!currentRelease &&
      currentRelease.title === artist.display_name

    const subParts = []
    if (artist.children_count > 0) {
      subParts.push(`${artist.children_count} ${artist.children_count === 1 ? 'папка' : artist.children_count < 5 ? 'папки' : 'папок'}`)
    } else if (artist.album_count > 0) {
      subParts.push(`${artist.album_count} ${artist.album_count === 1 ? 'релиз' : 'релизов'}`)
    } else if (isLeaf) {
      subParts.push('Треки')
    }
    if (artist.parent_id && artist.parent_name) {
      subParts.push(`↳ ${artist.parent_name}`)
    }

    return (
      <ClickableRow
        key={artist.id}
        onClick={() => handleArtistCardClick(artist)}
        className="tmusic-artist-card"
        {...artistCardDnd(artist)}
        style={{
          ...(isDropTarget
            ? { outline: '2px dashed var(--accent)', outlineOffset: '3px', borderRadius: '14px' }
            : {}),
          opacity: isDragging ? 0.45 : 1,
          cursor: isLeaf ? 'pointer' : isAdmin ? 'grab' : undefined,
        }}
      >
        <div className="tmusic-artist-card-avatar-wrap" style={{ position: 'relative' }}>
          <ImgWithFallback
            name={artist.display_name}
            kind="artist"
            src={cover}
            size="100%"
            className="tmusic-artist-card-avatar"
          />
          {isNowPlaying && (
            <span className={`tmusic-card-eq ${isPlaying ? '' : 'paused'}`} title="Сейчас играет">
              <i />
              <i />
              <i />
            </span>
          )}
          {isLeaf && (
            <button
              className="tmusic-card-play-btn"
              onClick={(e) => {
                e.stopPropagation()
                handlePlayLeafCard(artist, selectedArtist)
              }}
              disabled={playBusyId === artist.id}
              aria-busy={playBusyId === artist.id || undefined}
              aria-label={`Воспроизвести ${artist.display_name}`}
            >
              {playBusyId === artist.id ? (
                <Loader2 size={18} className="animate-spin" />
              ) : (
                <Play size={20} fill="var(--bg)" style={{ marginLeft: '2px' }} />
              )}
            </button>
          )}
          {isAdmin && artist.parent_id && (
            <button
              onClick={(e) => {
                e.stopPropagation()
                moveArtistInto(artist.id, null)
              }}
              title={`Извлечь из «${artist.parent_name || 'родительской карточки'}»`}
              aria-label={`Извлечь ${artist.display_name} из родителя`}
              style={{
                position: 'absolute',
                top: '6px',
                left: '6px',
                width: '26px',
                height: '26px',
                borderRadius: '50%',
                backgroundColor: 'rgba(20, 18, 16, 0.85)',
                border: '1px solid var(--line-bright)',
                color: 'var(--ink)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                cursor: 'pointer',
                padding: 0,
              }}
            >
              <Unlink size={13} />
            </button>
          )}
          {/* Admin cover curation straight from the grid — for artists and for
              collections alike. Otherwise the only route was the small overlay
              button on the artist page. */}
          {isAdmin && onOpenCoverPicker && !artist.is_cached_group && (
            <button
              onClick={(e) => {
                e.stopPropagation()
                onOpenCoverPicker({
                  type: 'artist',
                  id: artist.id,
                  displayName: artist.display_name,
                  hasCustom: !!artist.custom_image_url,
                })
              }}
              title="Изменить обложку (админ)"
              aria-label={`Изменить обложку ${artist.display_name}`}
              style={{
                position: 'absolute',
                top: '6px',
                right: '6px',
                width: '26px',
                height: '26px',
                borderRadius: '50%',
                backgroundColor: 'rgba(20, 18, 16, 0.85)',
                border: '1px solid var(--line-bright)',
                color: 'var(--ink)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                cursor: 'pointer',
                padding: 0,
              }}
            >
              <ImageIcon size={13} />
            </button>
          )}
        </div>
        <div className="tmusic-artist-card-name">{artist.display_name}</div>
        <div className="tmusic-artist-card-sub">
          {viewKind === 'all' && artist.kind !== 'artist' && `${CARD_KIND_LABEL[artist.kind] || artist.kind} · `}
            {subParts.join(' · ')}
          </div>
        </ClickableRow>
      )
    }

  const renderAlbumCard = (album, artist = null) => {
    const cover = resolveCoverUrl(album, null)
    const title = album.title || album.display_name
    const isDragging = dragItem?.type === 'album' && dragItem.id === album.id
    const isDropTarget = dropKey === `album:${album.id}`
    const isNowPlaying =
      !!currentTrack &&
      !!currentRelease &&
      currentRelease.title === title &&
      (!artist || currentRelease.artistName === artist.display_name)
    return (
      <ClickableRow
        key={album.id}
        onClick={() => openAlbum(album, artist)}
        className="tmusic-card"
        {...albumCardDnd(album)}
        style={{
          ...(isDropTarget
            ? { outline: '2px dashed var(--accent)', outlineOffset: '3px', borderRadius: '14px' }
            : {}),
          opacity: isDragging ? 0.45 : 1,
          cursor:
            album.is_favorites || album.is_cached_album ? 'pointer' : isAdmin ? 'grab' : undefined,
        }}
      >
        <div className="tmusic-card-cover-wrap">
          <ImgWithFallback
            name={title}
            kind="album"
            src={cover}
            size="100%"
            className="tmusic-card-img"
          />
          {isNowPlaying && (
            <span className={`tmusic-card-eq ${isPlaying ? '' : 'paused'}`} title="Сейчас играет">
              <i />
              <i />
              <i />
            </span>
          )}
          {/* Admin cover curation straight from the grid. It used to require
              opening the album and hunting a small overlay button, which made
              «сменить обложку альбома» effectively undiscoverable. */}
          {isAdmin && onOpenCoverPicker && !album.is_cached_album && (
            <button
              className="tmusic-card-cover-btn"
              onClick={(e) => {
                e.stopPropagation()
                onOpenCoverPicker({
                  type: 'album',
                  id: album.id,
                  title,
                  hasCustom: !!album.custom_image_url,
                })
              }}
              title="Изменить обложку (админ)"
              aria-label={`Изменить обложку альбома ${title}`}
            >
              <ImageIcon size={14} />
            </button>
          )}
          <button
            className="tmusic-card-play-btn"
            onClick={(e) => {
              e.stopPropagation()
              handlePlayAlbum(album, artist)
            }}
            disabled={playBusyId === album.id}
            aria-busy={playBusyId === album.id || undefined}
            aria-label={`Воспроизвести ${title}`}
          >
            {playBusyId === album.id ? (
              <Loader2 size={18} className="animate-spin" />
            ) : (
              <Play size={20} fill="var(--bg)" style={{ marginLeft: '2px' }} />
            )}
          </button>
        </div>
        <div className="tmusic-card-title">{title}</div>
        <div className="tmusic-card-sub">
          {album.is_favorites || album.is_cached_album
            ? `${album.track_count} ${tracksWord(album.track_count)}`
            : album.year || (album.album_count ? `${album.album_count} альб.` : 'Релиз')}
        </div>
      </ClickableRow>
    )
  }
  // ─── End card renderers ──────────────────────────────────────────────────

  const showCatalogStrip = mobileTab === 'library'

  return (
    <div style={{ paddingBottom: 'calc(var(--player-bar-h) + 24px + var(--safe-bottom))', minHeight: '100dvh', backgroundColor: 'var(--bg)' }}>
      {/* ─── Top Navbar ──────────────────────────────────────────────────────── */}
      <header
        className="tmusic-topbar"
        style={{
          position: 'sticky',
          // M-04: sit below the fixed offline banner while it is up.
          top: 'var(--offline-banner-h, 0px)',
          backgroundColor: 'rgba(20, 18, 16, 0.95)',
          backdropFilter: 'blur(10px)',
          borderBottom: '1px solid var(--line)',
          zIndex: 7000,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          {/* UI-43: the app wordmark is the single h1 — section labels below use h2. */}
          <h1
            className="title-zine"
            style={{
              margin: 0,
              fontSize: '20px',
              fontWeight: '900',
              letterSpacing: '0.04em',
              color: 'var(--ink)',
            }}
          >
            T<span style={{ color: 'var(--accent)' }}>-</span>MUSIC
          </h1>
          {stats && (
            <span
              className="tmusic-stats-badge"
              style={{
                fontSize: '11px',
                color: 'var(--dim)',
                padding: '2px 8px',
                borderRadius: '4px',
                backgroundColor: 'rgba(255,255,255,0.04)',
              }}
            >
              {stats.artists_count} артистов • {stats.albums_count} релизов • {stats.tracks_count} треков
            </span>
          )}
        </div>

        {/* Search Input */}
        <div
          className="tmusic-search-wrap"
          onClick={() => useSearchStore.getState().openSearch(search)}
          style={{ position: 'relative', cursor: 'pointer' }}
        >
          <Search
            size={15}
            style={{
              position: 'absolute',
              left: '12px',
              top: '50%',
              transform: 'translateY(-50%)',
              color: 'var(--dim)',
              pointerEvents: 'none',
            }}
          />
          <input
            type="text"
            readOnly
            placeholder="Поиск по трекам, альбомам, артистам... (Ctrl+K или /)"
            value={search}
            onClick={() => useSearchStore.getState().openSearch(search)}
            style={{
              width: '100%',
              padding: '10px 42px 10px 36px',
              minHeight: '40px',
              borderRadius: '999px',
              backgroundColor: 'rgba(255,255,255,0.04)',
              border: '1px solid var(--line)',
              color: 'var(--ink)',
              fontSize: '14px',
              cursor: 'pointer',
            }}
          />
          <span
            style={{
              position: 'absolute',
              right: '12px',
              top: '50%',
              transform: 'translateY(-50%)',
              fontSize: '11px',
              fontWeight: 700,
              padding: '2px 6px',
              borderRadius: '6px',
              backgroundColor: 'rgba(255, 255, 255, 0.08)',
              color: 'var(--dim)',
              pointerEvents: 'none',
            }}
          >
            /
          </span>
        </div>

        {/* Security / Account + view mode toggle (top-right, under SSL) */}
        <div className="tmusic-topbar-account">
          <div className="tmusic-topbar-account-row">
            {/* Quick Search Button on Mobile */}
            <button
              onClick={() => useSearchStore.getState().openSearch()}
              className="tmusic-mobile-search-trigger"
              aria-label="Поиск"
              title="Поиск"
            >
              <Search size={19} />
            </button>

            <div
              className="tmusic-ssl-badge"
              style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '12px', color: 'var(--accent)' }}
            >
              <ShieldCheck size={16} />
              <span style={{ fontWeight: '600' }}>SSL • SECURE</span>
            </div>
            {user ? (
              <ProfileMenu
                user={user}
                isAdmin={isAdmin}
                onLogout={logout}
                onLogin={() => setAuthModalOpen(true)}
                onOpenAdmin={() => setShowAdmin(true)}
                onProfileChanged={checkAuth}
                libSection={libSection}
                onSelectSection={(id) => {
                  if (id === 'playlists') {
                    // Отдельное окно с редактором плейлистов.
                    setLibSection(null)
                    setPlaylistsOpen(true)
                    return
                  }
                  setLibSection(id)
                  setMobileTab('library')
                  setSelectedArtist(null)
                }}
                onCreateCard={() => {
                  setViewMode('covers')
                  setMobileTab('library')
                  setSelectedArtist(null)
                  setShowCreate(true)
                }}
              />
            ) : (
              <button
                onClick={() => setAuthModalOpen(true)}
                style={{
                  padding: '4px 12px',
                  backgroundColor: 'var(--accent)',
                  color: 'var(--on-accent)',
                  borderRadius: '6px',
                  border: 'none',
                  fontWeight: '700',
                  fontSize: '12px',
                  cursor: 'pointer',
                }}
              >
                Войти
              </button>
            )}
          </div>

          {/* Covers ↔ Folders switch — centered in the page body below */}
        </div>
      </header>

      {/* ─── Main Content Body ────────────────────────────────────────────────── */}
      <main className="tmusic-main">
        {/* Admin is a full-page view from anywhere: search, artist, folder mode. */}
        {showAdmin ? (
          <AdminPanel onBack={() => setShowAdmin(false)} onOpenCoverPicker={onOpenCoverPicker} />
        ) : (
        <>
        {/* Centered catalog view switch (works in every tab, incl. search) */}
        <div className="tmusic-view-row">
          <div className="tmusic-view-toggle" role="group" aria-label="Вид каталога">
            <button
              type="button"
              onClick={() => setLibraryViewMode('covers')}
              className={`tmusic-view-toggle-btn ${!folderMode ? 'active' : ''}`}
              title="Каталог с обложками"
            >
              <LayoutGrid size={14} /> Обложки
            </button>
            <button
              type="button"
              onClick={() => setLibraryViewMode('folders')}
              className={`tmusic-view-toggle-btn ${folderMode ? 'active' : ''}`}
              title="Проводник Windows"
            >
              <FolderTree size={14} /> Папки
            </button>
          </div>
        </div>

        {/* Library panels (opened from the avatar menu): recent / playlists */}
        {!showAdmin && libSection && !selectedArtist && mobileTab !== 'search' && (
          <div className="tmusic-lib-section-wrap">
            <LibraryPanel section={libSection} />
          </div>
        )}

        {selectedArtist ? (
          /* ─── Artist Detail View ─────────────────────────────────────────── */
          <div>
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                flexWrap: 'wrap',
                gap: '12px',
                marginBottom: '20px',
              }}
            >
              <button
                type="button"
                onClick={() => {
                  if (selectedArtist.parent_id) {
                    openArtist(selectedArtist.parent_id)
                  } else {
                    setSelectedArtist(null)
                  }
                }}
                style={{
                  background: 'none',
                  border: 'none',
                  color: 'var(--accent)',
                  fontSize: '13.5px',
                  fontWeight: '700',
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '6px',
                  padding: 0,
                }}
              >
                <ArrowLeft size={16} />
                {selectedArtist.parent_name ? `Назад к «${selectedArtist.parent_name}»` : 'Назад к каталогу'}
              </button>

              {/* Breadcrumb trail */}
              <nav
                aria-label="Навигация по каталогу"
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '6px',
                  fontSize: '13px',
                  color: 'var(--dim)',
                  flexWrap: 'wrap',
                }}
              >
                <button
                  type="button"
                  onClick={() => setSelectedArtist(null)}
                  style={{
                    background: 'none',
                    border: 'none',
                    padding: 0,
                    color: 'var(--accent)',
                    cursor: 'pointer',
                    fontSize: '13px',
                    fontWeight: '600',
                  }}
                >
                  Каталог
                </button>
                {(selectedArtist.breadcrumbs || []).map((crumb) => (
                  <React.Fragment key={crumb.id}>
                    <ChevronRight size={13} style={{ opacity: 0.6 }} />
                    <button
                      type="button"
                      onClick={() => openArtist(crumb.id)}
                      style={{
                        background: 'none',
                        border: 'none',
                        padding: 0,
                        color: 'var(--accent)',
                        cursor: 'pointer',
                        fontSize: '13px',
                        fontWeight: '600',
                      }}
                    >
                      {crumb.display_name}
                    </button>
                  </React.Fragment>
                ))}
                <ChevronRight size={13} style={{ opacity: 0.6 }} />
                <span style={{ color: 'var(--ink)', fontWeight: '700' }}>{selectedArtist.display_name}</span>
              </nav>
            </div>

            {/* Artist Header Banner */}
            <div className="tmusic-artist-header">
              <div className="tmusic-artist-avatar-wrap">
                <ImgWithFallback
                  name={selectedArtist.display_name}
                  kind="artist"
                  src={resolveCoverUrl(selectedArtist, null)}
                  size={180}
                  className="tmusic-artist-avatar"
                  style={{
                    borderRadius: '50%',
                    border: '3px solid var(--line-bright)',
                    boxShadow: '0 10px 25px rgba(0,0,0,0.5)',
                  }}
                />
                <button
                  onClick={() =>
                    onOpenCoverPicker({
                      type: 'artist',
                      id: selectedArtist.id,
                      displayName: selectedArtist.display_name,
                      hasCustom: !!selectedArtist.custom_image_url,
                    })
                  }
                  title="Изменить фото (админ)"
                  style={{
                    position: 'absolute',
                    bottom: '6px',
                    right: '6px',
                    width: '36px',
                    height: '36px',
                    borderRadius: '50%',
                    backgroundColor: 'var(--bg-card)',
                    border: '1px solid var(--line-bright)',
                    color: 'var(--ink)',
                    // A cached artist is not a catalog row — there is nothing
                    // to write a custom cover back to.
                    display: isAdmin && !selectedArtist.is_cached_group ? 'flex' : 'none',
                    alignItems: 'center',
                    justifyContent: 'center',
                    cursor: 'pointer',
                  }}
                >
                  <ImageIcon size={16} />
                </button>
              </div>

              <div className="tmusic-artist-info">
                <div
                  style={{
                    fontSize: '12px',
                    textTransform: 'uppercase',
                    color: 'var(--accent)',
                    fontWeight: '700',
                    letterSpacing: '0.08em',
                  }}
                >
                  {(selectedArtist.kind || 'artist').toUpperCase()}
                </div>
                {renamingArtist ? (
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px', margin: '6px 0 12px 0', maxWidth: '520px' }}>
                    <input
                      value={artistNameDraft}
                      onChange={(e) => setArtistNameDraft(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') saveArtistName()
                        if (e.key === 'Escape') setRenamingArtist(false)
                      }}
                      autoFocus
                      aria-label="Новое имя артиста"
                      style={{
                        flex: 1,
                        minWidth: 0,
                        fontSize: '20px',
                        fontWeight: 800,
                        padding: '8px 10px',
                        borderRadius: '8px',
                        border: '1px solid var(--line-bright)',
                        background: 'var(--bg)',
                        color: 'var(--ink)',
                      }}
                    />
                    <button
                      type="button"
                      onClick={saveArtistName}
                      disabled={artistRenameBusy}
                      aria-label="Сохранить имя артиста"
                      style={{ ...artistHeaderBtn, opacity: artistRenameBusy ? 0.6 : 1 }}
                    >
                      {artistRenameBusy ? <Loader2 size={16} className="tmusic-spin" /> : <Check size={17} style={{ color: 'var(--accent)' }} />}
                    </button>
                    <button type="button" onClick={() => setRenamingArtist(false)} aria-label="Отмена" style={artistHeaderBtn}>
                      <X size={17} />
                    </button>
                  </div>
                ) : (
                  <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
                    <h2 className="title-zine" style={{ fontSize: '32px', margin: '6px 0 12px 0' }}>
                      {selectedArtist.display_name}
                    </h2>
                    {isAdmin && !selectedArtist.is_cached_group && (
                      <button
                        type="button"
                        onClick={() => {
                          setArtistNameDraft(selectedArtist.display_name || '')
                          setRenamingArtist(true)
                        }}
                        title="Переименовать артиста (админ)"
                        aria-label={`Переименовать ${selectedArtist.display_name}`}
                        style={{ ...artistHeaderBtn, marginBottom: '12px' }}
                      >
                        <Pencil size={15} />
                      </button>
                    )}
                  </div>
                )}
                {selectedArtist.bio && (
                  <p style={{ color: 'var(--dim)', fontSize: '13px', lineHeight: '1.6', marginBottom: '18px', maxWidth: '780px' }}>
                    {selectedArtist.bio}
                  </p>
                )}
                {(similarLoading || similarArtists.length > 0) && (
                  <div style={{ marginBottom: '18px', maxWidth: '780px' }}>
                    <div
                      style={{
                        fontSize: '11px',
                        textTransform: 'uppercase',
                        color: 'var(--dim)',
                        fontWeight: '700',
                        letterSpacing: '0.08em',
                        marginBottom: '8px',
                      }}
                    >
                      Похожие артисты {similarLoading ? '…' : ''}
                    </div>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
                      {similarArtists.map((name) => (
                        <button
                          key={name}
                          type="button"
                          onClick={() => openSimilarArtist(name)}
                          style={{
                            padding: '6px 12px',
                            borderRadius: '999px',
                            border: '1px solid var(--line-bright)',
                            background: 'transparent',
                            color: 'var(--ink)',
                            fontSize: '12px',
                            fontWeight: 600,
                            cursor: 'pointer',
                          }}
                          title="Найти в каталоге"
                        >
                          {name}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
                {(!selectedArtist.children || selectedArtist.children.length === 0) &&
                  selectedArtist.kind !== 'compilation' &&
                  selectedArtist.kind !== 'genre_collection' && (
                  <div className="tmusic-artist-actions">
                    <button
                      onClick={() => handlePlayAllArtist(selectedArtist)}
                      style={{
                        padding: '10px 20px',
                        backgroundColor: 'var(--accent)',
                        color: 'var(--on-accent)',
                        border: 'none',
                        borderRadius: '999px',
                        fontWeight: '800',
                        fontSize: '13px',
                        display: 'flex',
                        alignItems: 'center',
                        gap: '8px',
                        cursor: 'pointer',
                      }}
                    >
                      <Play size={16} fill="currentColor" /> Слушать всё
                    </button>
                  </div>
                )}
              </div>
            </div>

            {/* Nested cards (artists/collections dropped inside this one) */}
            {(selectedArtist.children || []).length > 0 && (
              <div style={{ marginBottom: (selectedArtist.albums || []).length > 0 ? '28px' : '0' }}>
                <h3 className="title-zine" style={{ fontSize: '18px', margin: '20px 0 16px' }}>
                  {selectedArtist.kind === 'compilation' ? 'ИСПОЛНИТЕЛИ И ВЛОЖЕНИЯ' : 'ВНУТРИ'} ({selectedArtist.children.length})
                </h3>
                <div className="tmusic-grid-artists">
                  {selectedArtist.children.map((child) => renderArtistCard(child))}
                </div>
              </div>
            )}

            {/* Releases Grid — only shown when artist has releases */}
            {(selectedArtist.albums || []).length > 0 && (
              <>
                <h3 className="title-zine" style={{ fontSize: '18px', marginBottom: '16px', marginTop: '16px' }}>
                  РЕЛИЗЫ И АЛЬБОМЫ ({(selectedArtist.albums || []).length})
                </h3>
                <div className="tmusic-grid-albums">
                  {(selectedArtist.albums || []).map((album) => renderAlbumCard(album, selectedArtist))}
                </div>
              </>
            )}
          </div>
        ) : mobileTab === 'profile' ? (
          /* ─── Mobile Profile Screen ───────────────────────────────────────── */
          <div className="tmusic-profile-screen">
            <div className="tmusic-profile-header">
              <div className="tmusic-profile-avatar" style={{ overflow: 'hidden', padding: 0 }}>
                {user?.avatar_url ? (
                  <img
                    src={user.avatar_url}
                    alt={user.username}
                    style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
                  />
                ) : (
                  <span>{(user?.username || 'A').toUpperCase().charAt(0)}</span>
                )}
              </div>
              <div className="tmusic-profile-username">{user?.username || 'admin'}</div>
              <div className="tmusic-profile-badge">
                <ShieldCheck size={14} /> {roleLabel(user)} • SSL Защищено
              </div>
            </div>

            {stats && (
              <div className="tmusic-profile-stats">
                <div className="tmusic-profile-stat-card">
                  <div className="stat-val">{stats.artists_count}</div>
                  <div className="stat-lbl">Артистов</div>
                </div>
                <div className="tmusic-profile-stat-card">
                  <div className="stat-val">{stats.albums_count}</div>
                  <div className="stat-lbl">Релизов</div>
                </div>
                <div className="tmusic-profile-stat-card">
                  <div className="stat-val">{stats.tracks_count}</div>
                  <div className="stat-lbl">Треков</div>
                </div>
              </div>
            )}

            <div className="tmusic-profile-actions">
              {(user?.role === 'admin' || user?.username === 'admin') && (
                <button
                  onClick={() => setShowAdmin(true)}
                  className="tmusic-profile-btn tmusic-profile-btn-primary"
                >
                  <ShieldCheck size={18} /> Админка
                </button>
              )}
              {user ? (
                <button
                  onClick={logout}
                  className="tmusic-profile-btn tmusic-profile-btn-danger"
                >
                  <LogOut size={18} /> Выйти из аккаунта
                </button>
              ) : (
                <button
                  onClick={() => setAuthModalOpen(true)}
                  className="tmusic-profile-btn tmusic-profile-btn-primary"
                >
                  Войти в T-Music
                </button>
              )}
            </div>
          </div>
        ) : mobileTab === 'search' ? (
          /* ─── Mobile Search Screen ─────────────────────────────────────────── */
          <div className="tmusic-search-screen">
            <div
              className="tmusic-search-screen-bar"
              onClick={() => useSearchStore.getState().openSearch()}
              style={{ cursor: 'pointer' }}
            >
              <Search size={18} className="tmusic-search-screen-icon" />
              <input
                type="text"
                readOnly
                placeholder="Поиск по всей медиатеке... (треки, альбомы, артисты)"
                value={search}
                onClick={() => useSearchStore.getState().openSearch()}
                className="tmusic-search-screen-input"
                style={{ cursor: 'pointer' }}
              />
              {search && (
                <button onClick={clearSearch} className="tmusic-search-screen-clear" aria-label="Очистить поиск">
                  <X size={18} />
                </button>
              )}
            </div>

            {!search ? (
              <div className="tmusic-search-suggestions">
                <div className="tmusic-search-subheading">Разделы каталога</div>
                <div className="tmusic-search-pills">
                  {SECTIONS.map((sec) => (
                    <button
                      key={sec.id}
                      onClick={() => {
                        setActiveKind(sec.id)
                        goToTab('library')
                      }}
                      className="tmusic-search-pill"
                    >
                      <sec.icon size={15} />
                      <span>{sec.label}</span>
                    </button>
                  ))}
                </div>
              </div>
            ) : isLoading ? (
              <div
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'center',
                  justifyContent: 'center',
                  padding: '60px 20px',
                  gap: '16px',
                  color: 'var(--dim)',
                }}
              >
                <Loader2 size={32} className="animate-spin" style={{ color: 'var(--accent)' }} />
                <div style={{ fontSize: '13px', fontWeight: '600' }}>Поиск...</div>
              </div>
            ) : items.length === 0 ? (
              <div
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'center',
                  justifyContent: 'center',
                  padding: '60px 20px',
                  gap: '12px',
                  color: 'var(--dim)',
                }}
              >
                <Music size={40} style={{ opacity: 0.3 }} />
                <div style={{ fontSize: '15px', fontWeight: '700', color: 'var(--ink)' }}>Ничего не найдено</div>
                <div style={{ fontSize: '13px' }}>Попробуйте изменить запрос</div>
              </div>
             ) : folderMode ? (
               <FolderBrowser />
             ) : items.length === 0 ? (
               <div
                 style={{
                   display: 'flex',
                   flexDirection: 'column',
                   alignItems: 'center',
                   justifyContent: 'center',
                   padding: '60px 20px',
                   gap: '12px',
                   color: 'var(--dim)',
                 }}
               >
                 <Music size={40} style={{ opacity: 0.3 }} />
                 <div style={{ fontSize: '15px', fontWeight: '700', color: 'var(--ink)' }}>Ничего не найдено</div>
                 <div style={{ fontSize: '13px' }}>Попробуйте изменить запрос</div>
               </div>
              ) : isAlbumSection ? (
                <>
                  <div className="tmusic-grid-albums">
                    {items.map((album) => renderAlbumCard(album))}
                  </div>
                  {renderLoadMore()}
                </>
              ) : (
                <>
                  <div className="tmusic-grid-artists">
                    {items.map((artist) => renderArtistCard(artist))}
                  </div>
                  {renderLoadMore()}
                </>
              )}
             </div>
           ) : (
            /* ─── Catalog: Медиатека (category strip) / Артисты / Альбомы ──────── */
            <div>
              {/* UI-05: failed artist detail — never silently drop into an empty grid */}
              {detailError && (
                <div
                  role="alert"
                  style={{
                    display: 'flex',
                    flexDirection: 'column',
                    alignItems: 'center',
                    justifyContent: 'center',
                    padding: '60px 20px',
                    gap: '12px',
                    color: 'var(--danger)',
                    textAlign: 'center',
                  }}
                >
                  <AlertTriangle size={36} />
                  <div style={{ fontSize: '15px', fontWeight: '700', color: 'var(--ink)' }}>
                    Не удалось загрузить артиста
                  </div>
                  <div style={{ fontSize: '13px', maxWidth: 320 }}>{detailError.message}</div>
                  <button
                    type="button"
                    onClick={() => openArtist(detailError.id)}
                    style={{
                      marginTop: '6px',
                      padding: '8px 18px',
                      borderRadius: 8,
                      border: '1px solid var(--line-bright)',
                      background: 'var(--accent)',
                      color: 'var(--bg)',
                      fontWeight: 700,
                      cursor: 'pointer',
                    }}
                  >
                    Повторить
                  </button>
                </div>
              )}
              {/* Admin: create catalog cards (opened from the avatar menu) */}
            {isAdmin && !folderMode && (
              <div style={{ marginBottom: 14 }}>
                {showCreate ? (
                  <div
                    style={{
                      background: 'var(--bg-card)',
                      border: '1px solid var(--line)',
                      borderRadius: 12,
                      padding: '14px 16px',
                    }}
                  >
                    <div style={{ fontWeight: 800, fontSize: 13, marginBottom: 10, display: 'flex', alignItems: 'center', gap: 6 }}>
                      <Plus size={15} style={{ color: 'var(--accent)' }} /> Новая карточка каталога
                    </div>
                    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'flex-end' }}>
                      <input
                        value={newCard.name}
                        onChange={(e) => setNewCard((c) => ({ ...c, name: e.target.value }))}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') createCard()
                          if (e.key === 'Escape') setShowCreate(false)
                        }}
                        placeholder="Название"
                        aria-label="Название новой карточки"
                        autoFocus
                        style={{
                          background: 'var(--bg)',
                          border: '1px solid var(--line)',
                          color: 'var(--ink)',
                          borderRadius: 8,
                          padding: '8px 10px',
                          minHeight: 40,
                          fontSize: 14,
                          flex: 1,
                          minWidth: 160,
                        }}
                      />
                      <select
                        value={newCard.kind}
                        onChange={(e) => setNewCard((c) => ({ ...c, kind: e.target.value }))}
                        title="Тип карточки"
                        aria-label="Тип новой карточки"
                        style={{
                          background: 'var(--bg)',
                          border: '1px solid var(--line)',
                          color: 'var(--ink)',
                          borderRadius: 8,
                          padding: '8px 10px',
                          minHeight: 40,
                          fontSize: 14,
                        }}
                      >
                        {CARD_KINDS.map((k) => (
                          <option key={k.id} value={k.id}>
                            {k.label}
                          </option>
                        ))}
                      </select>
                      <textarea
                        value={newCard.bio}
                        onChange={(e) => setNewCard((c) => ({ ...c, bio: e.target.value }))}
                        placeholder="Описание (необязательно)"
                        aria-label="Описание новой карточки"
                        rows={1}
                        style={{
                          background: 'var(--bg)',
                          border: '1px solid var(--line)',
                          color: 'var(--ink)',
                          borderRadius: 8,
                          padding: '8px 10px',
                          fontSize: 14,
                          flex: 2,
                          minWidth: 160,
                          minHeight: 40,
                          resize: 'vertical',
                        }}
                      />
                      <button
                        type="button"
                        disabled={creating || !newCard.name.trim()}
                        onClick={createCard}
                        style={{
                          background: 'var(--accent)',
                          color: 'var(--on-accent)',
                          border: 'none',
                          borderRadius: 8,
                          padding: '8px 14px',
                          minHeight: 40,
                          fontWeight: 800,
                          fontSize: 12,
                          cursor: creating || !newCard.name.trim() ? 'default' : 'pointer',
                          opacity: creating || !newCard.name.trim() ? 0.5 : 1,
                          display: 'inline-flex',
                          alignItems: 'center',
                          gap: 6,
                        }}
                      >
                        {creating ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />} Создать
                      </button>
                      <button
                        type="button"
                        onClick={() => setShowCreate(false)}
                        style={{
                          background: 'transparent',
                          border: '1px solid var(--line)',
                          color: 'var(--dim)',
                          borderRadius: 8,
                          minWidth: 36,
                          minHeight: 40,
                          cursor: 'pointer',
                          display: 'inline-flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                        }}
                        aria-label="Отмена"
                      >
                        <X size={14} />
                      </button>
                    </div>
                     <div style={{ fontSize: 11.5, color: 'var(--dim)', marginTop: 8 }}>
                       После создания перетаскивайте карточки артистов, сборники и релизы прямо на эту карточку в сетке.
                     </div>
                   </div>
                 ) : null}
               </div>
             )}

            {folderMode ? (
              <FolderBrowser />
            ) : showCatalogStrip ? (
              <div className="tmusic-tabs-nav">
                {SECTIONS.map((tab) => {
                  const Icon = tab.icon
                  const isActive = activeKind === tab.id
                  return (
                    <button
                      key={tab.id}
                      onClick={() => {
                        setActiveKind(tab.id)
                        setSearch('')
                      }}
                      className={`tmusic-tab-btn ${isActive ? 'active' : ''}`}
                    >
                      <Icon size={15} /> {tab.label}
                    </button>
                  )
                })}
              </div>
            ) : (
              <h2 className="title-zine" style={{ fontSize: '19px', marginBottom: '16px' }}>
                {currentSection.label}
              </h2>
            )}

            {/* Catalog grid — always under bar/panel in covers mode */}
            {!folderMode && (!isAuthenticated ? (
              /* UI-24: not signed in — offer a login CTA, never a bare empty grid */
              <div
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'center',
                  justifyContent: 'center',
                  padding: '80px 20px',
                  gap: '14px',
                  color: 'var(--dim)',
                  textAlign: 'center',
                }}
              >
                <ShieldCheck size={42} style={{ opacity: 0.4 }} />
                <div style={{ fontSize: '16px', fontWeight: '700', color: 'var(--ink)' }}>Войдите, чтобы слушать</div>
                <div style={{ fontSize: '13px', maxWidth: 320 }}>
                  Медиатека доступна после входа в аккаунт.
                </div>
                <button
                  type="button"
                  onClick={() => setAuthModalOpen(true)}
                  style={{
                    marginTop: '6px',
                    padding: '10px 22px',
                    borderRadius: 8,
                    border: '1px solid var(--line-bright)',
                    background: 'var(--accent)',
                    color: 'var(--bg)',
                    fontWeight: 700,
                    cursor: 'pointer',
                  }}
                >
                  Войти
                </button>
              </div>
            ) : isLoading ? (
              <div
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'center',
                  justifyContent: 'center',
                  padding: '80px 20px',
                  gap: '16px',
                  color: 'var(--dim)',
                }}
              >
                <Loader2 size={36} className="animate-spin" style={{ color: 'var(--accent)' }} />
                <div style={{ fontSize: '14px', fontWeight: '600' }}>Загрузка каталога...</div>
              </div>
            ) : loadError ? (
              /* UI-05: a fetch failure is an alert with retry, not an empty result */
              <div
                role="alert"
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'center',
                  justifyContent: 'center',
                  padding: '80px 20px',
                  gap: '12px',
                  color: 'var(--danger)',
                  textAlign: 'center',
                }}
              >
                <AlertTriangle size={40} />
                <div style={{ fontSize: '16px', fontWeight: '700', color: 'var(--ink)' }}>Не удалось загрузить каталог</div>
                <div style={{ fontSize: '13px', maxWidth: 320 }}>{loadError}</div>
                <button
                  type="button"
                  onClick={() => loadData()}
                  style={{
                    marginTop: '6px',
                    padding: '9px 20px',
                    borderRadius: 8,
                    border: '1px solid var(--line-bright)',
                    background: 'var(--accent)',
                    color: 'var(--bg)',
                    fontWeight: 700,
                    cursor: 'pointer',
                  }}
                >
                  Повторить
                </button>
              </div>
            ) : items.length === 0 ? (
              /* Empty State */
              <div
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'center',
                  justifyContent: 'center',
                  padding: '80px 20px',
                  gap: '12px',
                  color: 'var(--dim)',
                }}
              >
                <Music size={42} style={{ opacity: 0.3 }} />
                <div style={{ fontSize: '16px', fontWeight: '700', color: 'var(--ink)' }}>Ничего не найдено</div>
                <div style={{ fontSize: '13px' }}>Попробуйте изменить поисковый запрос или выбрать другую категорию.</div>
              </div>
            ) : isAlbumSection ? (
              /* ─── Albums / Compilations Grid (Square Cards) ─────────────── */
              <>
                <div className="tmusic-grid-albums">
                  {items.map((album) => renderAlbumCard(album))}
                </div>
                {renderLoadMore()}
              </>
            ) : (
              /* ─── Artists Grid (Circular Avatar Cards) ──────────────────── */
              <>
                <div className="tmusic-grid-artists">
                  {items.map((artist) => renderArtistCard(artist))}
                </div>
                {renderLoadMore()}
              </>
            ))}
          </div>
        )}
        </>
        )}
      </main>

      {/* Album tracklist sheet (opened by tapping an album cover) */}
      {selectedAlbum && (
        <AlbumDetail
          album={selectedAlbum.album}
          artist={selectedAlbum.artist}
          cachedTracks={selectedAlbum.album.is_cached_album ? selectedAlbum.album.cached_tracks : null}
          onClose={() => setSelectedAlbum(null)}
          onOpenCoverPicker={isAdmin && !selectedAlbum.album.is_cached_album ? onOpenCoverPicker : null}
        />
      )}

      {/* Unified Search Modal (Desktop / Mobile overlay) */}
      <UnifiedSearchModal
        onOpenArtist={openArtist}
        onOpenAlbum={(alb) =>
          setSelectedAlbum({
            album: alb,
            artist: { display_name: alb.artist_name, id: alb.artist_id },
          })
        }
      />

      {/* Playlists editor window (opened from the avatar menu → «Плейлисты») */}
      <PlaylistsModal open={playlistsOpen} onClose={() => setPlaylistsOpen(false)} />

      {/* Admin curation error toast (drag-and-drop / create failures) */}
      {actionError && (
        <div
          style={{
            position: 'fixed',
            bottom: 'calc(var(--player-bar-h) + 20px + var(--safe-bottom))',
            left: '50%',
            transform: 'translateX(-50%)',
            zIndex: 8000,
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            padding: '10px 14px',
            borderRadius: 10,
            fontSize: 12.5,
            fontWeight: 600,
            background: 'rgba(255,107,107,0.14)',
            color: 'var(--danger, #ff6b6b)',
            border: '1px solid var(--line)',
            backdropFilter: 'blur(8px)',
            maxWidth: '92vw',
          }}
          role="alert"
        >
          <AlertTriangle size={15} style={{ flexShrink: 0 }} />
          <span>{actionError}</span>
          <button
            onClick={() => setActionError(null)}
            style={{ background: 'none', border: 'none', color: 'inherit', cursor: 'pointer', padding: 0, display: 'flex' }}
            aria-label="Закрыть"
          >
            <X size={14} />
          </button>
        </div>
      )}

      {/* ─── Mobile Bottom Navigation Bar ──────────────────────────────────── */}
      <nav className="tmusic-bottom-nav">
        <button
          className={`tmusic-nav-item ${mobileTab === 'library' && !selectedArtist ? 'active' : ''}`}
          onClick={() => goToTab('library')}
          aria-label="Медиатека"
        >
          <Home size={20} />
          <span>Медиатека</span>
        </button>
        <button
          className={`tmusic-nav-item ${mobileTab === 'artists' && !selectedArtist ? 'active' : ''}`}
          onClick={() => goToTab('artists')}
          aria-label="Артисты"
        >
          <Users size={20} />
          <span>Артисты</span>
        </button>
        <button
          className={`tmusic-nav-item ${mobileTab === 'albums' && !selectedArtist ? 'active' : ''}`}
          onClick={() => goToTab('albums')}
          aria-label="Альбомы"
        >
          <Disc size={20} />
          <span>Альбомы</span>
        </button>
        <button
          className={`tmusic-nav-item ${mobileTab === 'search' ? 'active' : ''}`}
          onClick={() => goToTab('search')}
          aria-label="Поиск"
        >
          <Search size={20} />
          <span>Поиск</span>
        </button>
        <button
          className={`tmusic-nav-item ${mobileTab === 'profile' ? 'active' : ''}`}
          onClick={() => goToTab('profile')}
          aria-label="Профиль"
        >
          <User size={20} />
          <span>Профиль</span>
        </button>
      </nav>
    </div>
  )
}
