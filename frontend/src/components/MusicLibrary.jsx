import React, { useState, useEffect, useRef } from 'react'
import { libraryApi, bustCatalogCache } from '../api/library'
import { streamApi } from '../api/stream'
import { adminApi } from '../api/admin'
import { metadataApi } from '../api/metadata'
import { missingCoverKeys, probedCoverKeys, markCoverProbed, parseCoverKey } from '../services/coverMissing'
import { resolveCoverUrl } from '../utils/cover'
import { AlbumDetail } from './AlbumDetail'
import { LibraryPanel } from './HomeExtraPanels'
import { FolderBrowser } from './FolderBrowser'
import { AdminPanel } from './AdminPanel'
import { ProfileMenu, roleLabel } from './ProfileMenu'
import { usePlayerStore } from '../store/playerStore'
import { useAuthStore } from '../store/authStore'
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
} from 'lucide-react'

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

const SECTIONS = [
  { id: 'artist', label: 'Исполнители', icon: Users, isAlbum: false },
  { id: 'albums', label: 'Альбомы', icon: Disc, isAlbum: true },
  { id: 'compilation', label: 'Сборники', icon: Layers, isAlbum: true },
  { id: 'standalone_album', label: 'Отдельные релизы', icon: Disc, isAlbum: true },
  { id: 'genre_collection', label: 'Коллекции', icon: FolderHeart, isAlbum: true },
  { id: 'all', label: 'Все категории', icon: Layers, isAlbum: false },
]

const SECTIONS_BY_ID = Object.fromEntries(SECTIONS.map((s) => [s.id, s]))

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

