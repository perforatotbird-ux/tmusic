import React, { useEffect, useRef, useState } from 'react'
import { streamApi } from '../api/stream'
import { playlistsApi } from '../api/playlists'
import { adminApi } from '../api/admin'
import { resolveCoverUrl } from '../utils/cover'
import { trackRenamePatch } from '../utils/adminEdits'
import { withReleaseMeta } from '../utils/cachedBrowse'
import { usePlayerStore } from '../store/playerStore'
import { useAuthStore } from '../store/authStore'
import {
  X,
  Play,
  Pause,
  Loader2,
  Music,
  ListMusic,
  Image as ImageIcon,
  ListPlus,
  ListStart,
  ListEnd,
  Check,
  Star,
  MoreVertical,
  HardDrive,
  Trash2,
  Pencil,
} from 'lucide-react'
import { useFavoritesStore } from '../store/favoritesStore'
import { useOfflineStore } from '../store/offlineStore'
import { useBodyScrollLock } from '../hooks/useBodyScrollLock'
import { ClickableRow } from './ui/ClickableRow'

function formatSizeHint(track) {
  if (track?.size && Number.isFinite(track.size) && track.size > 0) {
    const mb = track.size / (1024 * 1024)
    return mb >= 1 ? `${mb.toFixed(1)} МБ` : `${Math.max(1, Math.round(track.size / 1024))} КБ`
  }
  return ''
}

