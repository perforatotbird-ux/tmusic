import React, { useState, useEffect, useRef } from 'react'
import { libraryApi } from '../api/library'
import { streamApi } from '../api/stream'
import { missingCoverKeys, parseCoverKey } from '../services/coverMissing'
import { resolveCoverUrl } from '../utils/cover'
import { AlbumDetail } from './AlbumDetail'
import { HomeExtraPanels } from './HomeExtraPanels'
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

// The "Артисты" / "Альбомы" tabs are fixed views catalogued under these kinds.
const MOBILE_TAB_KIND = { artists: 'artist', albums: 'albums' }

export function MusicLibrary({ onOpenCoverPicker }) {
  const [items, setItems] = useState([])
  const [selectedArtist, setSelectedArtist] = useState(null)
  const [selectedAlbum, setSelectedAlbum] = useState(null) // { album, artist } — AlbumDetail sheet
  const [activeKind, setActiveKind] = useState('artist')
  const [mobileTab, setMobileTab] = useState('library') // 'library' | 'artists' | 'albums' | 'search' | 'profile'
  const [search, setSearch] = useState('')
  const [isLoading, setIsLoading] = useState(true)
  const [stats, setStats] = useState(null)
  // Bumped when the cover manifest arrives so the grid re-renders with fresh
  // missing-cover knowledge (the registry itself is not React state).
  const [, setManifestTick] = useState(0)
  // Monotonic request id: a slow earlier fetch must not overwrite a newer one
  // (fast typing fires parallel loadData calls that resolve out of order).
  const loadSeq = useRef(0)

  const { playQueue, currentTrack } = usePlayerStore()
  const { user, isAuthenticated, logout, setAuthModalOpen } = useAuthStore()

  // Which catalog section is on screen: the Артисты/Альбомы tabs are fixed views,
  // everything else follows the category strip picked under Медиатека.
  const viewKind = MOBILE_TAB_KIND[mobileTab] || activeKind
  const currentSection = SECTIONS_BY_ID[viewKind] || SECTIONS[0]
  const isAlbumSection = currentSection.isAlbum

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

  // Prefetch cover existence for the current grid: images proven missing render
  // the fallback badge without firing a GET (+retries). Fire-and-forget — any
  // failure leaves the registry empty, i.e. previous behavior.
  useEffect(() => {
    const byCustom = { 0: [], 1: [] }
    for (const it of items) {
      for (const u of [it?.custom_image_url, it?.image_url]) {
        const p = parseCoverKey(u)
        if (p && !missingCoverKeys.has(`${p.custom ? 1 : 0}:${p.key}`)) {
          byCustom[p.custom ? 1 : 0].push(p.key)
        }
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
      if (viewKind === 'albums') {
        const data = await libraryApi.getAlbums({
          search: search || null,
          limit: 100,
        })
        if (mySeq === loadSeq.current) setItems(data || [])
      } else {
        const data = await libraryApi.getArtists({
          kind: viewKind === 'all' ? null : viewKind,
          search: search || null,
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

  // Cover tap → open the tracklist sheet (playback starts from there).
  const openAlbum = (album, artist = null) => {
    setSelectedAlbum({ album, artist })
  }

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

        {/* Security / Account Status */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          {/* Quick Search Button on Mobile */}
          <button
            onClick={() => setMobileTab('search')}
            className="tmusic-mobile-search-trigger"
            aria-label="Поиск"
            title="Поиск"
          >
            <Search size={19} />
          </button>

          <div className="tmusic-ssl-badge" style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '12px', color: 'var(--accent)' }}>
            <ShieldCheck size={16} />
            <span style={{ fontWeight: '600' }}>SSL • SECURE</span>
          </div>
          {user ? (
            <button
              onClick={logout}
              title="Выйти"
              style={{
                background: 'none',
                border: 'none',
                color: 'var(--dim)',
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: '4px',
                fontSize: '12px',
              }}
            >
              <LogOut size={16} />
            </button>
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
      </header>

      {/* ─── Main Content Body ────────────────────────────────────────────────── */}
      <main className="tmusic-main">
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
                  title="Изменить фото"
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
                    display: 'flex',
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
              {(selectedArtist.albums || []).map((album) => {
                const cover = resolveCoverUrl(album, selectedArtist, null)
                return (
                  <div
                    key={album.id}
                    onClick={() => openAlbum(album, selectedArtist)}
                    className="tmusic-card"
                  >
                    <div className="tmusic-card-cover-wrap">
                      <ImgWithFallback
                        name={album.title}
                        kind="album"
                        src={cover}
                        size="100%"
                        className="tmusic-card-img"
                      />
                      <button
                        className="tmusic-card-play-btn"
                        onClick={(e) => {
                          e.stopPropagation()
                          handlePlayAlbum(album, selectedArtist)
                        }}
                        aria-label={`Воспроизвести ${album.title}`}
                      >
                        <Play size={20} fill="#141210" style={{ marginLeft: '2px' }} />
                      </button>
                    </div>

                    <div className="tmusic-card-title">
                      {album.title}
                    </div>
                    <div className="tmusic-card-sub">
                      {album.year || 'Альбом'}
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        ) : mobileTab === 'profile' ? (
          /* ─── Mobile Profile Screen ───────────────────────────────────────── */
          <div className="tmusic-profile-screen">
            <div className="tmusic-profile-header">
              <div className="tmusic-profile-avatar">
                <span>{(user?.username || 'A').toUpperCase().charAt(0)}</span>
              </div>
              <div className="tmusic-profile-username">{user?.username || 'admin'}</div>
              <div className="tmusic-profile-badge">
                <ShieldCheck size={14} /> {user?.role === 'admin' || user?.username === 'admin' ? 'Администратор • SSL Защищено' : 'Слушатель • SSL Защищено'}
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
            ) : isAlbumSection ? (
              <div className="tmusic-grid-albums">
                {items.map((album) => {
                  const cover = resolveCoverUrl(album, null, null)
                  return (
                    <div
                      key={album.id}
                      onClick={() => openAlbum(album)}
                      className="tmusic-card"
                    >
                      <div className="tmusic-card-cover-wrap">
                        <ImgWithFallback
                          name={album.title || album.display_name}
                          kind="album"
                          src={cover}
                          size="100%"
                          className="tmusic-card-img"
                        />
                        <button
                          className="tmusic-card-play-btn"
                          onClick={(e) => {
                            e.stopPropagation()
                            handlePlayAlbum(album)
                          }}
                          aria-label={`Воспроизвести ${album.title || album.display_name}`}
                        >
                          <Play size={20} fill="#141210" style={{ marginLeft: '2px' }} />
                        </button>
                      </div>
                      <div className="tmusic-card-title">{album.title || album.display_name}</div>
                      <div className="tmusic-card-sub">{album.year || 'Релиз'}</div>
                    </div>
                  )
                })}
              </div>
            ) : (
              <div className="tmusic-grid-artists">
                {items.map((artist) => {
                  const cover = resolveCoverUrl(artist, null, null)
                  return (
                    <div
                      key={artist.id}
                      onClick={() => openArtist(artist.id)}
                      className="tmusic-artist-card"
                    >
                      <div className="tmusic-artist-card-avatar-wrap">
                        <ImgWithFallback
                          name={artist.display_name}
                          kind="artist"
                          src={cover}
                          size="100%"
                          className="tmusic-artist-card-avatar"
                        />
                      </div>
                      <div className="tmusic-artist-card-name">{artist.display_name}</div>
                      <div className="tmusic-artist-card-sub">
                        {artist.album_count} {artist.album_count === 1 ? 'релиз' : 'релизов'}
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        ) : (
          /* ─── Catalog: Медиатека (category strip) / Артисты / Альбомы ──────── */
          <div>
            {showCatalogStrip ? (
              <>
                <HomeExtraPanels />
                {/* Category Navigation Tabs */}
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
              </>
            ) : (
              <h2 className="title-zine" style={{ fontSize: '19px', marginBottom: '16px' }}>
                {currentSection.label}
              </h2>
            )}

            {/* Loading Indicator */}
            {isLoading ? (
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
                {items.map((album) => {
                  const cover = resolveCoverUrl(album, null, null)
                  return (
                    <div
                      key={album.id}
                      onClick={() => openAlbum(album)}
                      className="tmusic-card"
                    >
                      <div className="tmusic-card-cover-wrap">
                        <ImgWithFallback
                          name={album.title || album.display_name}
                          kind="album"
                          src={cover}
                          size="100%"
                          className="tmusic-card-img"
                        />
                        <button
                          className="tmusic-card-play-btn"
                          onClick={(e) => {
                            e.stopPropagation()
                            handlePlayAlbum(album)
                          }}
                          aria-label={`Воспроизвести ${album.title || album.display_name}`}
                        >
                          <Play size={20} fill="#141210" style={{ marginLeft: '2px' }} />
                        </button>
                      </div>

                      <div className="tmusic-card-title">
                        {album.title || album.display_name}
                      </div>
                      <div className="tmusic-card-sub">
                        {album.year || (album.album_count ? `${album.album_count} альб.` : 'Релиз')}
                      </div>
                    </div>
                  )
                })}
              </div>
            ) : (
              /* ─── Artists Grid (Circular Avatar Cards) ──────────────────── */
              <div className="tmusic-grid-artists">
                {items.map((artist) => {
                  const cover = resolveCoverUrl(artist, null, null)
                  return (
                    <div
                      key={artist.id}
                      onClick={() => openArtist(artist.id)}
                      className="tmusic-artist-card"
                    >
                      <div className="tmusic-artist-card-avatar-wrap">
                        <ImgWithFallback
                          name={artist.display_name}
                          kind="artist"
                          src={cover}
                          size="100%"
                          className="tmusic-artist-card-avatar"
                        />
                      </div>
                      <div className="tmusic-artist-card-name">
                        {artist.display_name}
                      </div>
                      <div className="tmusic-artist-card-sub">
                        {artist.album_count} {artist.album_count === 1 ? 'релиз' : 'релизов'}
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        )}
      </main>

      {/* Album tracklist sheet (opened by tapping an album cover) */}
      {selectedAlbum && (
        <AlbumDetail
          album={selectedAlbum.album}
          artist={selectedAlbum.artist}
          onClose={() => setSelectedAlbum(null)}
          onOpenCoverPicker={onOpenCoverPicker}
        />
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
