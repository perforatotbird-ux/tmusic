import React, { useState, useEffect, useRef } from 'react'
import { libraryApi } from '../api/library'
import { streamApi } from '../api/stream'
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
function Fallback({ name, kind = 'artist', size = 120 }) {
  const hue = hashHue(name || '?')
  const letter = (name || '?').trim()[0] || '?'
  const isCircle = kind === 'artist'

  return (
    <div
      style={{
        width: typeof size === 'number' ? `${size}px` : size,
        height: typeof size === 'number' ? `${size}px` : size,
        borderRadius: isCircle ? '50%' : '8px',
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
          fontSize: isCircle ? '38px' : '32px',
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

// Image with automatic retry and Zine fallback badge on error
function ImgWithFallback({ name, kind = 'artist', src, alt = '', style = {}, size = 120 }) {
  const [hasError, setHasError] = useState(false)
  const [retries, setRetries] = useState(0)
  const timerRef = useRef(null)

  useEffect(() => {
    setHasError(false)
    setRetries(0)
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current)
    }
  }, [src])

  if (!src || hasError) {
    return <Fallback name={name} kind={kind} size={size} />
  }

  const handleError = () => {
    if (retries < 2) {
      timerRef.current = setTimeout(() => {
        setRetries((r) => r + 1)
      }, 1000 * (retries + 1))
    } else {
      setHasError(true)
    }
  }

  const effectiveSrc = retries > 0 ? `${src}${src.includes('?') ? '&' : '?'}r=${retries}` : src

  return (
    <img
      key={`${src}-${retries}`}
      src={effectiveSrc}
      alt={alt || name}
      onError={handleError}
      loading="lazy"
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

export function MusicLibrary({ onOpenCoverPicker }) {
  const [items, setItems] = useState([])
  const [selectedArtist, setSelectedArtist] = useState(null)
  const [activeKind, setActiveKind] = useState('artist')
  const [search, setSearch] = useState('')
  const [isLoading, setIsLoading] = useState(true)
  const [stats, setStats] = useState(null)
  // Monotonic request id: a slow earlier fetch must not overwrite a newer one
  // (fast typing fires parallel loadData calls that resolve out of order).
  const loadSeq = useRef(0)

  const { playQueue } = usePlayerStore()
  const { user, isAuthenticated, logout, setAuthModalOpen } = useAuthStore()

  useEffect(() => {
    if (isAuthenticated) {
      loadData()
      loadStats()
    } else {
      setIsLoading(false)
    }
  }, [activeKind, isAuthenticated])

  const loadStats = async () => {
    try {
      const s = await libraryApi.getStats()
      setStats(s)
    } catch {
      // Ignore
    }
  }

  const loadData = async (searchQuery = search) => {
    const mySeq = ++loadSeq.current
    setIsLoading(true)
    try {
      if (activeKind === 'albums') {
        const data = await libraryApi.getAlbums({
          search: searchQuery || null,
          limit: 500,
        })
        if (mySeq === loadSeq.current) setItems(data || [])
      } else {
        const data = await libraryApi.getArtists({
          kind: activeKind === 'all' ? null : activeKind,
          search: searchQuery || null,
          limit: 500,
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
    const val = e.target.value
    setSearch(val)
    loadData(val)
  }

  const clearSearch = () => {
    setSearch('')
    loadData('')
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
        const coverUrl =
          album.custom_image_url ||
          album.cover_url ||
          (artist && (artist.custom_image_url || artist.image_url)) ||
          '/icon.svg'

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

  const handlePlayAllArtist = async (artist) => {
    try {
      const tracks = await streamApi.getFolderTracks(artist.folder_id, true)
      if (tracks && tracks.length > 0) {
        playQueue(tracks, 0, {
          title: 'Все треки',
          artistName: artist.display_name,
          coverUrl: artist.custom_image_url || artist.image_url || '/icon.svg',
        })
      }
    } catch (e) {
      console.error('Failed to play all artist tracks:', e)
    }
  }

  const currentSection = SECTIONS.find((s) => s.id === activeKind) || SECTIONS[0]
  const isAlbumSection = currentSection.isAlbum

  return (
    <div style={{ paddingBottom: '90px', minHeight: '100vh', backgroundColor: 'var(--bg)' }}>
      {/* ─── Top Navbar ──────────────────────────────────────────────────────── */}
      <header
        style={{
          position: 'sticky',
          top: 0,
          backgroundColor: 'rgba(20, 18, 16, 0.95)',
          backdropFilter: 'blur(10px)',
          borderBottom: '1px solid var(--line)',
          padding: '12px 24px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          zIndex: 7000,
          gap: '20px',
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
        <div style={{ flex: 1, maxWidth: '420px', position: 'relative' }}>
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
              padding: '8px 34px 8px 34px',
              borderRadius: '999px',
              backgroundColor: 'rgba(255,255,255,0.04)',
              border: '1px solid var(--line)',
              color: 'var(--ink)',
              fontSize: '13px',
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
        <div style={{ display: 'flex', alignItems: 'center', gap: '14px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '12px', color: 'var(--accent)' }}>
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
      <main style={{ maxWidth: '1240px', margin: '0 auto', padding: '24px' }}>
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
            <div
              style={{
                display: 'flex',
                flexWrap: 'wrap',
                gap: '30px',
                padding: '24px',
                backgroundColor: 'var(--bg-card)',
                borderRadius: 'var(--radius)',
                border: '1px solid var(--line)',
                marginBottom: '32px',
                alignItems: 'center',
              }}
            >
              <div style={{ position: 'relative' }}>
                <ImgWithFallback
                  name={selectedArtist.display_name}
                  kind="artist"
                  src={selectedArtist.custom_image_url || selectedArtist.image_url}
                  size={180}
                  style={{
                    width: '180px',
                    height: '180px',
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

              <div style={{ flex: 1, minWidth: '280px' }}>
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
                <div style={{ display: 'flex', gap: '12px' }}>
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
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))',
                gap: '18px',
              }}
            >
              {(selectedArtist.albums || []).map((album) => {
                const cover =
                  album.custom_image_url ||
                  album.cover_url ||
                  selectedArtist.custom_image_url ||
                  selectedArtist.image_url
                return (
                  <div
                    key={album.id}
                    className="transition-card"
                    style={{
                      backgroundColor: 'var(--bg-card)',
                      border: '1px solid var(--line)',
                      borderRadius: 'var(--radius)',
                      padding: '12px',
                      display: 'flex',
                      flexDirection: 'column',
                      position: 'relative',
                    }}
                  >
                    <div style={{ position: 'relative', marginBottom: '10px' }}>
                      <ImgWithFallback
                        name={album.title}
                        kind="album"
                        src={cover}
                        size="100%"
                        style={{
                          width: '100%',
                          aspectRatio: '1/1',
                          borderRadius: '8px',
                          border: '1px solid var(--line)',
                        }}
                      />
                      <button
                        onClick={() => handlePlayAlbum(album, selectedArtist)}
                        style={{
                          position: 'absolute',
                          bottom: '10px',
                          right: '10px',
                          width: '40px',
                          height: '40px',
                          borderRadius: '50%',
                          backgroundColor: 'var(--accent)',
                          color: '#141210',
                          border: 'none',
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          cursor: 'pointer',
                          boxShadow: '0 4px 12px rgba(0,0,0,0.5)',
                        }}
                      >
                        <Play size={18} fill="#141210" style={{ marginLeft: '2px' }} />
                      </button>
                    </div>

                    <div
                      style={{
                        fontWeight: '700',
                        fontSize: '13.5px',
                        whiteSpace: 'nowrap',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        marginBottom: '4px',
                      }}
                    >
                      {album.title}
                    </div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '11.5px', color: 'var(--dim)' }}>
                      <span>{album.year || 'Альбом'}</span>
                      <button
                        onClick={(e) => {
                          e.stopPropagation()
                          onOpenCoverPicker({
                            type: 'album',
                            id: album.id,
                            title: album.title,
                            hasCustom: !!album.custom_image_url,
                          })
                        }}
                        style={{ background: 'none', border: 'none', color: 'var(--dim)', cursor: 'pointer' }}
                        title="Сменить обложку"
                      >
                        <ImageIcon size={13} />
                      </button>
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        ) : (
          /* ─── Catalog Overview ───────────────────────────────────────────── */
          <div>
            {/* Category Navigation Tabs */}
            <div style={{ display: 'flex', gap: '10px', marginBottom: '24px', overflowX: 'auto', paddingBottom: '4px' }}>
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
                    style={{
                      padding: '8px 16px',
                      borderRadius: '999px',
                      border: '1px solid',
                      borderColor: isActive ? 'var(--accent)' : 'var(--line)',
                      backgroundColor: isActive ? 'var(--accent-muted)' : 'rgba(255,255,255,0.02)',
                      color: isActive ? 'var(--accent)' : 'var(--ink)',
                      fontWeight: isActive ? '700' : '500',
                      fontSize: '13px',
                      display: 'flex',
                      alignItems: 'center',
                      gap: '8px',
                      cursor: 'pointer',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    <Icon size={15} /> {tab.label}
                  </button>
                )
              })}
            </div>

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
              <div
                style={{
                  display: 'grid',
                  gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))',
                  gap: '18px',
                }}
              >
                {items.map((album) => {
                  const cover = album.custom_image_url || album.cover_url || album.image_url
                  return (
                    <div
                      key={album.id}
                      className="transition-card"
                      style={{
                        backgroundColor: 'var(--bg-card)',
                        border: '1px solid var(--line)',
                        borderRadius: 'var(--radius)',
                        padding: '12px',
                        display: 'flex',
                        flexDirection: 'column',
                        position: 'relative',
                      }}
                    >
                      <div style={{ position: 'relative', marginBottom: '10px' }}>
                        <ImgWithFallback
                          name={album.title || album.display_name}
                          kind="album"
                          src={cover}
                          size="100%"
                          style={{
                            width: '100%',
                            aspectRatio: '1/1',
                            borderRadius: '8px',
                            border: '1px solid var(--line)',
                          }}
                        />
                        <button
                          onClick={() => handlePlayAlbum(album)}
                          style={{
                            position: 'absolute',
                            bottom: '10px',
                            right: '10px',
                            width: '40px',
                            height: '40px',
                            borderRadius: '50%',
                            backgroundColor: 'var(--accent)',
                            color: '#141210',
                            border: 'none',
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            cursor: 'pointer',
                            boxShadow: '0 4px 12px rgba(0,0,0,0.5)',
                          }}
                        >
                          <Play size={18} fill="#141210" style={{ marginLeft: '2px' }} />
                        </button>
                      </div>

                      <div
                        style={{
                          fontWeight: '700',
                          fontSize: '13.5px',
                          whiteSpace: 'nowrap',
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          marginBottom: '4px',
                        }}
                      >
                        {album.title || album.display_name}
                      </div>
                      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '11.5px', color: 'var(--dim)' }}>
                        <span>{album.year || (album.album_count ? `${album.album_count} альб.` : 'Релиз')}</span>
                        <button
                          onClick={(e) => {
                            e.stopPropagation()
                            onOpenCoverPicker({
                              type: album.title ? 'album' : 'artist',
                              id: album.id,
                              title: album.title || album.display_name,
                              hasCustom: !!album.custom_image_url,
                            })
                          }}
                          style={{ background: 'none', border: 'none', color: 'var(--dim)', cursor: 'pointer' }}
                          title="Сменить обложку"
                        >
                          <ImageIcon size={13} />
                        </button>
                      </div>
                    </div>
                  )
                })}
              </div>
            ) : (
              /* ─── Artists Grid (Circular Avatar Cards) ──────────────────── */
              <div
                style={{
                  display: 'grid',
                  gridTemplateColumns: 'repeat(auto-fill, minmax(170px, 1fr))',
                  gap: '18px',
                }}
              >
                {items.map((artist) => {
                  const cover = artist.custom_image_url || artist.image_url
                  return (
                    <div
                      key={artist.id}
                      onClick={() => openArtist(artist.id)}
                      className="transition-card"
                      style={{
                        backgroundColor: 'var(--bg-card)',
                        border: '1px solid var(--line)',
                        borderRadius: 'var(--radius)',
                        padding: '14px',
                        cursor: 'pointer',
                        display: 'flex',
                        flexDirection: 'column',
                        alignItems: 'center',
                        textAlign: 'center',
                      }}
                    >
                      <div style={{ marginBottom: '12px' }}>
                        <ImgWithFallback
                          name={artist.display_name}
                          kind="artist"
                          src={cover}
                          size={120}
                          style={{
                            width: '120px',
                            height: '120px',
                            borderRadius: '50%',
                            border: '2px solid var(--line-bright)',
                          }}
                        />
                      </div>
                      <div
                        style={{
                          fontWeight: '700',
                          fontSize: '13.5px',
                          color: 'var(--ink)',
                          width: '100%',
                          whiteSpace: 'nowrap',
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          marginBottom: '4px',
                        }}
                      >
                        {artist.display_name}
                      </div>
                      <div style={{ fontSize: '11px', color: 'var(--dim)' }}>
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
    </div>
  )
}