export function MusicLibrary({ onOpenCoverPicker }) {
  const [items, setItems] = useState([])
  const [selectedArtist, setSelectedArtist] = useState(null)
  const [similarArtists, setSimilarArtists] = useState([])
  const [similarLoading, setSimilarLoading] = useState(false)
  const [selectedAlbum, setSelectedAlbum] = useState(null) // { album, artist } — AlbumDetail sheet
  const [activeKind, setActiveKind] = useState('artist')
  const [mobileTab, setMobileTab] = useState('library') // 'library' | 'artists' | 'albums' | 'search' | 'profile'
  const [search, setSearch] = useState('')
  const [isLoading, setIsLoading] = useState(true)
  const [stats, setStats] = useState(null)
  // 'covers' | 'folders' — Windows-style VFS hierarchy vs cover catalog
  const [viewMode, setViewMode] = useState(() => {
    try {
      return localStorage.getItem('tmusic_view_mode') === 'folders' ? 'folders' : 'covers'
    } catch {
      return 'covers'
    }
  })
  // Top bar section: catalog is default; recent/playlists/favorites open panels
  const [libSection, setLibSection] = useState(null) // null | 'recent' | 'playlists' | 'favorites'
  const [showAdmin, setShowAdmin] = useState(false)
  // Bumped when the cover manifest arrives so the grid re-renders with fresh
  // missing-cover knowledge (the registry itself is not React state).
  const [, setManifestTick] = useState(0)
  // Monotonic request id: a slow earlier fetch must not overwrite a newer one
  // (fast typing fires parallel loadData calls that resolve out of order).
  const loadSeq = useRef(0)

  const { playQueue, prepareQueue, setPlayerExpanded, currentTrack, currentRelease, isPlaying } = usePlayerStore()
  const { user, isAuthenticated, logout, setAuthModalOpen, checkAuth } = useAuthStore()

  // Catalog curation (rename / covers / collections) is admin-only.
  const isAdmin = user?.role === 'admin' || user?.username === 'admin'

  // Which catalog section is on screen: the Артисты/Альбомы tabs are fixed views,
  // everything else follows the category strip picked under Медиатека.
  const viewKind = MOBILE_TAB_KIND[mobileTab] || activeKind
  const currentSection = SECTIONS_BY_ID[viewKind] || SECTIONS[0]
  const isAlbumSection = currentSection.isAlbum
  const folderMode = viewMode === 'folders'

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

  useEffect(() => {
    if (isAuthenticated) {
      loadData()
    } else {
      setIsLoading(false)
    }
  }, [viewKind, debouncedSearch, isAuthenticated])

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
        const missing = []
        for (const c of [0, 1]) {
          if (byCustom[c].length === 0) continue
          const res = await libraryApi.coversManifest(byCustom[c], c)
          if (res && Array.isArray(res.missing)) {
            for (const k of res.missing) missing.push(`${c}:${k}`)
          }
        }
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
    const mySeq = ++loadSeq.current
    setIsLoading(true)
    try {
      // Never send search=null — backend treats it as a literal filter.
      const searchVal = (debouncedSearch || search || '').trim()
      if (viewKind === 'albums') {
        const data = await libraryApi.getAlbums({
          ...(searchVal ? { search: searchVal } : {}),
          limit: 100,
        })
        if (mySeq === loadSeq.current) setItems(data || [])
      } else {
        const data = await libraryApi.getArtists({
          ...(viewKind === 'all' ? {} : { kind: viewKind }),
          ...(searchVal ? { search: searchVal } : {}),
          limit: 100,
        })
        if (mySeq === loadSeq.current) setItems(data || [])
      }
    } catch (e) {
      console.error('Failed to load library catalog:', e)
      if (mySeq === loadSeq.current) setItems([])
    } finally {
      if (mySeq === loadSeq.current) setIsLoading(false)
    }
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
    setMobileTab(tab)
    setSelectedArtist(null)
    if (MOBILE_TAB_KIND[tab]) setSearch('')
    window.scrollTo(0, 0)
  }

  const openArtist = async (artistId) => {
    setIsLoading(true)
    try {
      const detail = await libraryApi.getArtistDetail(artistId)
      setSelectedArtist(detail)
    } catch (e) {
      console.error('Failed to load artist detail:', e)
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
    try {
      const tracks = await streamApi.getFolderTracks(album.folder_id, !album.is_tracks)
      if (tracks && tracks.length > 0) {
        const artistName = (artist && artist.display_name) || album.title || 'Альбом'
        const coverUrl = resolveCoverUrl(album, artist)

        playQueue(tracks, 0, {
          title: album.title,
          artistName,
          coverUrl,
        })
      }
    } catch (e) {
      console.error('Failed to load tracks for album:', e)
    }
  }

  // Cover tap → open the fullscreen player with the album loaded but NOT
  // playing (Spotify-style: the bar's play or the queue's item starts audio).
  const openAlbum = async (album, artist = null) => {
    try {
      const tracks = await streamApi.getFolderTracks(album.folder_id, !album.is_tracks)
      if (!tracks || tracks.length === 0) return
      const artistName = (artist && artist.display_name) || album.title || 'Альбом'
      const coverUrl = resolveCoverUrl(album, artist)
      prepareQueue(tracks, 0, { title: album.title, artistName, coverUrl })
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
    try {
      const tracks = await streamApi.getFolderTracks(artist.folder_id, true)
      if (tracks && tracks.length > 0) {
        playQueue(tracks, 0, {
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

  const albumCardDnd = (album) =>
    isAdmin
      ? {
          draggable: true,
          onDragStart: (e) => startAlbumDrag(e, album),
          onDragEnd: endDrag,
          onDragOver: (e) => overAlbumCard(e, album),
          onDragLeave: () => setDropKey((k) => (k === `album:${album.id}` ? null : k)),
          onDrop: (e) => dropOnAlbumCard(e, album),
        }
      : {}

  const renderArtistCard = (artist) => {
    const cover = resolveCoverUrl(artist, null, null)
    const isDragging = dragItem?.type === 'artist' && dragItem.id === artist.id
    const isDropTarget = dropKey === `artist:${artist.id}`
    const subParts = [
      `${artist.album_count} ${artist.album_count === 1 ? 'релиз' : 'релизов'}`,
      ...(artist.parent_id ? [`↳ ${artist.parent_name || 'вложено'}`] : []),
      ...(artist.children_count > 0 ? [`+${artist.children_count} влож.`] : []),
    ]
    return (
      <div
        key={artist.id}
        onClick={() => openArtist(artist.id)}
        className="tmusic-artist-card"
        {...artistCardDnd(artist)}
        style={{
          ...(isDropTarget
            ? { outline: '2px dashed var(--accent)', outlineOffset: '3px', borderRadius: '14px' }
            : {}),
          opacity: isDragging ? 0.45 : 1,
          cursor: isAdmin ? 'grab' : undefined,
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
        </div>
        <div className="tmusic-artist-card-name">{artist.display_name}</div>
        <div className="tmusic-artist-card-sub">
          {viewKind === 'all' && artist.kind !== 'artist' && `${CARD_KIND_LABEL[artist.kind] || artist.kind} · `}
          {subParts.join(' · ')}
        </div>
      </div>
    )
  }

  const renderAlbumCard = (album, artist = null) => {
    const cover = resolveCoverUrl(album, artist, null)
    const title = album.title || album.display_name
    const isDragging = dragItem?.type === 'album' && dragItem.id === album.id
    const isDropTarget = dropKey === `album:${album.id}`
    const isNowPlaying =
      !!currentTrack &&
      !!currentRelease &&
      currentRelease.title === title &&
      (!artist || currentRelease.artistName === artist.display_name)
    return (
      <div
        key={album.id}
        onClick={() => openAlbum(album, artist)}
        className="tmusic-card"
        {...albumCardDnd(album)}
        style={{
          ...(isDropTarget
            ? { outline: '2px dashed var(--accent)', outlineOffset: '3px', borderRadius: '14px' }
            : {}),
          opacity: isDragging ? 0.45 : 1,
          cursor: isAdmin ? 'grab' : undefined,
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
          <button
            className="tmusic-card-play-btn"
            onClick={(e) => {
              e.stopPropagation()
              handlePlayAlbum(album, artist)
            }}
            aria-label={`Воспроизвести ${title}`}
          >
            <Play size={20} fill="#141210" style={{ marginLeft: '2px' }} />
          </button>
        </div>
        <div className="tmusic-card-title">{title}</div>
        <div className="tmusic-card-sub">
          {album.year || (album.album_count ? `${album.album_count} альб.` : 'Релиз')}
        </div>
      </div>
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
          top: 0,
          backgroundColor: 'rgba(20, 18, 16, 0.95)',
          backdropFilter: 'blur(10px)',
          borderBottom: '1px solid var(--line)',
          zIndex: 7000,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <div
            className="title-zine"
            style={{
              fontSize: '20px',
              fontWeight: '900',
              letterSpacing: '0.04em',
              color: 'var(--ink)',
            }}
          >
            T<span style={{ color: 'var(--accent)' }}>-</span>MUSIC
          </div>
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
        <div className="tmusic-search-wrap">
          <Search
            size={15}
            style={{
              position: 'absolute',
              left: '12px',
              top: '50%',
              transform: 'translateY(-50%)',
              color: 'var(--dim)',
            }}
          />
          <input
            type="text"
            placeholder={isAlbumSection ? 'Поиск по альбомам и релизам...' : 'Поиск по артистам...'}
            value={search}
            onChange={handleSearchChange}
            style={{
              width: '100%',
              padding: '10px 36px 10px 36px',
              minHeight: '40px',
              borderRadius: '999px',
              backgroundColor: 'rgba(255,255,255,0.04)',
              border: '1px solid var(--line)',
              color: 'var(--ink)',
              fontSize: '14px',
              outline: 'none',
            }}
          />
          {search && (
            <button
              onClick={clearSearch}
              style={{
                position: 'absolute',
                right: '12px',
                top: '50%',
                transform: 'translateY(-50%)',
                background: 'none',
                border: 'none',
                color: 'var(--dim)',
                cursor: 'pointer',
                padding: 0,
                display: 'flex',
              }}
            >
              <X size={14} />
            </button>
          )}
        </div>

        {/* Security / Account + view mode toggle (top-right, under SSL) */}
        <div className="tmusic-topbar-account">
          <div className="tmusic-topbar-account-row">
            {/* Quick Search Button on Mobile */}
            <button
              onClick={() => setMobileTab('search')}
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
                  color: '#141210',
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

        {/* Library panels (opened from the avatar menu): recent / playlists / favorites */}
        {!showAdmin && libSection && !selectedArtist && mobileTab !== 'search' && (
          <div className="tmusic-lib-section-wrap">
            <LibraryPanel section={libSection} />
          </div>
        )}

        {selectedArtist ? (
          /* ─── Artist Detail View ─────────────────────────────────────────── */
          <div>
            <button
              onClick={() => setSelectedArtist(null)}
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
                marginBottom: '20px',
              }}
            >
              <ArrowLeft size={16} /> Назад к каталогу
            </button>

            {/* Artist Header Banner */}
            <div className="tmusic-artist-header">
              <div className="tmusic-artist-avatar-wrap">
                <ImgWithFallback
                  name={selectedArtist.display_name}
                  kind="artist"
                  src={resolveCoverUrl(selectedArtist, null, null)}
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
                    display: isAdmin ? 'flex' : 'none',
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
                <h1 className="title-zine" style={{ fontSize: '32px', margin: '6px 0 12px 0' }}>
                  {selectedArtist.display_name}
                </h1>
                {selectedArtist.bio && (
                  <p style={{ color: '#b5ada2', fontSize: '13px', lineHeight: '1.6', marginBottom: '18px', maxWidth: '780px' }}>
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
                <div className="tmusic-artist-actions">
                  <button
                    onClick={() => handlePlayAllArtist(selectedArtist)}
                    style={{
                      padding: '10px 20px',
                      backgroundColor: 'var(--accent)',
                      color: '#141210',
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
                    <Play size={16} fill="#141210" /> Слушать всё
                  </button>
                </div>
              </div>
            </div>

            {/* Releases Grid */}
            <h3 className="title-zine" style={{ fontSize: '18px', marginBottom: '16px' }}>
              РЕЛИЗЫ И АЛЬБОМЫ ({(selectedArtist.albums || []).length})
            </h3>
            <div className="tmusic-grid-albums">
              {(selectedArtist.albums || []).map((album) => renderAlbumCard(album, selectedArtist))}
            </div>

            {/* Nested cards (artists/collections dropped inside this one) */}
            {(selectedArtist.children || []).length > 0 && (
              <>
                <h3 className="title-zine" style={{ fontSize: '18px', margin: '26px 0 16px' }}>
                  ВНУТРИ ({selectedArtist.children.length})
                </h3>
                <div className="tmusic-grid-artists">
                  {selectedArtist.children.map((child) => renderArtistCard(child))}
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
            <div className="tmusic-search-screen-bar">
              <Search size={18} className="tmusic-search-screen-icon" />
              <input
                type="text"
                placeholder="Поиск по всей медиатеке..."
                value={search}
                onChange={handleSearchChange}
                autoFocus
                className="tmusic-search-screen-input"
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
               <div className="tmusic-grid-albums">
                 {items.map((album) => renderAlbumCard(album))}
               </div>
             ) : (
               <div className="tmusic-grid-artists">
                 {items.map((artist) => renderArtistCard(artist))}
               </div>
             )}
            </div>
          ) : (
            /* ─── Catalog: Медиатека (category strip) / Артисты / Альбомы ──────── */
            <div>
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
                          color: '#141210',
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
            {!folderMode && (isLoading ? (
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
              <div className="tmusic-grid-albums">
                {items.map((album) => renderAlbumCard(album))}
              </div>
            ) : (
              /* ─── Artists Grid (Circular Avatar Cards) ──────────────────── */
              <div className="tmusic-grid-artists">
                {items.map((artist) => renderArtistCard(artist))}
              </div>
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
          onClose={() => setSelectedAlbum(null)}
          onOpenCoverPicker={isAdmin ? onOpenCoverPicker : null}
        />
      )}

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