function formatTime(sec) {
  if (!sec || isNaN(sec)) return ''
  const s = Math.max(0, Math.floor(sec))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

function stripExt(name) {
  return (name || 'Без названия').replace(/\.[^/.]+$/, '')
}

// Small square control used by the admin-only inline editors in the sheet head.
const headerIconBtn = {
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
  width: '30px',
  height: '30px',
  flexShrink: 0,
  borderRadius: '8px',
  border: '1px solid var(--line)',
  background: 'transparent',
  color: 'var(--dim)',
  cursor: 'pointer',
}

/**
 * `cachedTracks` short-circuits the folder fetch: a «В кэше» album has no
 * catalog folder_id, its tracklist already lives on the device.
 */
export function AlbumDetail({ album, artist = null, onClose, onOpenCoverPicker, cachedTracks = null }) {
  const [tracks, setTracks] = useState([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState(null)
  const [playlistTarget, setPlaylistTarget] = useState(null) // track being added
  const [trackMenu, setTrackMenu] = useState(null) // { track, x, y, flip } — ⋯ menu anchored to its button
  const [playlists, setPlaylists] = useState([])
  const [playlistsLoading, setPlaylistsLoading] = useState(false)
  const [newPlName, setNewPlName] = useState('')
  const [playlistError, setPlaylistError] = useState(null)
  const [playlistBusy, setPlaylistBusy] = useState(false)
  const playlistMutationRef = useRef(false)
  const [createUncertain, setCreateUncertain] = useState(false)
  const [addedFlash, setAddedFlash] = useState(null)
  // Admin curation: inline track rename + a locally-overridden album title so a
  // successful PATCH is visible without refetching the whole catalog.
  const [editingTrack, setEditingTrack] = useState(null) // { id, draft }
  const [trackBusyId, setTrackBusyId] = useState(null)
  const [trackEditError, setTrackEditError] = useState(null)
  const [titleOverride, setTitleOverride] = useState(null)
  const [renamingAlbum, setRenamingAlbum] = useState(false)
  const [albumNameDraft, setAlbumNameDraft] = useState('')
  const [albumBusy, setAlbumBusy] = useState(false)
  const loadSeq = useRef(0)
  const flashTimer = useRef(null)
  const flashMounted = useRef(false)
  useEffect(() => {
    flashMounted.current = true
    return () => {
      flashMounted.current = false
      clearTimeout(flashTimer.current)
      flashTimer.current = null
    }
  }, [])
  const clearPlaylistFlashLater = () => {
    if (!flashMounted.current) return
    clearTimeout(flashTimer.current)
    flashTimer.current = setTimeout(() => {
      flashTimer.current = null
      setAddedFlash(null)
      setPlaylistTarget(null)
    }, 550)
  }

  const { playQueue, queue, queueIndex, isPlaying, togglePlay, playNext, addToQueue, setNotice, durations } = usePlayerStore()
  const { isAuthenticated } = useAuthStore()
  const favIds = useFavoritesStore((s) => s.ids)
  const loadFavorites = useFavoritesStore((s) => s.load)
  const toggleFavorite = useFavoritesStore((s) => s.toggle)

  const cachedTrackIds = useOfflineStore((s) => s.cachedTrackIds)
  const cachingTrackIds = useOfflineStore((s) => s.cachingTrackIds)
  const cacheTrack = useOfflineStore((s) => s.cacheTrack)
  const removeTrack = useOfflineStore((s) => s.removeTrack)

  useEffect(() => {
    if (isAuthenticated) loadFavorites()
  }, [isAuthenticated, loadFavorites])

  const cover = resolveCoverUrl(album, null)
  // `onOpenCoverPicker` is only wired up for admins (MusicLibrary gates it), so
  // it doubles as the admin flag for every curation control in this sheet.
  const isAdmin = !!onOpenCoverPicker
  const albumTitle = titleOverride || album?.title || album?.display_name || 'Альбом'
  const artistName = (artist && artist.display_name) || albumTitle

  // UI-11/UI-26: iOS-correct scroll lock (position:fixed + offset) instead of
  // the old body.overflow trick, which fails to lock scroll on iOS Safari.
  useBodyScrollLock(true)

  // UI-11/UI-26: move focus into the dialog on open and restore it on close.
  const overlayRef = useRef(null)
  useEffect(() => {
    const prev = document.activeElement
    const raf = requestAnimationFrame(() => overlayRef.current?.focus?.())
    return () => {
      cancelAnimationFrame(raf)
      if (prev && typeof prev.focus === 'function' && document.contains(prev)) {
        try { prev.focus({ preventScroll: true }) } catch { /* gone */ }
      }
    }
  }, [])

  // UI-29 + M-05: anchored menus (track ⋮ and playlist picker) are positioned
  // once. Recompute is impossible without the source node, so close them on
  // any viewport change — and on scroll (capture): the menu is position:fixed
  // while its ⋮ anchor scrolls away inside the sheet, leaving the menu
  // floating over unrelated rows on touch devices.
  useEffect(() => {
    const closeMenus = () => {
      setTrackMenu(null)
      setPlaylistTarget(null)
    }
    window.addEventListener('resize', closeMenus)
    window.addEventListener('orientationchange', closeMenus)
    window.addEventListener('scroll', closeMenus, true)
    return () => {
      window.removeEventListener('resize', closeMenus)
      window.removeEventListener('orientationchange', closeMenus)
      window.removeEventListener('scroll', closeMenus, true)
    }
  }, [])

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape') {
        if (trackMenu) setTrackMenu(null)
        else if (playlistTarget) setPlaylistTarget(null)
        else onClose?.()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose, playlistTarget, trackMenu])

  useEffect(() => {
    if (cachedTracks) {
      setTracks(cachedTracks)
      setError(null)
      setIsLoading(false)
      return undefined
    }
    let cancelled = false
    const mySeq = ++loadSeq.current
    setIsLoading(true)
    setError(null)
    setTracks([])
    ;(async () => {
      try {
        const list = await streamApi.getFolderTracks(album.folder_id, !album.is_tracks)
        if (!cancelled && mySeq === loadSeq.current) {
          setTracks(Array.isArray(list) ? list : [])
        }
      } catch (e) {
        if (!cancelled && mySeq === loadSeq.current) {
          setError('Не удалось загрузить треки')
        }
      } finally {
        if (!cancelled && mySeq === loadSeq.current) {
          setIsLoading(false)
        }
      }
    })()
    return () => {
      cancelled = true
    }
  }, [album?.folder_id, album?.is_tracks, cachedTracks])

  // A cover change made through the picker must show up in the sheet that is
  // already open, without a page reload (UI-08) and without the loading
  // flicker a full re-mount would cause. Refetch in place instead.
  useEffect(() => {
    if (cachedTracks) return undefined
    let cancelled = false
    const onCatalogChanged = () => {
      streamApi
        .getFolderTracks(album.folder_id, !album.is_tracks)
        .then((list) => {
          if (!cancelled) setTracks(Array.isArray(list) ? list : [])
        })
        .catch(() => {
          /* keep the current list — a stale cover beats an empty sheet */
        })
    }
    window.addEventListener('tmusic:catalog-changed', onCatalogChanged)
    return () => {
      cancelled = true
      window.removeEventListener('tmusic:catalog-changed', onCatalogChanged)
    }
  }, [album?.folder_id, album?.is_tracks, cachedTracks])

  const openPlaylistPicker = async (track) => {
    setTrackMenu(null)
    setPlaylistTarget(track)
    if (!isAuthenticated) return
    setPlaylistsLoading(true)
    setPlaylistError(null)
    try {
      const list = await playlistsApi.list()
      setPlaylists(list || [])
      setCreateUncertain(false)
    } catch {
      setPlaylistError('Не удалось обновить список плейлистов. Попробуйте ещё раз.')
    } finally {
      setPlaylistsLoading(false)
    }
  }

  const addToPlaylist = async (playlist) => {
    if (!playlistTarget || !playlist || playlistMutationRef.current) return
    playlistMutationRef.current = true
    setPlaylistBusy(true)
    setPlaylistError(null)
    try {
      await playlistsApi.addTrack(playlist.id, playlistTarget.id)
      setAddedFlash(playlist.id)
      clearPlaylistFlashLater()
    } catch (error) {
      setPlaylistError(error?.message || 'Не удалось добавить трек в плейлист.')
    } finally {
      playlistMutationRef.current = false
      setPlaylistBusy(false)
    }
  }

  const createAndAdd = async () => {
    const name = newPlName.trim()
    if (!name || !playlistTarget || playlistMutationRef.current || createUncertain) return
    playlistMutationRef.current = true
    setPlaylistBusy(true)
    setPlaylistError(null)
    try {
      const pl = await playlistsApi.create(name, playlistTarget.id)
      setNewPlName('')
      setAddedFlash(pl.id)
      clearPlaylistFlashLater()
    } catch (error) {
      const uncertain = !error?.status || error.status >= 500 || error.code === 'invalid_response'
      setCreateUncertain(uncertain)
      setPlaylistError(uncertain
        ? 'Создание не подтверждено. Обновите список и проверьте, появился ли плейлист, перед повтором.'
        : error?.message || 'Не удалось создать плейлист с треком.')
    } finally {
      playlistMutationRef.current = false
      setPlaylistBusy(false)
    }
  }

  const releaseInfo = {
    title: albumTitle,
    artistName,
    coverUrl: resolveCoverUrl(album),
  }

  // Queue tracks must carry the release context themselves: stream rows have
  // no artist/album/cover, and only per-track fields reach the offline cache
  // (see withReleaseMeta) — otherwise the row lands in «Без исполнителя».
  const queueTracks = (list) => withReleaseMeta(list, {
    artist: artistName,
    album: albumTitle,
    coverUrl: resolveCoverUrl(album, null),
  })

  const handlePlayAll = () => {
    if (tracks.length > 0) playQueue(queueTracks(tracks), 0, releaseInfo)
  }

  const handlePlayTrack = (idx) => {
    playQueue(queueTracks(tracks), idx, releaseInfo)
  }

  const isThisQueue = queue.length > 0 && tracks.length > 0 && queue[0]?.id === tracks[0]?.id

  const playNextWithToast = (track) => {
    playNext(track)
    setTrackMenu(null)
    setNotice({ kind: 'info', text: `«${stripExt(track.name)}» — следующим` })
  }
  const addToQueueWithToast = (track) => {
    addToQueue(track)
    setTrackMenu(null)
    setNotice({ kind: 'info', text: `«${stripExt(track.name)}» — добавлен в очередь` })
  }

  // ─── Admin curation: rename a track / the album ───────────────────────────
  const saveTrackName = async (track) => {
    const body = trackRenamePatch(editingTrack?.draft, track.name)
    if (!body) {
      // Blank or unchanged draft: close the editor, never fire a no-op PATCH.
      setEditingTrack(null)
      return
    }
    setTrackBusyId(track.id)
    setTrackEditError(null)
    try {
      await adminApi.patchVfsFile(track.id, body)
      setTracks((prev) => prev.map((t) => (t.id === track.id ? { ...t, name: body.name } : t)))
      setEditingTrack(null)
    } catch (e) {
      setTrackEditError(e.message || 'Не удалось переименовать трек')
    } finally {
      setTrackBusyId(null)
    }
  }

  const saveAlbumName = async () => {
    const body = trackRenamePatch(albumNameDraft, albumTitle)
    if (!body) {
      setRenamingAlbum(false)
      return
    }
    setAlbumBusy(true)
    setTrackEditError(null)
    try {
      await adminApi.patchAlbum(album.id, { title: body.name })
      setTitleOverride(body.name)
      setRenamingAlbum(false)
    } catch (e) {
      setTrackEditError(e.message || 'Не удалось переименовать альбом')
    } finally {
      setAlbumBusy(false)
    }
  }

  return (
    <div
      ref={overlayRef}
      tabIndex={-1}
      className="tmusic-album-overlay"
      onClick={() => {
        if (trackMenu) setTrackMenu(null)
        else if (playlistTarget) setPlaylistTarget(null)
        else onClose?.()
      }}
      role="dialog"
      aria-modal="true"
      aria-label={albumTitle}
    >
      <div className="tmusic-album-sheet" onClick={(e) => e.stopPropagation()}>
        <div className="tmusic-sheet-handle" />
        <div className="tmusic-album-head">
          <div style={{ position: 'relative', flexShrink: 0 }}>
            {cover ? (
              <img
                src={cover}
                alt={albumTitle}
                style={{
                  width: '84px',
                  height: '84px',
                  borderRadius: '10px',
                  objectFit: 'cover',
                  border: '1px solid var(--line-bright)',
                  display: 'block',
                }}
              />
            ) : (
              <div
                style={{
                  width: '84px',
                  height: '84px',
                  borderRadius: '10px',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  background: 'var(--bg-surface)',
                  border: '1px solid var(--line)',
                  color: 'var(--dim)',
                }}
              >
                <Music size={28} />
              </div>
            )}
            {onOpenCoverPicker && (
              <button
                onClick={() =>
                  onOpenCoverPicker({
                    type: album.title ? 'album' : 'artist',
                    id: album.id,
                    title: albumTitle,
                    hasCustom: !!album.custom_image_url,
                  })
                }
                title="Сменить обложку"
                style={{
                  position: 'absolute',
                  bottom: '-6px',
                  right: '-6px',
                  width: '28px',
                  height: '28px',
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
                <ImageIcon size={13} />
              </button>
            )}
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div
              style={{
                fontSize: '11px',
                textTransform: 'uppercase',
                color: 'var(--accent)',
                fontWeight: '700',
                letterSpacing: '0.08em',
                marginBottom: '2px',
              }}
            >
              {album?.year || 'Релиз'}
            </div>
            {renamingAlbum ? (
              <div style={{ display: 'flex', alignItems: 'center', gap: '6px', margin: '0 0 2px 0' }}>
                <input
                  value={albumNameDraft}
                  onChange={(e) => setAlbumNameDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') saveAlbumName()
                    if (e.key === 'Escape') setRenamingAlbum(false)
                  }}
                  autoFocus
                  aria-label="Новое название альбома"
                  style={{
                    flex: 1,
                    minWidth: 0,
                    fontSize: '15px',
                    padding: '6px 8px',
                    borderRadius: '8px',
                    border: '1px solid var(--line-bright)',
                    background: 'var(--bg)',
                    color: 'var(--ink)',
                  }}
                />
                <button
                  type="button"
                  onClick={saveAlbumName}
                  disabled={albumBusy}
                  aria-label="Сохранить название альбома"
                  style={headerIconBtn}
                >
                  {albumBusy ? <Loader2 size={14} className="tmusic-spin" /> : <Check size={15} style={{ color: 'var(--accent)' }} />}
                </button>
                <button type="button" onClick={() => setRenamingAlbum(false)} aria-label="Отмена" style={headerIconBtn}>
                  <X size={15} />
                </button>
              </div>
            ) : (
              <h2
                className="title-zine"
                style={{
                  fontSize: '19px',
                  lineHeight: 1.2,
                  margin: '0 0 2px 0',
                  whiteSpace: 'nowrap',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                }}
              >
                {albumTitle}
              </h2>
            )}
            {isAdmin && !renamingAlbum && !album?.is_cached_album && (
              <button
                type="button"
                onClick={() => {
                  setAlbumNameDraft(albumTitle)
                  setRenamingAlbum(true)
                  setTrackEditError(null)
                }}
                style={{ ...headerIconBtn, width: 'auto', padding: '0 8px', gap: '5px', fontSize: '11.5px', marginTop: '2px' }}
              >
                <Pencil size={12} /> Переименовать
              </button>
            )}
            {trackEditError && (
              <div style={{ marginTop: '6px', fontSize: '11.5px', color: 'var(--danger)' }}>{trackEditError}</div>
            )}
            <div style={{ fontSize: '12.5px', color: 'var(--dim)' }}>
              {artistName}
              {!isLoading && tracks.length > 0 && ` • ${tracks.length} тр.`}
            </div>
            <button
              onClick={handlePlayAll}
              disabled={isLoading || tracks.length === 0}
              style={{
                marginTop: '10px',
                padding: '9px 18px',
                backgroundColor: tracks.length === 0 ? 'var(--line-bright)' : 'var(--accent)',
                color: 'var(--on-accent)',
                border: 'none',
                borderRadius: '999px',
                fontWeight: '800',
                fontSize: '12.5px',
                display: 'inline-flex',
                alignItems: 'center',
                gap: '7px',
                cursor: tracks.length === 0 ? 'default' : 'pointer',
                opacity: isLoading ? 0.6 : 1,
                minHeight: '40px',
              }}
            >
              {isLoading ? <Loader2 size={15} className="tmusic-spin" /> : <Play size={15} fill="currentColor" />}
              Слушать всё
            </button>
          </div>

          <button
            onClick={onClose}
            aria-label="Закрыть"
            style={{
              alignSelf: 'flex-start',
              background: 'none',
              border: 'none',
              color: 'var(--dim)',
              cursor: 'pointer',
              padding: '8px',
              minWidth: '40px',
              minHeight: '40px',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            <X size={20} />
          </button>

        </div>

        <div className="tmusic-album-list">
          {isLoading && (
            <div className="tmusic-album-state">
              <Loader2 size={18} className="tmusic-spin" />
              Загрузка треков...
            </div>
          )}

          {!isLoading && error && (
            <div className="tmusic-album-state" style={{ color: 'var(--danger)' }}>
              {error}
            </div>
          )}

          {!isLoading && !error && tracks.length === 0 && (
            <div className="tmusic-album-state">В этом релизе нет треков</div>
          )}

          {!isLoading &&
            !error &&
            tracks.map((t, idx) => {
              const isCurrent = isThisQueue && queue[queueIndex]?.id === t.id
              const playing = isCurrent && isPlaying
              const dur = formatTime(t.duration || durations[t.id] || 0)
              return (
                <ClickableRow
                  key={t.id || idx}
                  onClick={() => (isCurrent ? togglePlay() : handlePlayTrack(idx))}
                  className="tmusic-track-row"
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '12px',
                    padding: '10px 12px',
                    borderRadius: '10px',
                    cursor: 'pointer',
                    backgroundColor: isCurrent ? 'var(--accent-muted)' : 'transparent',
                    position: 'relative',
                  }}
                >
                  <span
                    style={{
                      width: '26px',
                      flexShrink: 0,
                      textAlign: 'center',
                      color: isCurrent ? 'var(--accent)' : 'var(--dim)',
                      fontSize: '12px',
                      fontWeight: isCurrent ? '800' : '400',
                    }}
                  >
                    {playing ? (
                      <Pause size={15} fill="var(--accent)" style={{ verticalAlign: 'middle' }} />
                    ) : isCurrent ? (
                      <Play size={15} fill="var(--accent)" style={{ verticalAlign: 'middle' }} />
                    ) : (
                      idx + 1
                    )}
                  </span>
                  {t.custom_image_url && (
                    <img
                      src={t.custom_image_url}
                      alt=""
                      aria-hidden="true"
                      style={{
                        width: '30px',
                        height: '30px',
                        borderRadius: '6px',
                        objectFit: 'cover',
                        flexShrink: 0,
                        border: '1px solid var(--line)',
                      }}
                    />
                  )}
                  <div
                    style={{
                      flex: 1,
                      minWidth: 0,
                      fontSize: '13.5px',
                      fontWeight: isCurrent ? '700' : '500',
                      color: isCurrent ? 'var(--accent)' : 'var(--ink)',
                      display: 'flex',
                      alignItems: 'center',
                      gap: '8px',
                    }}
                  >
                    {editingTrack?.id === t.id ? (
                      <>
                        <input
                          value={editingTrack.draft}
                          onClick={(e) => e.stopPropagation()}
                          onChange={(e) => setEditingTrack({ id: t.id, draft: e.target.value })}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter') saveTrackName(t)
                            if (e.key === 'Escape') setEditingTrack(null)
                          }}
                          autoFocus
                          aria-label={`Новое название трека ${stripExt(t.name)}`}
                          style={{
                            flex: 1,
                            minWidth: 0,
                            fontSize: '13px',
                            padding: '5px 8px',
                            borderRadius: '6px',
                            border: '1px solid var(--line-bright)',
                            background: 'var(--bg)',
                            color: 'var(--ink)',
                          }}
                        />
                        <button
                          type="button"
                          onClick={(e) => { e.stopPropagation(); saveTrackName(t) }}
                          disabled={trackBusyId === t.id}
                          aria-label="Сохранить название трека"
                          style={{ ...headerIconBtn, width: '28px', height: '28px' }}
                        >
                          {trackBusyId === t.id
                            ? <Loader2 size={13} className="tmusic-spin" />
                            : <Check size={14} style={{ color: 'var(--accent)' }} />}
                        </button>
                        <button
                          type="button"
                          onClick={(e) => { e.stopPropagation(); setEditingTrack(null) }}
                          aria-label="Отмена"
                          style={{ ...headerIconBtn, width: '28px', height: '28px' }}
                        >
                          <X size={14} />
                        </button>
                      </>
                    ) : (
                      <span style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                        {stripExt(t.name)}
                      </span>
                    )}
                    {cachedTrackIds.has(Number(t.id)) && (
                      <span
                        className="tmusic-badge-cached"
                        title="В кэше • Доступно офлайн"
                        style={{
                          display: 'inline-flex',
                          alignItems: 'center',
                          gap: '3px',
                          fontSize: '10px',
                          fontWeight: '800',
                          padding: '1px 6px',
                          borderRadius: '6px',
                          background: 'rgba(34, 197, 94, 0.15)',
                          color: 'var(--success)',
                          border: '1px solid rgba(34, 197, 94, 0.3)',
                          flexShrink: 0,
                        }}
                      >
                        <Check size={11} strokeWidth={3} /> Кэш
                      </span>
                    )}
                    {cachingTrackIds.has(Number(t.id)) && (
                      <span
                        className="tmusic-badge-caching"
                        title="Кеширование..."
                        style={{
                          display: 'inline-flex',
                          alignItems: 'center',
                          gap: '3px',
                          fontSize: '10px',
                          fontWeight: '700',
                          padding: '1px 6px',
                          borderRadius: '6px',
                          background: 'rgba(56, 189, 248, 0.15)',
                          color: 'var(--info)',
                          flexShrink: 0,
                        }}
                      >
                        <Loader2 size={11} className="animate-spin" /> Загрузка
                      </span>
                    )}
                  </div>
                  {/* M-06: hidden on phones — duration + three 44px buttons
                      crushed the title to ~20px on a 360px sheet. */}
                  <span className="tm-track-dur" style={{ fontSize: '11px', color: 'var(--dim)', flexShrink: 0, minWidth: '38px', textAlign: 'right' }}>
                    {dur || formatSizeHint(t)}
                  </span>
                  {isAuthenticated && (
                    <button
                      onClick={(e) => {
                        e.stopPropagation()
                        toggleFavorite(t.id)
                      }}
                      title={favIds.has(t.id) ? 'Убрать из любимых' : 'В любимые'}
                      aria-label={favIds.has(t.id) ? 'Убрать из любимых' : 'В любимые'}
                      style={{
                        flexShrink: 0,
                        width: '32px',
                        height: '32px',
                        borderRadius: '8px',
                        border: '1px solid var(--line, #333)',
                        background: 'transparent',
                        color: favIds.has(t.id) ? 'var(--accent)' : 'var(--dim)',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        cursor: 'pointer',
                      }}
                    >
                      <Star size={15} fill={favIds.has(t.id) ? 'var(--accent)' : 'none'} />
                    </button>
                  )}
                  <button
                    onClick={(e) => {
                      e.stopPropagation()
                      openPlaylistPicker(t)
                    }}
                    title="В плейлист"
                    aria-label={`Добавить ${stripExt(t.name)} в плейлист`}
                    style={{
                      flexShrink: 0,
                      width: '32px',
                      height: '32px',
                      borderRadius: '8px',
                      border: '1px solid var(--line, #333)',
                      background: 'transparent',
                      color: 'var(--dim)',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      cursor: 'pointer',
                    }}
                  >
                    <ListPlus size={15} />
                  </button>
                  <button
                    onClick={(e) => {
                      e.stopPropagation()
                      if (trackMenu?.track?.id === t.id) {
                        setTrackMenu(null)
                        return
                      }
                      const rect = e.currentTarget.getBoundingClientRect()
                      // Anchor bottom-left of the ⋮ button; flip above when the
                      // sheet's scroll region would clip the menu at the bottom.
                      const fitsBelow = window.innerHeight - rect.bottom > 120
                      setTrackMenu({
                        track: t,
                        x: Math.min(rect.left, window.innerWidth - 226),
                        y: fitsBelow ? rect.bottom + 4 : rect.top - 4,
                        flip: !fitsBelow,
                      })
                    }}
                    title="Очередь"
                    aria-label={`Действия очереди для ${stripExt(t.name)}`}
                    aria-expanded={!!trackMenu && trackMenu.track?.id === t.id}
                    style={{
                      flexShrink: 0,
                      width: '32px',
                      height: '32px',
                      borderRadius: '8px',
                      border: '1px solid var(--line, #333)',
                      background: trackMenu?.track?.id === t.id ? 'var(--accent-muted)' : 'transparent',
                      color: trackMenu?.track?.id === t.id ? 'var(--accent)' : 'var(--dim)',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      cursor: 'pointer',
                    }}
                  >
                    <MoreVertical size={15} />
                  </button>
                </ClickableRow>
              )
            })}
        </div>

        {!isLoading && tracks.length > 0 && (
          <div className="tmusic-album-foot">
            <ListMusic size={14} />
            {tracks.length} {tracks.length === 1 ? 'трек' : 'треков'} • тап — играть • ★ — любимое • + — в плейлист • ⋮ — в очередь
          </div>
        )}

        {trackMenu && (
          <div
            className="tm-track-menu"
            style={{
              position: 'fixed',
              left: trackMenu.x,
              top: trackMenu.flip ? undefined : trackMenu.y,
              bottom: trackMenu.flip ? `calc(100dvh - ${trackMenu.y}px)` : undefined,
            }}
            onClick={(e) => e.stopPropagation()}
            role="menu"
          >
            <button
              type="button"
              className="tm-track-menu-item"
              onClick={() => playNextWithToast(trackMenu.track)}
            >
              <ListStart size={14} /> Играть следующим
            </button>
            <button
              type="button"
              className="tm-track-menu-item"
              onClick={() => addToQueueWithToast(trackMenu.track)}
            >
              <ListEnd size={14} /> Добавить в очередь
            </button>
            {isAdmin && !album?.is_cached_album && (
              <>
                <button
                  type="button"
                  className="tm-track-menu-item"
                  onClick={() => {
                    setEditingTrack({ id: trackMenu.track.id, draft: trackMenu.track.name })
                    setTrackEditError(null)
                    setTrackMenu(null)
                  }}
                >
                  <Pencil size={14} /> Переименовать трек
                </button>
                <button
                  type="button"
                  className="tm-track-menu-item"
                  onClick={() => {
                    onOpenCoverPicker({
                      type: 'track',
                      id: trackMenu.track.id,
                      title: stripExt(trackMenu.track.name),
                      hasCustom: !!trackMenu.track.custom_image_url,
                    })
                    setTrackMenu(null)
                  }}
                >
                  <ImageIcon size={14} /> Обложка трека
                </button>
              </>
            )}
            {cachedTrackIds.has(Number(trackMenu.track.id)) ? (
              <button
                type="button"
                className="tm-track-menu-item"
                onClick={() => {
                  removeTrack(trackMenu.track.id)
                  setTrackMenu(null)
                }}
                style={{ color: 'var(--danger)' }}
              >
                <Trash2 size={14} /> Удалить из кэша
              </button>
            ) : (
              <button
                type="button"
                className="tm-track-menu-item"
                onClick={() => {
                  cacheTrack(trackMenu.track)
                  setTrackMenu(null)
                }}
              >
                <HardDrive size={14} /> Сохранить в кэш
              </button>
            )}
          </div>
        )}

        {playlistTarget && (
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              position: 'absolute',
              inset: 0,
              background: 'rgba(0,0,0,0.55)',
              borderRadius: 'inherit',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              padding: '16px',
              zIndex: 20,
            }}
          >
            <div
              style={{
                width: 'min(360px, 100%)',
                background: 'var(--bg-card, #1c1917)',
                border: '1px solid var(--line-bright, #444)',
                borderRadius: '14px',
                padding: '16px',
                maxHeight: '70%',
                overflow: 'auto',
              }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '10px', gap: '8px' }}>
                <div style={{ fontWeight: 800, fontSize: '14px', color: 'var(--ink)' }}>
                  В плейлист
                </div>
                <button
                  onClick={() => setPlaylistTarget(null)}
                  style={{ background: 'none', border: 'none', color: 'var(--dim)', cursor: 'pointer' }}
                  aria-label="Закрыть"
                >
                  <X size={16} />
                </button>
              </div>
              <div style={{ fontSize: '12px', color: 'var(--dim)', marginBottom: '12px' }}>
                {stripExt(playlistTarget.name)}
              </div>

              {playlistError && <div role="alert" style={{ color: 'var(--danger)', marginBottom: 10 }}>
                {playlistError}
                <button type="button" onClick={() => openPlaylistPicker(playlistTarget)} disabled={playlistBusy || playlistsLoading}>Обновить список</button>
              </div>}
              {!isAuthenticated ? (
                <div style={{ fontSize: '13px', color: 'var(--dim)' }}>Войдите, чтобы сохранять плейлисты.</div>
              ) : playlistsLoading ? (
                <div style={{ fontSize: '13px', color: 'var(--dim)' }}>Загрузка...</div>
              ) : (
                <>
                  {(playlists || []).map((pl) => (
                    <button
                      key={pl.id}
                      onClick={() => addToPlaylist(pl)}
                      disabled={playlistBusy}
                      style={{
                        width: '100%',
                        textAlign: 'left',
                        marginBottom: '6px',
                        padding: '10px 12px',
                        borderRadius: '8px',
                        border: '1px solid var(--line, #333)',
                        background: addedFlash === pl.id ? 'var(--accent)' : 'transparent',
                        color: addedFlash === pl.id ? 'var(--on-accent)' : 'var(--ink)',
                        cursor: 'pointer',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        gap: '8px',
                        fontSize: '13px',
                        fontWeight: 600,
                      }}
                    >
                      <span>{pl.name}</span>
                      {addedFlash === pl.id ? <Check size={14} /> : (
                        <span style={{ fontSize: '11px', opacity: 0.7 }}>{pl.track_count || 0}</span>
                      )}
                    </button>
                  ))}
                  <div style={{ display: 'flex', gap: '8px', marginTop: '10px' }}>
                    <input
                      value={newPlName}
                      onChange={(e) => setNewPlName(e.target.value)}
                      placeholder="Новый плейлист"
                      style={{
                        flex: 1,
                        background: 'var(--bg)',
                        border: '1px solid var(--line, #333)',
                        color: 'var(--ink)',
                        borderRadius: '8px',
                        padding: '8px 10px',
                        fontSize: '13px',
                      }}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') createAndAdd()
                      }}
                    />
                    <button
                      onClick={createAndAdd}
                      disabled={!newPlName.trim() || playlistBusy || createUncertain}
                      style={{
                        background: newPlName.trim() ? 'var(--accent)' : 'var(--line-bright)',
                        color: 'var(--on-accent)',
                        border: 'none',
                        borderRadius: '8px',
                        padding: '8px 12px',
                        fontWeight: 800,
                        fontSize: '12px',
                        cursor: newPlName.trim() ? 'pointer' : 'default',
                      }}
                    >
                      Создать
                    </button>
                  </div>
                </>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
